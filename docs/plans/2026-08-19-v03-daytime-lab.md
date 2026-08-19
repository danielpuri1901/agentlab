# v0.3 Daytime Lab Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The lab talks to Daniel on Telegram the moment things happen, and Daniel steers it with taps: scheduled proposer runs file cited proposals, tap-approved registered work auto-submits to the existing fabric, and finished experiments ping his phone with the verdict and a chart.

**Architecture:** Five small Python modules (`notify`, `proposals`, `charts`, `sources`, `proposer`) extend the existing worker; one self-contained Lambda behind a function URL receives Telegram taps; EventBridge Scheduler fires the proposer task on the existing Fargate pattern.
No new database: proposals and pending pings live in the existing `agentlab-state` DynamoDB table.

**Tech Stack:** Python 3.12, uv, typer, boto3, httpx, matplotlib, litellm (Bedrock), moto for tests, Terraform (aws ~> 6.0, archive provider), Telegram Bot API.

**Spec:** `docs/specs/2026-08-18-v03-daytime-lab-design.md` (binding authority; the deferred-rungs section is roadmap, not scope).

## Global Constraints

- Every user-facing message follows STE: headline first, short sentences, plain words, and NEVER an em dash (use a plain dash).
- Only Telegram user id 6309668956 may decide a proposal; a tap from any other id is logged and ignored, with no state change.
- Secrets (bot token, webhook secret) live ONLY in SSM Parameter Store as SecureString; never in the repo, the image, plan/spec files, or Terraform state.
- Quiet hours are 23:00-08:00 Europe/Amsterdam: pings queue in DynamoDB and flush at the 08:00 scheduled run.
- Max 3 proposals filed per day; auto-submit is allowed ONLY for proposals with a prepared, validated `submit_body` (registered compaction re-runs with `tasks<=20`, `repeats<=5`, model starting with `bedrock/`).
- Tests are fully offline: moto for AWS, monkeypatched HTTP/LLM calls; no test touches the network.
- Terraform: aws provider ~> 6.0; CloudWatch log groups get explicit 30-day retention; IAM trust policies carry `aws:SourceAccount`; every `iam:PassRole` grant carries `iam:PassedToService = ecs-tasks.amazonaws.com`; no wildcards on resources where an ARN is knowable.
- AWS clients are passed into functions as parameters (the `worker.py` convention) so moto tests hit them directly.
- Run `uv run pytest` and `uv run ruff check` before every commit; never pipe pytest through `tail` without `set -o pipefail`.
- Commit messages never add an agent co-author.

## File Structure

- Create: `src/agentlab/notify.py` (Telegram sends + quiet-hours queue), `src/agentlab/proposals.py` (ledger), `src/agentlab/charts.py` (verdict PNG), `src/agentlab/sources.py` (GitHub/arXiv/HN fetchers), `src/agentlab/proposer.py` (propose flow), `infra/lambda/approvals_webhook.py` (webhook), `infra/telegram.tf`, `infra/lambda.tf`, `infra/scheduler.tf`, `scripts/register_telegram_webhook.py`.
- Modify: `src/agentlab/worker.py` (three new commands + finalize ping), `infra/ecs.tf` (proposer task definition), `infra/iam.tf` (proposer task role, execution-role log grant), `infra/outputs.tf`, `infra/variables.tf`, `infra/main.tf` (archive provider), `infra/.gitignore`, `pyproject.toml` (httpx, matplotlib).
- Tests: `tests/test_notify.py`, `tests/test_proposals.py`, `tests/test_charts.py`, `tests/test_sources.py`, `tests/test_proposer.py`, `tests/test_approvals_webhook.py`, additions to `tests/test_worker.py`.

---

### Task 1: `agentlab/notify.py` - the Telegram channel with quiet hours

**Files:**
- Create: `src/agentlab/notify.py`
- Create: `tests/test_notify.py`
- Modify: `pyproject.toml` (add httpx as an explicit dependency)

**Interfaces:**
- Consumes: the existing `agentlab-state` table shape (PK `experiment_id` S, SK `sk` S).
- Produces: `notify(table, ssm_client, text, buttons=None, photo_png=None) -> str` returning `"sent"` or `"queued"`; `flush_pending(table, ssm_client) -> int`; `load_config(ssm_client) -> TelegramConfig`; `now_amsterdam()` monkeypatchable clock; constants `TOKEN_PARAM = "/agentlab/telegram/bot-token"`, `CHAT_ID_PARAM = "/agentlab/telegram/chat-id"`, `PENDING_PARTITION = "pending_ping"`.
- Buttons are `list[list[tuple[str, str]]]`: rows of `(button text, callback_data)` pairs. After a DynamoDB round trip tuples come back as lists; the serializer unpacks pairs either way.

- [ ] **Step 1: Add httpx as an explicit dependency**

Run: `uv add httpx`
(httpx is already resolved transitively; this only promotes it to a direct dependency.)

- [ ] **Step 2: Write the failing tests**

Create `tests/test_notify.py`:

```python
"""Telegram notify channel, fully offline: moto stands in for SSM/DynamoDB,
a monkeypatched httpx.post captures every Telegram call."""

import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from agentlab import notify as notify_mod
from agentlab.notify import (
    CHAT_ID_PARAM,
    PENDING_PARTITION,
    TOKEN_PARAM,
    flush_pending,
    in_quiet_hours,
    notify,
)

TABLE = "agentlab-state-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def fabric():
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        table = dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "experiment_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "experiment_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name=TOKEN_PARAM, Value="test-token", Type="SecureString")
        ssm.put_parameter(Name=CHAT_ID_PARAM, Value="6309668956", Type="String")
        yield table, ssm


@pytest.fixture
def telegram_calls(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(notify_mod.httpx, "post", fake_post)
    return calls


def _daytime(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )


def _quiet(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 2, 0, tzinfo=AMS)
    )


def test_quiet_hours_boundaries():
    assert not in_quiet_hours(datetime(2026, 8, 19, 22, 59, tzinfo=AMS))
    assert in_quiet_hours(datetime(2026, 8, 19, 23, 0, tzinfo=AMS))
    assert in_quiet_hours(datetime(2026, 8, 19, 7, 59, tzinfo=AMS))
    assert not in_quiet_hours(datetime(2026, 8, 19, 8, 0, tzinfo=AMS))


def test_notify_daytime_sends_with_buttons(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _daytime(monkeypatch)
    status = notify(
        table, ssm, "hello", buttons=[[("APPROVE 1", "prop:p1:approve")]]
    )
    assert status == "sent"
    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"]["chat_id"] == "6309668956"
    assert kwargs["json"]["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "APPROVE 1",
        "callback_data": "prop:p1:approve",
    }


def test_notify_quiet_queues_instead_of_sending(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    status = notify(table, ssm, "night finding", photo_png=b"pngbytes")
    assert status == "queued"
    assert telegram_calls == []
    items = table.scan()["Items"]
    pending = [i for i in items if i["experiment_id"] == PENDING_PARTITION]
    assert len(pending) == 1
    payload = json.loads(pending[0]["payload"])
    assert payload["text"] == "night finding"
    assert base64.b64decode(payload["photo_b64"]) == b"pngbytes"


def test_flush_sends_in_order_and_deletes(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "first")
    notify(table, ssm, "second", buttons=[[("A", "prop:x:approve")]])
    _daytime(monkeypatch)
    sent = flush_pending(table, ssm)
    assert sent == 2
    texts = [kwargs["json"]["text"] for _, kwargs in telegram_calls]
    assert texts == ["first", "second"]
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert remaining == []


def test_flush_photo_roundtrip_uses_sendphoto(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "with chart", photo_png=b"img")
    sent = flush_pending(table, ssm)
    assert sent == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendPhoto")
    assert kwargs["files"]["photo"][1] == b"img"


def test_flush_stops_at_first_failure_and_keeps_remainder(fabric, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "one")
    notify(table, ssm, "two")

    def exploding_post(url, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify_mod.httpx, "post", exploding_post)
    with pytest.raises(RuntimeError):
        flush_pending(table, ssm)
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert len(remaining) == 2


def test_oversized_photo_dropped_from_queue(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "big chart", photo_png=b"x" * 300_000)
    items = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    payload = json.loads(items[0]["payload"])
    assert "photo_b64" not in payload
    assert "chart omitted" in payload["text"]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_notify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentlab.notify'`

- [ ] **Step 4: Write the implementation**

Create `src/agentlab/notify.py`:

```python
"""Telegram notification channel: event-driven pings to Daniel's phone.

Every message follows the STE rule: headline first, short sentences, no em
dash. Quiet hours (23:00-08:00 Europe/Amsterdam) queue pings into the
`pending_ping` partition of the state table instead of sending;
`flush_pending` delivers them oldest-first at the next scheduled run (the
08:00 flush schedule, or the start of any propose run).

AWS clients (SSM, the DynamoDB table) are passed in as parameters, matching
the worker.py convention, so tests run against moto without patching boto3.
"""

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from boto3.dynamodb.conditions import Key

TOKEN_PARAM = "/agentlab/telegram/bot-token"
CHAT_ID_PARAM = "/agentlab/telegram/chat-id"
QUIET_START_HOUR = 23
QUIET_END_HOUR = 8
PENDING_PARTITION = "pending_ping"
# DynamoDB items cap at 400KB; a queued photo rides inside the item as
# base64 (4/3 inflation), so anything past this raw size is dropped from
# the queue with a note rather than risking a rejected put.
MAX_QUEUED_PHOTO_BYTES = 250_000
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str


def now_amsterdam() -> datetime:
    """Module-level clock indirection; tests monkeypatch this."""
    return datetime.now(AMSTERDAM)


def in_quiet_hours(dt: datetime) -> bool:
    return dt.hour >= QUIET_START_HOUR or dt.hour < QUIET_END_HOUR


def load_config(ssm_client) -> TelegramConfig:
    token = ssm_client.get_parameter(Name=TOKEN_PARAM, WithDecryption=True)[
        "Parameter"
    ]["Value"]
    chat_id = ssm_client.get_parameter(Name=CHAT_ID_PARAM)["Parameter"]["Value"]
    return TelegramConfig(token=token, chat_id=chat_id)


def _inline_keyboard(buttons) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in buttons
        ]
    }


def send_message(config: TelegramConfig, text: str, buttons=None) -> None:
    payload = {"chat_id": config.chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = _inline_keyboard(buttons)
    response = httpx.post(
        f"https://api.telegram.org/bot{config.token}/sendMessage",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def send_photo(config: TelegramConfig, caption: str, photo_png: bytes) -> None:
    # Telegram caps captions at 1024 chars; callers keep photo texts short
    # (the finalize headline is well under), so truncation here is a guard,
    # not an expected path.
    response = httpx.post(
        f"https://api.telegram.org/bot{config.token}/sendPhoto",
        data={"chat_id": config.chat_id, "caption": caption[:1024]},
        files={"photo": ("chart.png", photo_png, "image/png")},
        timeout=30,
    )
    response.raise_for_status()


def _deliver(config: TelegramConfig, text: str, buttons, photo_png) -> None:
    if photo_png is not None:
        send_photo(config, text, photo_png)
        if buttons:
            send_message(config, text, buttons)
    else:
        send_message(config, text, buttons)


def queue_ping(table, text: str, buttons, photo_png) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sk = f"ping#{ts}#{secrets.token_hex(2)}"
    payload: dict = {"text": text, "buttons": buttons}
    if photo_png is not None:
        if len(photo_png) <= MAX_QUEUED_PHOTO_BYTES:
            payload["photo_b64"] = base64.b64encode(photo_png).decode("ascii")
        else:
            payload["text"] = text + "\n(chart omitted: too large to queue)"
    table.put_item(
        Item={
            "experiment_id": PENDING_PARTITION,
            "sk": sk,
            "payload": json.dumps(payload),
        }
    )
    return sk


def notify(table, ssm_client, text: str, buttons=None, photo_png=None) -> str:
    """Send now, or queue during quiet hours. Returns "sent" or "queued"."""
    if in_quiet_hours(now_amsterdam()):
        queue_ping(table, text, buttons, photo_png)
        return "queued"
    config = load_config(ssm_client)
    _deliver(config, text, buttons, photo_png)
    return "sent"


def flush_pending(table, ssm_client) -> int:
    """Deliver queued pings oldest-first.

    A send failure propagates: the failed ping and everything after it stay
    queued for the next flush, while already-delivered items are gone (each
    is deleted immediately after its successful send).
    """
    items = table.query(
        KeyConditionExpression=Key("experiment_id").eq(PENDING_PARTITION)
    )["Items"]
    if not items:
        return 0
    config = load_config(ssm_client)
    sent = 0
    for item in sorted(items, key=lambda i: i["sk"]):
        payload = json.loads(item["payload"])
        photo = (
            base64.b64decode(payload["photo_b64"])
            if "photo_b64" in payload
            else None
        )
        _deliver(config, payload["text"], payload.get("buttons"), photo)
        table.delete_item(
            Key={"experiment_id": PENDING_PARTITION, "sk": item["sk"]}
        )
        sent += 1
    return sent
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_notify.py -v && uv run ruff check src/agentlab/notify.py tests/test_notify.py`
Expected: all PASS, ruff clean.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src/agentlab/notify.py tests/test_notify.py
git commit -m "feat: telegram notify channel with quiet-hours queueing"
```

---

### Task 2: `agentlab/proposals.py` - the proposals ledger

**Files:**
- Create: `src/agentlab/proposals.py`
- Create: `tests/test_proposals.py`

**Interfaces:**
- Consumes: the existing state-table shape.
- Produces: `generate_proposal_id() -> str` (format `prop-<YYYYMMDDTHHMMSSZ>-<4hex>`, with monkeypatchable module-level `now()`/`random_hex()` like `cloud.py`); `file_proposal(table, pid, title, headline, citation, distance, kind, submit_body=None)`; `get_proposal(table, pid) -> dict | None`; `set_verdict(table, pid, verdict, source)` raising `AlreadyDecided`; `list_recent(table, limit=20) -> list[dict]` newest-first; `count_created_today(table) -> int`; constants `PROPOSAL_SK = "proposal"`, `DAILY_CAP = 3`.
- Item shape (Task 5 writes it, Task 6's Lambda reads it verbatim): `{"experiment_id": "proposal#<pid>", "sk": "proposal", "pid", "status", "title", "headline", "citation", "distance", "kind", "created_ts", "submit_body"?}` where `status` moves `PROPOSED -> APPROVED | REJECTED` exactly once and `submit_body` is a JSON string of the exact `cloud.build_message_body` dict.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_proposals.py` (reuse the `aws_credentials`/table fixture pattern from `tests/test_notify.py`, table name `agentlab-state-test`):

```python
import pytest

from agentlab import proposals as proposals_mod
from agentlab.proposals import (
    AlreadyDecided,
    count_created_today,
    file_proposal,
    generate_proposal_id,
    get_proposal,
    list_recent,
    set_verdict,
)

# ... aws_credentials + fabric fixtures identical in shape to test_notify.py ...


def test_generate_proposal_id_format(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 9, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(proposals_mod, "random_hex", lambda: "ab12")
    assert generate_proposal_id() == "prop-20260819T093000Z-ab12"


def test_file_and_get_roundtrip(fabric):
    table, _ = fabric
    file_proposal(
        table, "prop-1", "Title", "Headline.", "https://x", "differs from 001",
        "new_hypothesis",
    )
    item = get_proposal(table, "prop-1")
    assert item["status"] == "PROPOSED"
    assert item["experiment_id"] == "proposal#prop-1"
    assert "submit_body" not in item


def test_submit_body_stored_as_json_string(fabric):
    table, _ = fabric
    file_proposal(
        table, "prop-2", "Rerun", "H.", "https://x", "d", "registered_rerun",
        submit_body={"experiment_id": "exp-1", "model": "bedrock/eu.amazon.nova-lite-v1:0"},
    )
    import json

    body = json.loads(get_proposal(table, "prop-2")["submit_body"])
    assert body["experiment_id"] == "exp-1"


def test_set_verdict_once_then_already_decided(fabric):
    table, _ = fabric
    file_proposal(table, "prop-3", "T", "H", "https://x", "d", "new_hypothesis")
    set_verdict(table, "prop-3", "APPROVED", "telegram_tap")
    item = get_proposal(table, "prop-3")
    assert item["status"] == "APPROVED"
    assert item["verdict_source"] == "telegram_tap"
    with pytest.raises(AlreadyDecided):
        set_verdict(table, "prop-3", "REJECTED", "telegram_tap")
    assert get_proposal(table, "prop-3")["status"] == "APPROVED"


def test_list_recent_newest_first_and_count_today(fabric, monkeypatch):
    table, _ = fabric
    # file three proposals with distinct created_ts by monkeypatching now()
    ...  # freeze proposals_mod.now to three increasing datetimes, file three items
    recent = list_recent(table, limit=2)
    assert len(recent) == 2
    assert recent[0]["created_ts"] > recent[1]["created_ts"]
    assert count_created_today(table) == 3
```

(Write the elided test bodies out fully: monkeypatch `proposals_mod.now` to `datetime(2026, 8, 19, h, 0, tzinfo=UTC)` for `h in (9, 10, 11)` around three `file_proposal` calls with pids `prop-a`/`prop-b`/`prop-c`; `count_created_today` must also see a frozen `now()` returning 2026-08-19.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_proposals.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentlab.proposals'`

- [ ] **Step 3: Write the implementation**

Create `src/agentlab/proposals.py`:

```python
"""Proposals ledger on the existing agentlab-state table.

One item per proposal: PK `proposal#<pid>`, SK the literal "proposal"
(the spec's "PK proposal#<id>" slot). Status moves PROPOSED -> APPROVED |
REJECTED exactly once via a conditional update; a second tap raises
AlreadyDecided instead of overwriting. Listing and daily counts use a table
Scan, which is fine at this volume (a handful of items per day); revisit if
proposals ever reach thousands.

The approvals Lambda (infra/lambda/approvals_webhook.py) deliberately does
NOT import this module (its deployment package is a single file); it
re-implements set_verdict's exact update. If the item shape or the
conditional expression here changes, change the Lambda in the same commit.
"""

import json
import secrets
from datetime import UTC, datetime

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

PROPOSAL_SK = "proposal"
DAILY_CAP = 3


class AlreadyDecided(Exception):
    """The proposal already has a verdict; a second tap must not overwrite it."""


def now() -> datetime:
    """Module-level clock indirection; tests monkeypatch this."""
    return datetime.now(UTC)


def random_hex() -> str:
    """Module-level randomness indirection; tests monkeypatch this."""
    return secrets.token_hex(2)


def generate_proposal_id() -> str:
    return f"prop-{now().strftime('%Y%m%dT%H%M%SZ')}-{random_hex()}"


def file_proposal(
    table,
    pid: str,
    title: str,
    headline: str,
    citation: str,
    distance: str,
    kind: str,
    submit_body: dict | None = None,
) -> None:
    item = {
        "experiment_id": f"proposal#{pid}",
        "sk": PROPOSAL_SK,
        "pid": pid,
        "status": "PROPOSED",
        "title": title,
        "headline": headline,
        "citation": citation,
        "distance": distance,
        "kind": kind,
        "created_ts": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    if submit_body is not None:
        item["submit_body"] = json.dumps(submit_body)
    table.put_item(Item=item)


def get_proposal(table, pid: str) -> dict | None:
    response = table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": PROPOSAL_SK}
    )
    return response.get("Item")


def set_verdict(table, pid: str, verdict: str, source: str) -> None:
    try:
        table.update_item(
            Key={"experiment_id": f"proposal#{pid}", "sk": PROPOSAL_SK},
            UpdateExpression="SET #s = :v, verdict_ts = :ts, verdict_source = :src",
            ConditionExpression="#s = :proposed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":v": verdict,
                ":proposed": "PROPOSED",
                ":ts": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ":src": source,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AlreadyDecided(pid) from exc
        raise


def list_recent(table, limit: int = 20) -> list[dict]:
    items = table.scan(FilterExpression=Attr("sk").eq(PROPOSAL_SK))["Items"]
    return sorted(items, key=lambda i: i["created_ts"], reverse=True)[:limit]


def count_created_today(table) -> int:
    today = now().strftime("%Y-%m-%d")
    items = table.scan(
        FilterExpression=Attr("sk").eq(PROPOSAL_SK)
        & Attr("created_ts").begins_with(today)
    )["Items"]
    return len(items)
```

- [ ] **Step 4: Run tests, lint, commit**

Run: `uv run pytest tests/test_proposals.py -v && uv run ruff check src/agentlab/proposals.py tests/test_proposals.py`

```bash
git add src/agentlab/proposals.py tests/test_proposals.py
git commit -m "feat: proposals ledger with one-shot verdicts"
```

---

### Task 3: verdict chart + finalize ping + flush CLI

**Files:**
- Create: `src/agentlab/charts.py`, `tests/test_charts.py`
- Modify: `src/agentlab/worker.py` (finalize ping + `flush-pings` command), `tests/test_worker.py` (new tests), `pyproject.toml` (matplotlib)

**Interfaces:**
- Consumes: `notify.notify`, `notify.flush_pending` (Task 1); `PairedResult.mean_delta/.ci_low/.ci_high` (existing `stats.py`); `mean_score` (existing `results.py`).
- Produces: `charts.verdict_chart_png(baseline_label, candidate_label, baseline_mean, candidate_mean, mean_delta, ci_low, ci_high) -> bytes`; CLI `agentlab worker flush-pings` (env: `STATE_TABLE`); the finalize command now sends a ping and records `PING_FAILED` on notify errors, never failing the experiment.

- [ ] **Step 1: Add matplotlib**

Run: `uv add matplotlib`

- [ ] **Step 2: Write failing chart test**

Create `tests/test_charts.py`:

```python
from agentlab.charts import verdict_chart_png


def test_chart_is_reasonable_png():
    png = verdict_chart_png("truncate", "codes_first", 0.61, 0.88, 0.27, 0.21, 0.33)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert 1_000 < len(png) < 250_000
```

Run: `uv run pytest tests/test_charts.py -v` - expected FAIL (module missing).

- [ ] **Step 3: Implement `src/agentlab/charts.py`**

```python
"""Verdict chart: the one PNG attached to a finalize ping."""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402 - backend must be set before pyplot


def verdict_chart_png(
    baseline_label: str,
    candidate_label: str,
    baseline_mean: float,
    candidate_mean: float,
    mean_delta: float,
    ci_low: float,
    ci_high: float,
) -> bytes:
    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=110)
    bars = ax.bar(
        [baseline_label, candidate_label],
        [baseline_mean, candidate_mean],
        color=["#8a8a8a", "#2f6fd6"],
    )
    ax.bar_label(bars, fmt="%.3f")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("mean score")
    ax.set_title(
        f"delta {mean_delta:+.3f} (95% CI {ci_low:+.3f} to {ci_high:+.3f})",
        fontsize=10,
    )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
```

Run the chart test: PASS.

- [ ] **Step 4: Write failing worker tests**

Add to `tests/test_worker.py` (the `moto_fabric` fixture needs an SSM client with both telegram parameters added, mirroring `test_notify.py`; monkeypatch `agentlab.notify.now_amsterdam` to daytime and `agentlab.notify.httpx.post` to capture):

```python
def test_flush_pings_command(moto_fabric_with_ssm, telegram_calls, monkeypatch):
    # queue one ping directly via notify with a quiet clock, then:
    result = runner.invoke(app, ["worker", "flush-pings"], env={"STATE_TABLE": TABLE})
    assert result.exit_code == 0
    assert "flushed 1" in result.output
    assert len(telegram_calls) == 1


def test_finalize_sends_ping_with_chart(...):
    # run the existing finalize test path (mockllm logs already exercised in
    # this file), then assert the LAST telegram call is a sendPhoto whose
    # caption contains "Verdict:" and the experiment id.
    ...


def test_finalize_ping_failure_records_transition_not_crash(...):
    # monkeypatch notify to raise; finalize must still exit 0 and the table
    # must contain a PING_FAILED event item.
    ...
```

Write these three tests out fully following the file's existing finalize-test setup (it already builds mockllm EvalLogs and invokes `worker finalize` via CliRunner with the env contract; extend that env with nothing new - the ping reads SSM directly).
Run: expected FAIL (no ping sent, no flush command).

- [ ] **Step 5: Implement worker changes**

In `src/agentlab/worker.py`:

1. Add imports: `from agentlab.charts import verdict_chart_png`, `from agentlab.notify import flush_pending, notify`.
2. Add the flush command:

```python
@worker_app.command("flush-pings")
def flush_pings_command() -> None:
    """Deliver pings queued during quiet hours (the 08:00 schedule's job)."""
    state_table = _require_env("STATE_TABLE")
    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    count = flush_pending(table, ssm_client)
    typer.echo(f"flushed {count} pending pings")
```

3. In `finalize_command`, immediately after the `transition(..., "FINALIZED", ...)` line, add:

```python
    try:
        ssm_client = boto3.client("ssm")
        chart = verdict_chart_png(
            baseline_style,
            candidate_style,
            mean_score(baseline.scores),
            mean_score(candidate.scores),
            result.mean_delta,
            result.ci_low,
            result.ci_high,
        )
        text = (
            f"Experiment {experiment_id} is done.\n"
            f"Verdict: {verdict_str}.\n"
            f"{candidate_style} scores {mean_score(candidate.scores):.3f}. "
            f"{baseline_style} scores {mean_score(baseline.scores):.3f}.\n"
            f"The 95% CI of the difference is {result.ci_low:+.3f} to {result.ci_high:+.3f}.\n"
            f"Report: s3://{results_bucket}/experiments/{experiment_id}/report.md"
        )
        ping_status = notify(table, ssm_client, text, photo_png=chart)
        typer.echo(f"finalize ping: {ping_status}")
    except Exception as exc:  # noqa: BLE001 - a ping failure must never fail a finished experiment
        transition(table, experiment_id, "PING_FAILED", None, str(exc))
```

Note: `baseline`, `candidate`, and `result` are currently local to the `with tempfile...` block; the ping code must run where they are still in scope (inside the block, after `upload_report`) OR hoist the needed floats out - put the `transition(FINALIZED)` + ping inside the block's tail; the existing behavior (FINALIZED written after upload) is preserved either way.

- [ ] **Step 6: Run the full suite, lint, commit**

Run: `set -o pipefail && uv run pytest && uv run ruff check`

```bash
git add pyproject.toml uv.lock src/agentlab/charts.py src/agentlab/worker.py tests/
git commit -m "feat: finalize ping with verdict chart and flush-pings command"
```

---

### Task 4: `agentlab/sources.py` - fresh external sources

**Files:**
- Create: `src/agentlab/sources.py`, `tests/test_sources.py`

**Interfaces:**
- Produces: `gather(client=None) -> list[dict]` where every dict has keys `source` (`"github" | "arxiv" | "hn"`), `title`, `url`, plus source-specific extras; `TRACKED_REPOS`, `KEYWORDS` constants; individual `fetch_github_releases(client, repos)`, `fetch_arxiv(client, max_results=25)`, `fetch_hn_front(client)`.
- Every fetcher swallows non-200s / malformed payloads and returns what it has: one dead source must never sink a proposer run.

- [ ] **Step 1: Verify the tracked repo slugs are real**

Run (no auth needed, 5 calls):

```bash
for r in UKGovernmentBEIS/inspect_ai langchain-ai/langgraph anthropics/claude-agent-sdk-python strands-agents/sdk-python deepseek-ai/DeepSeek-Coder; do
  curl -s -o /dev/null -w "%{http_code} $r\n" "https://api.github.com/repos/$r"
done
```

Any 404: find the correct slug (the DeepSeek harness repo Daniel tracked in Tournament 001 - check `docs/tournaments/001-entrants/` for its cited URL and use THAT repo) and fix `TRACKED_REPOS` before writing tests.
The list that ships is whatever this step verified, with the tournament's cited harness repo required and the other four expected as-is.

- [ ] **Step 2: Write failing tests**

Create `tests/test_sources.py` with a `FakeClient` whose `get(url, **kw)` returns canned `FakeResponse(status_code, json_data=None, text="")` objects keyed by URL substring:

```python
def test_github_releases_normalized(): ...
    # FakeClient returns one repo 200 with two releases, one repo 404;
    # assert 2 dicts, each with source="github", the repo slug, title, url,
    # and the 404 repo silently skipped.


def test_arxiv_filters_by_keyword(): ...
    # canned Atom XML with two entries, one containing "agent evaluation"
    # in its title, one about astrophysics; assert only the first survives
    # and whitespace in the title is collapsed.


def test_hn_front_page_filter(): ...
    # canned Algolia JSON with hits ["New agent harness released",
    # "Show HN: my cat"]; assert only the first survives, with url falling
    # back to the news.ycombinator.com item link when "url" is null.


def test_gather_survives_total_network_failure(): ...
    # FakeClient raises on every call; gather returns [] and does not raise.
```

Write the canned XML/JSON payloads inline in the test file, full literals, no fixtures on disk.
Run: expected FAIL (module missing).

- [ ] **Step 3: Implement `src/agentlab/sources.py`**

```python
"""Fresh external sources for the proposer: GitHub releases, arXiv, HN.

Each fetcher normalizes to small dicts and fails soft: a non-200, a
malformed payload, or a network error yields fewer sources, never an
exception out of gather(). The proposer treats an empty list as "no fresh
sources today" and says so instead of inventing work (anti-collapse rule:
fresh-external-source anchoring).
"""

import xml.etree.ElementTree as ET

import httpx

TRACKED_REPOS = [
    # verified in Task 4 Step 1; the DeepSeek harness slug comes from the
    # Tournament 001 entrant citation
    "UKGovernmentBEIS/inspect_ai",
    "langchain-ai/langgraph",
    "anthropics/claude-agent-sdk-python",
    "strands-agents/sdk-python",
    "<the-verified-deepseek-harness-slug>",
]
KEYWORDS = (
    "agent",
    "eval",
    "harness",
    "compaction",
    "context",
    "memory",
    "benchmark",
    "tool use",
    "llm",
)


def fetch_github_releases(client, repos=None) -> list[dict]:
    out = []
    for repo in repos if repos is not None else TRACKED_REPOS:
        try:
            response = client.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": 3},
                headers={"Accept": "application/vnd.github+json"},
                timeout=30,
            )
            if response.status_code != 200:
                continue
            releases = response.json()
        except Exception:  # noqa: BLE001 - one dead repo must not sink the run
            continue
        for release in releases:
            out.append(
                {
                    "source": "github",
                    "repo": repo,
                    "title": release.get("name") or release.get("tag_name") or "",
                    "url": release.get("html_url") or "",
                    "published": release.get("published_at") or "",
                    "notes": (release.get("body") or "")[:500],
                }
            )
    return out


def fetch_arxiv(client, max_results: int = 25) -> list[dict]:
    try:
        response = client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": "cat:cs.CL OR cat:cs.AI",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
            timeout=30,
        )
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.text)
    except Exception:  # noqa: BLE001
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for entry in root.findall("a:entry", ns):
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", "", ns) or "").split())
        url = entry.findtext("a:id", "", ns) or ""
        if any(k in f"{title} {summary}".lower() for k in KEYWORDS):
            entries.append(
                {
                    "source": "arxiv",
                    "title": title,
                    "url": url,
                    "summary": summary[:400],
                }
            )
    return entries


def fetch_hn_front(client) -> list[dict]:
    try:
        response = client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": 30},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        hits = response.json().get("hits", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for hit in hits:
        title = hit.get("title") or ""
        if any(k in title.lower() for k in KEYWORDS):
            out.append(
                {
                    "source": "hn",
                    "title": title,
                    "url": hit.get("url")
                    or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "points": hit.get("points") or 0,
                }
            )
    return out


def gather(client=None) -> list[dict]:
    client = client or httpx.Client()
    return fetch_github_releases(client) + fetch_arxiv(client) + fetch_hn_front(client)
```

(Replace `<the-verified-deepseek-harness-slug>` with the Step 1 result - the plan cannot know it; the test file pins whatever was verified.)

- [ ] **Step 4: Run tests, lint, commit**

```bash
git add src/agentlab/sources.py tests/test_sources.py
git commit -m "feat: proposer source fetchers (github releases, arxiv, hn)"
```

---

### Task 5: `agentlab/proposer.py` + `agentlab worker propose`

**Files:**
- Create: `src/agentlab/proposer.py`, `tests/test_proposer.py`
- Modify: `src/agentlab/worker.py` (propose command), `tests/test_worker.py`

**Interfaces:**
- Consumes: `sources.gather` (Task 4), `proposals.*` (Task 2), `notify.notify`/`flush_pending` (Task 1), `cloud._MODEL_PATTERN`/`_valid_styles`/`build_message_body`/`generate_experiment_id` (existing).
- Produces: `run_propose(table, ssm_client, s3_client, bucket, model) -> int`; `parse_proposals(raw) -> list[dict]`; `_registered_submit_body(exp) -> dict | None`; `_complete(model, messages) -> str` (the ONLY litellm touchpoint, monkeypatched in every test); CLI `agentlab worker propose` (env: `STATE_TABLE`, `RESULTS_BUCKET`, optional `PROPOSER_MODEL`); `DEFAULT_PROPOSER_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"`.
- Callback data format (Task 6's Lambda parses it): `prop:<pid>:approve` / `prop:<pid>:reject` (fits Telegram's 64-byte limit: pid is 26 chars).

- [ ] **Step 1: Write failing tests**

Create `tests/test_proposer.py` (moto table + SSM fixture as before, plus a moto S3 bucket; monkeypatch `proposer._complete`, `proposer.gather`, and `agentlab.notify.now_amsterdam`/`httpx.post`):

```python
VALID_LLM_OUTPUT = """```json
[
  {"title": "Haiku vs codes_first rerun", "headline": "Test the champion on Haiku again.",
   "citation": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v0.4.0",
   "distance": "Repeats tournament 001 round 1 on a new inspect version.",
   "kind": "registered_rerun",
   "experiment": {"model": "bedrock/eu.amazon.nova-lite-v1:0", "tasks": 10, "repeats": 3,
                   "baseline_style": "truncate", "candidate_style": "codes_first"}},
  {"title": "Reflection on arithmetic", "headline": "New hypothesis needs your call.",
   "citation": "https://arxiv.org/abs/2601.00001", "distance": "No archive overlap.",
   "kind": "new_hypothesis"}
]
```"""


def test_parse_proposals_strips_fences_and_clips(): ...
    # parse VALID_LLM_OUTPUT -> 2 dicts; a 500-char title gets clipped to 80;
    # kind "weird" coerces to "new_hypothesis"; a non-dict entry is dropped;
    # garbage input ("not json") returns [].


def test_registered_submit_body_valid(): ...
    # the experiment dict above -> dict with experiment_id starting "exp-",
    # max_connections == 30, styles preserved.


def test_registered_submit_body_rejects(): ...
    # each of: model without bedrock/ prefix, unknown style, equal styles,
    # tasks=50, repeats=9, missing model -> None.


def test_run_propose_files_pings_and_uploads(fabric, ...): ...
    # gather -> two fake sources; _complete -> VALID_LLM_OUTPUT; daytime clock.
    # run_propose returns 2; two PROPOSED items in the table, the rerun one
    # with submit_body; one telegram sendMessage whose reply_markup has two
    # rows, callback_data "prop:<pid>:approve"; s3 has proposals/<pid>/proposal.json
    # for both.


def test_run_propose_respects_daily_cap(fabric, ...): ...
    # pre-file 3 proposals dated today -> run_propose returns 0, no LLM call
    # (monkeypatched _complete raises if called), no ping.


def test_run_propose_no_sources_says_so(fabric, ...): ...
    # gather -> []; run_propose returns 0; the single telegram message
    # contains "no fresh sources"; _complete not called.
```

Write every elided body fully.
Run: expected FAIL.

- [ ] **Step 2: Implement `src/agentlab/proposer.py`**

```python
"""The proposer: read fresh sources, draft cited proposals, ping Daniel.

Anti-collapse rules enforced here (docs/phase0-synthesis.md): every proposal
anchors to a fresh external source by URL; every proposal states its
distance from the recent archive (which includes rejected proposals, so
rejected ideas do not come back reworded); at most DAILY_CAP proposals per
day. Auto-submittable work is ONLY a registered compaction re-run whose
fields pass the same validation `agentlab cloud submit` applies, with hard
caps (tasks<=20, repeats<=5, model must be a bedrock/ id) bounding spend.
"""

import json
import os

from agentlab.cloud import (
    _MODEL_PATTERN,
    _valid_styles,
    build_message_body,
    generate_experiment_id,
)
from agentlab.notify import notify
from agentlab.proposals import (
    DAILY_CAP,
    count_created_today,
    file_proposal,
    generate_proposal_id,
    list_recent,
)
from agentlab.sources import gather

DEFAULT_PROPOSER_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_TITLE = 80
MAX_HEADLINE = 300

PROPOSER_SYSTEM = """You are the proposer for AgentLab, an experimentation \
lab that measures agent techniques with paired statistics. You read fresh \
sources and propose experiments. Rules: every proposal cites exactly one \
source URL from the list you are given. Every proposal states its distance \
from the archive in one sentence. Do not repropose anything the archive \
already rejected or completed. Kind "registered_rerun" means a compaction \
suite run and must include an experiment object with model, tasks, repeats, \
baseline_style, candidate_style. Any new idea is kind "new_hypothesis" and \
carries no experiment object. Write titles and headlines in short plain \
sentences. Output ONLY a JSON array of proposal objects with keys: title, \
headline, citation, distance, kind, and optionally experiment."""


def _complete(model: str, messages: list[dict]) -> str:
    """The only litellm touchpoint; tests monkeypatch this."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    response = litellm.completion(model=model, messages=messages, max_tokens=3000)
    return response.choices[0].message.content


def build_prompt(sources: list[dict], archive: list[dict], slots: int) -> str:
    source_lines = "\n".join(
        f"- [{s['source']}] {s['title']} :: {s['url']}" for s in sources
    )
    archive_lines = "\n".join(
        f"- [{p.get('status')}] {p.get('title')} :: {p.get('distance', '')}"
        for p in archive
    ) or "- (archive is empty)"
    return (
        f"Fresh sources today:\n{source_lines}\n\n"
        f"Recent archive (do not repeat these):\n{archive_lines}\n\n"
        f"Propose at most {slots} experiments as a JSON array."
    )


def parse_proposals(raw: str) -> list[dict]:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text.removeprefix("json").strip()
    start = text.find("[")
    if start == -1:
        return []
    try:
        parsed = json.loads(text[start:])
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    proposals = []
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        required = ("title", "headline", "citation", "distance", "kind")
        if not all(isinstance(entry.get(k), str) and entry[k] for k in required):
            continue
        entry["title"] = entry["title"][:MAX_TITLE]
        entry["headline"] = entry["headline"][:MAX_HEADLINE]
        if entry["kind"] not in ("registered_rerun", "new_hypothesis"):
            entry["kind"] = "new_hypothesis"
        proposals.append(entry)
    return proposals


def _registered_submit_body(exp: dict) -> dict | None:
    """Validate a proposed registered re-run; None means 'not auto-submittable'."""
    try:
        model = str(exp["model"])
        baseline = str(exp["baseline_style"])
        candidate = str(exp["candidate_style"])
        tasks = int(exp.get("tasks", 20))
        repeats = int(exp.get("repeats", 5))
        n_facts = int(exp.get("n_facts", 12))
        filler_turns = int(exp.get("filler_turns", 40))
        summary_budget = int(exp.get("summary_budget", 150))
    except (KeyError, TypeError, ValueError):
        return None
    if not model.startswith("bedrock/") or not _MODEL_PATTERN.fullmatch(model):
        return None
    valid = _valid_styles()
    if baseline not in valid or candidate not in valid or baseline == candidate:
        return None
    if not (1 <= tasks <= 20 and 1 <= repeats <= 5):
        return None
    return build_message_body(
        generate_experiment_id(),
        model,
        tasks,
        repeats,
        n_facts,
        filler_turns,
        summary_budget,
        30,
        baseline,
        candidate,
    )


def run_propose(table, ssm_client, s3_client, bucket: str, model: str) -> int:
    slots = DAILY_CAP - count_created_today(table)
    if slots <= 0:
        return 0
    sources = gather()
    if not sources:
        notify(
            table,
            ssm_client,
            "The proposer ran but found no fresh sources. No proposals today.",
        )
        return 0
    archive = list_recent(table)
    raw = _complete(
        model,
        [
            {"role": "system", "content": PROPOSER_SYSTEM},
            {"role": "user", "content": build_prompt(sources, archive, slots)},
        ],
    )
    proposals = parse_proposals(raw)[:slots]
    if not proposals:
        notify(
            table,
            ssm_client,
            "The proposer ran but produced no valid proposals this time.",
        )
        return 0
    lines, buttons = [], []
    for index, proposal in enumerate(proposals, 1):
        pid = generate_proposal_id()
        submit_body = None
        if proposal["kind"] == "registered_rerun":
            submit_body = _registered_submit_body(proposal.get("experiment") or {})
            if submit_body is None:
                proposal["kind"] = "new_hypothesis"
        file_proposal(
            table,
            pid,
            proposal["title"],
            proposal["headline"],
            proposal["citation"],
            proposal["distance"],
            proposal["kind"],
            submit_body=submit_body,
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=f"proposals/{pid}/proposal.json",
            Body=json.dumps(
                {"proposal": proposal, "sources_seen": len(sources)}
            ).encode("utf-8"),
        )
        lines.append(
            f"{index}. {proposal['title']}\n"
            f"{proposal['headline']}\n"
            f"Source: {proposal['citation']}\n"
            f"Kind: {proposal['kind']}"
        )
        buttons.append(
            [
                (f"APPROVE {index}", f"prop:{pid}:approve"),
                (f"REJECT {index}", f"prop:{pid}:reject"),
            ]
        )
    text = "New proposals. Tap to decide.\n\n" + "\n\n".join(lines)
    notify(table, ssm_client, text, buttons=buttons)
    return len(proposals)
```

- [ ] **Step 3: Add the CLI command**

In `src/agentlab/worker.py` add:

```python
@worker_app.command("propose")
def propose_command() -> None:
    """Scheduled proposer run: flush queued pings, then propose new work."""
    from agentlab.proposer import DEFAULT_PROPOSER_MODEL, run_propose

    state_table = _require_env("STATE_TABLE")
    results_bucket = _require_env("RESULTS_BUCKET")
    model = os.environ.get("PROPOSER_MODEL", DEFAULT_PROPOSER_MODEL)
    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    s3_client = boto3.client("s3")
    try:
        flushed = flush_pending(table, ssm_client)
    except Exception as exc:  # noqa: BLE001 - a stuck queued ping must not block proposing
        typer.echo(f"flush failed, continuing: {exc}", err=True)
        flushed = 0
    count = run_propose(table, ssm_client, s3_client, results_bucket, model)
    typer.echo(f"flushed {flushed} pending pings, filed {count} proposals")
```

Add a CLI-level test in `tests/test_worker.py` (monkeypatch `agentlab.proposer.run_propose` to return 2 and assert the echo line).

- [ ] **Step 4: Full suite, lint, commit**

```bash
set -o pipefail && uv run pytest && uv run ruff check
git add src/agentlab/proposer.py src/agentlab/worker.py tests/
git commit -m "feat: proposer worker with cited proposals and tap buttons"
```

---

### Task 6: the approvals webhook Lambda

**Files:**
- Create: `infra/lambda/approvals_webhook.py`, `tests/test_approvals_webhook.py`

**Interfaces:**
- Consumes: the proposal item shape (Task 2), callback data `prop:<pid>:approve|reject` (Task 5), the SQS message body contract (existing `cloud.build_message_body` - the stored `submit_body` is sent verbatim).
- Produces: `handler(event, context)` for a Lambda function URL. Env contract: `STATE_TABLE`, `QUEUE_URL`, `ALLOWED_USER_ID`, `TOKEN_PARAM`, `SECRET_PARAM`.
- Self-contained single file: stdlib + boto3 only, NO agentlab imports (the deployment package is this one file). It re-implements `set_verdict`'s exact conditional update; if Task 2's item shape changes, this file changes in the same commit.

- [ ] **Step 1: Write failing tests**

Create `tests/test_approvals_webhook.py`.
Import the handler by path:

```python
import importlib.util
import sys
from pathlib import Path

_LAMBDA_PATH = Path(__file__).parent.parent / "infra" / "lambda" / "approvals_webhook.py"
spec = importlib.util.spec_from_file_location("approvals_webhook", _LAMBDA_PATH)
webhook = importlib.util.module_from_spec(spec)
sys.modules["approvals_webhook"] = webhook
spec.loader.exec_module(webhook)
```

Fixtures: moto table + SSM (params `/agentlab/telegram/bot-token` and `/agentlab/telegram/webhook-secret`, values `"test-token"`/`"test-secret"`) + an SQS queue; env vars set via monkeypatch (`STATE_TABLE`, `QUEUE_URL`, `ALLOWED_USER_ID="6309668956"`, `TOKEN_PARAM`, `SECRET_PARAM`); `monkeypatch.setattr(webhook, "_telegram", recorder)` capturing `(method, payload)` tuples; clear `webhook._SSM` between tests (`monkeypatch.setattr(webhook, "_SSM", {})`).

Event builder:

```python
def make_event(secret="test-secret", from_id=6309668956, data="prop:p1:approve",
               text="msg text", keyboard=None):
    update = {
        "callback_query": {
            "id": "cb1",
            "from": {"id": from_id},
            "data": data,
            "message": {
                "message_id": 7,
                "chat": {"id": 6309668956},
                "text": text,
                "reply_markup": {"inline_keyboard": keyboard or []},
            },
        }
    }
    return {
        "headers": {"x-telegram-bot-api-secret-token": secret},
        "body": json.dumps(update),
    }
```

Tests (write each fully):

```python
def test_wrong_secret_403(): ...          # returns 403, no table writes, no telegram calls
def test_foreign_tap_ignored(): ...       # from_id=42 -> 200, proposal still PROPOSED, no telegram calls
def test_non_callback_update_ok(): ...    # body {"message": {...}} -> 200, nothing happens
def test_malformed_callback_data_ignored(): ...  # data="drop tables" -> 200, no writes
def test_approve_sets_verdict_and_edits(): ...
    # file a PROPOSED item first (raw put_item mirroring Task 2's shape);
    # approve -> item APPROVED with verdict_source "telegram_tap";
    # telegram calls contain answerCallbackQuery then editMessageText;
    # the edit text ends with "APPROVED: p1"; the edited keyboard row for
    # p1 is gone while another proposal's row survives.
def test_reject_sets_rejected(): ...
def test_double_tap_answers_already_decided(): ...
    # second approve -> status stays APPROVED, answerCallbackQuery text
    # contains "already decided", NO editMessageText on the second tap.
def test_approve_with_submit_body_auto_submits(): ...
    # item carries submit_body '{"experiment_id": "exp-9", ...}';
    # approve -> one SQS message whose body equals submit_body verbatim;
    # a CREATED event item exists for exp-9; toast contains "submitted".
def test_base64_encoded_body(): ...       # isBase64Encoded=True path works
def test_telegram_failure_does_not_lose_verdict(): ...
    # _telegram raises; handler still returns 200 and the verdict is written
```

Run: expected FAIL (file missing).

- [ ] **Step 2: Implement `infra/lambda/approvals_webhook.py`**

```python
"""Telegram approvals webhook: the only inbound path into AgentLab.

Runs as a plain-Python Lambda behind a function URL (auth NONE; the secret
header IS the auth, validated first thing). Telegram calls it on every
update. The handler acts only on callback-query taps that carry the right
secret header AND come from Daniel's Telegram user id; everything else is
logged and ignored with a 200 (Telegram retries non-200s, and a retry storm
on garbage is worthless).

A valid tap writes the verdict to the proposals ledger with a conditional
update (first tap wins; a second tap gets an "already decided" toast). An
APPROVE on a proposal that carries a prepared submit_body also auto-submits
that experiment: the stored body goes to SQS verbatim and a CREATED event is
recorded, exactly what `agentlab cloud submit` would have done.

Self-contained on purpose: the deployment package is this single file, so it
imports nothing from the agentlab package (stdlib + boto3 only, both present
in the Lambda runtime). The item shape and conditional expression mirror
src/agentlab/proposals.py; change both in the same commit or not at all.
"""

import base64
import json
import os
import urllib.request
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

_SSM: dict = {}


def _param(name: str) -> str:
    if name not in _SSM:
        client = boto3.client("ssm")
        _SSM[name] = client.get_parameter(Name=name, WithDecryption=True)[
            "Parameter"
        ]["Value"]
    return _SSM[name]


def _telegram(method: str, payload: dict) -> None:
    """POST one Telegram Bot API call. Module-level so tests monkeypatch it."""
    token = _param(os.environ["TOKEN_PARAM"])
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=10).read()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ok() -> dict:
    return {"statusCode": 200, "body": "ok"}


def _set_verdict(table, pid: str, verdict: str) -> bool:
    """True if this tap decided the proposal; False if it was already decided."""
    try:
        table.update_item(
            Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"},
            UpdateExpression="SET #s = :v, verdict_ts = :ts, verdict_source = :src",
            ConditionExpression="#s = :proposed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":v": verdict,
                ":proposed": "PROPOSED",
                ":ts": _now(),
                ":src": "telegram_tap",
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _auto_submit(table, pid: str) -> str | None:
    """Send an approved registered re-run to the fabric; None if not registered."""
    item = table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"}
    ).get("Item") or {}
    submit_body = item.get("submit_body")
    if not submit_body:
        return None
    experiment_id = json.loads(submit_body)["experiment_id"]
    boto3.client("sqs").send_message(
        QueueUrl=os.environ["QUEUE_URL"], MessageBody=submit_body
    )
    ts = _now()
    table.put_item(
        Item={
            "experiment_id": experiment_id,
            "sk": f"event#{ts}#CREATED",
            "event": "CREATED",
            "arm": None,
            "detail": f"auto-submitted from {pid}",
            "ts": ts,
        }
    )
    return experiment_id


def _strip_buttons(markup: dict, pid: str) -> list:
    prefix = f"prop:{pid}:"
    rows = markup.get("inline_keyboard") or []
    return [
        row
        for row in rows
        if not any(
            str(button.get("callback_data", "")).startswith(prefix)
            for button in row
        )
    ]


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-telegram-bot-api-secret-token") != _param(
        os.environ["SECRET_PARAM"]
    ):
        return {"statusCode": 403, "body": "forbidden"}

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    update = json.loads(body)

    callback = update.get("callback_query")
    if not callback:
        return _ok()

    if callback.get("from", {}).get("id") != int(os.environ["ALLOWED_USER_ID"]):
        print(f"foreign tap ignored: from={callback.get('from', {}).get('id')}")
        return _ok()

    parts = (callback.get("data") or "").split(":")
    if len(parts) != 3 or parts[0] != "prop" or parts[2] not in ("approve", "reject"):
        print(f"unknown callback data ignored: {callback.get('data')!r}")
        return _ok()
    pid, action = parts[1], parts[2]
    verdict = "APPROVED" if action == "approve" else "REJECTED"

    table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
    decided = _set_verdict(table, pid, verdict)

    submitted = None
    if decided and verdict == "APPROVED":
        submitted = _auto_submit(table, pid)

    if not decided:
        toast = f"{pid} was already decided"
    elif submitted:
        toast = f"APPROVED and submitted: {submitted}"
    else:
        toast = f"{verdict}: {pid}"

    try:
        _telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback["id"], "text": toast[:200]},
        )
        message = callback.get("message")
        if message and decided:
            rows = _strip_buttons(message.get("reply_markup") or {}, pid)
            decision_line = f"\n\n{verdict}: {pid}"
            if submitted:
                decision_line += f"\nsubmitted: {submitted}"
            edit = {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": (message.get("text") or "") + decision_line,
            }
            if rows:
                edit["reply_markup"] = {"inline_keyboard": rows}
            _telegram("editMessageText", edit)
    except Exception as exc:  # noqa: BLE001 - message cosmetics must never lose a recorded verdict
        print(f"telegram call failed after verdict write: {exc}")

    return _ok()
```

- [ ] **Step 3: Run tests, lint, commit**

```bash
uv run pytest tests/test_approvals_webhook.py -v && uv run ruff check infra/lambda tests/test_approvals_webhook.py
git add infra/lambda/approvals_webhook.py tests/test_approvals_webhook.py
git commit -m "feat: telegram approvals webhook lambda"
```

---

### Task 7: Terraform - SSM params, Lambda, proposer task, schedules

**Files:**
- Create: `infra/telegram.tf`, `infra/lambda.tf`, `infra/scheduler.tf`
- Modify: `infra/main.tf` (archive provider), `infra/variables.tf`, `infra/ecs.tf`, `infra/iam.tf`, `infra/outputs.tf`, `infra/.gitignore`

**Interfaces:**
- Consumes: existing resources (`aws_dynamodb_table.state`, `aws_s3_bucket.results`, `aws_sqs_queue.experiments`, `aws_ecs_cluster.agentlab`, `aws_ecr_repository.agentlab`, `aws_iam_role.ecs_execution`, `data.aws_subnets.default_public`, `aws_security_group.fargate_egress`, `data.aws_caller_identity.current`).
- Produces: Lambda `agentlab-approvals-webhook` + public function URL (auth NONE - the secret header validated in code is the auth, per spec); proposer task definition `agentlab-proposer` (0.5 vCPU / 1 GB, ARM64, same image, command `["worker", "propose"]`); three `aws_scheduler_schedule`s in Europe/Amsterdam: `agentlab-flush-pings` 08:00 (command override `["worker", "flush-pings"]`), `agentlab-propose-morning` 09:30, `agentlab-propose-midday` 12:00; SSM param `/agentlab/telegram/chat-id` (String, managed); output `approvals_webhook_url`.
- NOT produced here: the bot-token and webhook-secret parameters. Those are secrets, created out of band in Task 8 so they never enter Terraform state. Terraform passes only their NAMES to the Lambda env.

- [ ] **Step 1: main.tf, variables, gitignore**

In `infra/main.tf` `required_providers`, add:

```hcl
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.0"
    }
```

In `infra/variables.tf`, add:

```hcl
variable "telegram_chat_id" {
  description = "Daniel's Telegram chat/user id. The webhook Lambda accepts taps from this id only. Not a secret."
  type        = string
  default     = "6309668956"
}
```

In `infra/.gitignore`, ensure these lines exist (add missing ones):

```
*.tfstate
*.tfstate.*
.terraform/
tfplan
.build/
```

If `infra/tfplan` is currently tracked (`git ls-files infra/tfplan` prints it), run `git rm --cached infra/tfplan`.

- [ ] **Step 2: `infra/telegram.tf`**

```hcl
# Telegram channel parameters. The chat id is not a secret and is managed
# here. The bot token and the webhook secret ARE secrets: they are created
# out of band (aws ssm put-parameter + scripts/register_telegram_webhook.py)
# so they never appear in Terraform state or the repo. Everything that needs
# them reads them by NAME at runtime, so Terraform only handles the names.

resource "aws_ssm_parameter" "telegram_chat_id" {
  name  = "/agentlab/telegram/chat-id"
  type  = "String"
  value = var.telegram_chat_id
}

locals {
  telegram_token_param      = "/agentlab/telegram/bot-token"
  telegram_secret_param     = "/agentlab/telegram/webhook-secret"
  telegram_param_arn_prefix = "arn:aws:ssm:${var.aws_region}:${data.aws_caller_identity.current.account_id}:parameter/agentlab/telegram"
}
```

- [ ] **Step 3: `infra/lambda.tf`**

```hcl
# The approvals webhook: a single-file Python Lambda behind a public function
# URL. authorization_type NONE is deliberate (spec: Telegram cannot sign
# SigV4); the Telegram secret-token header, validated first thing in the
# handler, is the auth. The IAM role grants exactly what the handler does:
# read the two telegram parameters, read/write the state table, send to the
# experiments queue, write its own logs.

data "archive_file" "approvals_webhook" {
  type        = "zip"
  source_file = "${path.module}/lambda/approvals_webhook.py"
  output_path = "${path.module}/.build/approvals_webhook.zip"
}

resource "aws_cloudwatch_log_group" "approvals_webhook" {
  name              = "/aws/lambda/agentlab-approvals-webhook"
  retention_in_days = 30
}

resource "aws_iam_role" "approvals_webhook" {
  name = "agentlab-approvals-webhook"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "lambda.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "approvals_webhook" {
  name = "webhook-runtime"
  role = aws_iam_role.approvals_webhook.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "Logs"
        Effect = "Allow"
        Action = ["logs:CreateLogStream", "logs:PutLogEvents"]
        Resource = ["${aws_cloudwatch_log_group.approvals_webhook.arn}:*"]
      },
      {
        Sid      = "TelegramParams"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "${local.telegram_param_arn_prefix}/*"
      },
      {
        Sid    = "Ledger"
        Effect = "Allow"
        Action = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem"]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Sid      = "AutoSubmit"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.experiments.arn
      },
    ]
  })
}

resource "aws_lambda_function" "approvals_webhook" {
  function_name    = "agentlab-approvals-webhook"
  role             = aws_iam_role.approvals_webhook.arn
  runtime          = "python3.12"
  handler          = "approvals_webhook.handler"
  filename         = data.archive_file.approvals_webhook.output_path
  source_code_hash = data.archive_file.approvals_webhook.output_base64sha256
  timeout          = 10
  memory_size      = 256

  environment {
    variables = {
      STATE_TABLE     = aws_dynamodb_table.state.name
      QUEUE_URL       = aws_sqs_queue.experiments.url
      ALLOWED_USER_ID = var.telegram_chat_id
      TOKEN_PARAM     = local.telegram_token_param
      SECRET_PARAM    = local.telegram_secret_param
    }
  }

  depends_on = [aws_cloudwatch_log_group.approvals_webhook]
}

resource "aws_lambda_function_url" "approvals_webhook" {
  function_name      = aws_lambda_function.approvals_webhook.function_name
  authorization_type = "NONE"
}

resource "aws_lambda_permission" "approvals_webhook_url" {
  statement_id           = "AllowPublicFunctionUrl"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.approvals_webhook.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}
```

- [ ] **Step 4: proposer task definition + roles**

In `infra/ecs.tf`, add (mirroring the existing task definitions):

```hcl
resource "aws_cloudwatch_log_group" "proposer" {
  name              = "/ecs/agentlab-proposer"
  retention_in_days = 30
}

# 0.5 vCPU / 1 GB: the proposer makes one LLM call and a handful of HTTP/AWS
# calls; it never runs evals.
resource "aws_ecs_task_definition" "proposer" {
  family                   = "agentlab-proposer"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.proposer_task.arn

  runtime_platform {
    cpu_architecture        = "ARM64"
    operating_system_family = "LINUX"
  }

  container_definitions = jsonencode([
    {
      name      = "proposer"
      image     = "${aws_ecr_repository.agentlab.repository_url}:${var.image_tag}"
      essential = true
      command   = ["worker", "propose"]
      environment = [
        { name = "AWS_REGION", value = var.aws_region },
        { name = "AWS_DEFAULT_REGION", value = var.aws_region },
        { name = "STATE_TABLE", value = aws_dynamodb_table.state.name },
        { name = "RESULTS_BUCKET", value = aws_s3_bucket.results.id },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.proposer.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "proposer"
        }
      }
    }
  ])
}
```

In `infra/iam.tf`:

1. Add `"${aws_cloudwatch_log_group.proposer.arn}:*"` to the `Logs` statement of `aws_iam_role_policy.ecs_execution`.
2. Add to the `aws_iam_role_policy.ecs_task` policy (the finalizer now sends the finalize ping under this role) two statements:

```hcl
      {
        Sid      = "TelegramParams"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "${local.telegram_param_arn_prefix}/*"
      },
      {
        # notify() queues pending pings (PutItem - already granted) and
        # flush/queue reads need Query + DeleteItem on the pending partition;
        # DynamoDB cannot scope IAM to a partition, so table-level it is.
        Sid      = "PendingPings"
        Effect   = "Allow"
        Action   = ["dynamodb:Query", "dynamodb:DeleteItem"]
        Resource = aws_dynamodb_table.state.arn
      },
```

3. Add the proposer task role:

```hcl
resource "aws_iam_role" "proposer_task" {
  name = "agentlab-proposer-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "ecs-tasks.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "proposer_task" {
  name = "proposer-runtime"
  role = aws_iam_role.proposer_task.id

  # Exactly what worker propose/flush-pings does at runtime: one Bedrock
  # completion, telegram params, ledger + pending-ping items, proposal docs
  # to its own S3 prefix. No SQS: auto-submission is the Lambda's job.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
          "bedrock:Converse",
          "bedrock:ConverseStream",
        ]
        Resource = "*"
      },
      {
        Sid      = "TelegramParams"
        Effect   = "Allow"
        Action   = ["ssm:GetParameter"]
        Resource = "${local.telegram_param_arn_prefix}/*"
      },
      {
        Sid    = "Ledger"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = aws_dynamodb_table.state.arn
      },
      {
        Sid      = "ProposalDocs"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.results.arn}/proposals/*"
      },
    ]
  })
}
```

- [ ] **Step 5: `infra/scheduler.tf`**

```hcl
# EventBridge Scheduler: three cron schedules in Europe/Amsterdam, each
# launching the proposer task definition on Fargate. 08:00 is a pure flush
# (delivers pings queued during quiet hours, which end at exactly 08:00);
# 09:30 and 12:00 are full proposer runs (they flush first, then propose).
# The target arn is the CLUSTER; the task definition rides in ecs_parameters,
# revision-pinned (.arn) so a redeploy updates the schedules deterministically.
# The `input` is the ECS TaskOverride JSON - implementer: verify the
# containerOverrides shape against the aws_scheduler_schedule provider docs
# before applying.

resource "aws_iam_role" "scheduler" {
  name = "agentlab-scheduler"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "scheduler.amazonaws.com" }
        Action    = "sts:AssumeRole"
        Condition = {
          StringEquals = { "aws:SourceAccount" = data.aws_caller_identity.current.account_id }
        }
      }
    ]
  })
}

resource "aws_iam_role_policy" "scheduler" {
  name = "run-proposer-task"
  role = aws_iam_role.scheduler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RunProposer"
        Effect = "Allow"
        Action = ["ecs:RunTask"]
        Resource = [
          aws_ecs_task_definition.proposer.arn,
          "${aws_ecs_task_definition.proposer.arn_without_revision}:*",
        ]
      },
      {
        Sid      = "PassEcsRoles"
        Effect   = "Allow"
        Action   = ["iam:PassRole"]
        Resource = [aws_iam_role.ecs_execution.arn, aws_iam_role.proposer_task.arn]
        Condition = {
          StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" }
        }
      },
    ]
  })
}

locals {
  proposer_schedules = {
    flush-pings = {
      cron    = "cron(0 8 * * ? *)"
      command = ["worker", "flush-pings"]
    }
    propose-morning = {
      cron    = "cron(30 9 * * ? *)"
      command = ["worker", "propose"]
    }
    propose-midday = {
      cron    = "cron(0 12 * * ? *)"
      command = ["worker", "propose"]
    }
  }
}

resource "aws_scheduler_schedule" "proposer" {
  for_each = local.proposer_schedules

  name                         = "agentlab-${each.key}"
  schedule_expression          = each.value.cron
  schedule_expression_timezone = "Europe/Amsterdam"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.agentlab.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.proposer.arn
      launch_type         = "FARGATE"

      network_configuration {
        subnets          = data.aws_subnets.default_public.ids
        security_groups  = [aws_security_group.fargate_egress.id]
        assign_public_ip = true
      }
    }

    input = jsonencode({
      containerOverrides = [
        {
          name    = "proposer"
          command = each.value.command
        }
      ]
    })

    retry_policy {
      maximum_retry_attempts = 1
    }
  }
}
```

- [ ] **Step 6: outputs**

Add to `infra/outputs.tf`:

```hcl
output "approvals_webhook_url" {
  description = "Public function URL of the approvals webhook - the setWebhook target for scripts/register_telegram_webhook.py."
  value       = aws_lambda_function_url.approvals_webhook.function_url
}

output "proposer_task_definition_arn" {
  description = "Proposer task definition ARN, for manual `aws ecs run-task` smoke runs."
  value       = aws_ecs_task_definition.proposer.arn
}
```

- [ ] **Step 7: Validate and commit**

Run: `terraform -chdir=infra fmt && terraform -chdir=infra init -upgrade -backend=false && terraform -chdir=infra validate`
Expected: `Success! The configuration is valid.`
(`plan`/`apply` wait for Task 8's credentials.)

```bash
git add infra/
git commit -m "infra: approvals webhook lambda, proposer task, daytime schedules"
```

---

### Task 8: deploy, register the webhook, E2E acceptance

**GATE: this task needs live AWS credentials.** If `AWS_PROFILE=agentlab aws sts get-caller-identity` fails, STOP and ask Daniel for one `! aws login`, then continue.

**Files:**
- Create: `scripts/register_telegram_webhook.py`

**Interfaces:**
- Consumes: `terraform output -raw approvals_webhook_url`, SSM param names from Task 7, the bot token (provided by Daniel at runtime, NEVER written to any file in the repo).

- [ ] **Step 1: Write `scripts/register_telegram_webhook.py`**

```python
"""Register the approvals Lambda as the Telegram bot's webhook.

Generates a fresh webhook secret, stores it in SSM (SecureString), and calls
setWebhook so Telegram sends callback taps to the function URL with that
secret in the X-Telegram-Bot-Api-Secret-Token header. Idempotent: rerunning
rotates the secret and re-registers.

Usage:
    AWS_PROFILE=agentlab uv run python scripts/register_telegram_webhook.py \
        --url "$(terraform -chdir=infra output -raw approvals_webhook_url)"
"""

import argparse
import secrets

import boto3
import httpx

TOKEN_PARAM = "/agentlab/telegram/bot-token"
SECRET_PARAM = "/agentlab/telegram/webhook-secret"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="The Lambda function URL.")
    args = parser.parse_args()

    ssm = boto3.client("ssm")
    token = ssm.get_parameter(Name=TOKEN_PARAM, WithDecryption=True)["Parameter"][
        "Value"
    ]
    webhook_secret = secrets.token_urlsafe(32)
    ssm.put_parameter(
        Name=SECRET_PARAM, Value=webhook_secret, Type="SecureString", Overwrite=True
    )

    response = httpx.post(
        f"https://api.telegram.org/bot{token}/setWebhook",
        json={
            "url": args.url,
            "secret_token": webhook_secret,
            "allowed_updates": ["callback_query"],
        },
        timeout=30,
    )
    response.raise_for_status()
    print("setWebhook:", response.json())

    info = httpx.get(
        f"https://api.telegram.org/bot{token}/getWebhookInfo", timeout=30
    )
    print("getWebhookInfo:", info.json())


if __name__ == "__main__":
    main()
```

Commit it before deploying:

```bash
git add scripts/register_telegram_webhook.py
git commit -m "feat: webhook registration script"
```

- [ ] **Step 2: Store the bot token in SSM**

The token is the one Daniel got from BotFather for @Learn1901_bot (he pasted it in the session; it lives nowhere in the repo).
Run with the value passed inline from the session, not from a file:

```bash
AWS_PROFILE=agentlab aws ssm put-parameter \
  --name /agentlab/telegram/bot-token \
  --type SecureString --overwrite \
  --value "<BOT_TOKEN_FROM_DANIEL>"
```

- [ ] **Step 3: Build and push the image, then apply**

The image needs the new code and the new deps (httpx explicit, matplotlib):

```bash
AWS_PROFILE=agentlab ./scripts/build_and_push_image.sh
AWS_PROFILE=agentlab terraform -chdir=infra apply
```

Review the plan before approving: expect ~1 SSM param, 1 log group x3, lambda + url + permission + 2 roles + 2 policies, proposer taskdef, scheduler role + policy, 3 schedules, ecs_task/ecs_execution policy updates, new taskdef revisions.

- [ ] **Step 4: Register the webhook**

```bash
AWS_PROFILE=agentlab uv run python scripts/register_telegram_webhook.py \
  --url "$(terraform -chdir=infra output -raw approvals_webhook_url)"
```

Expected: `setWebhook: {'ok': True, ...}` and `getWebhookInfo` showing the URL with `pending_update_count` 0.

- [ ] **Step 5: E2E acceptance against the spec's five criteria**

1. **Proposer runs and pings** - run one proposer task now instead of waiting for 09:30:
   `AWS_PROFILE=agentlab aws ecs run-task --cluster agentlab --launch-type FARGATE --task-definition agentlab-proposer --network-configuration '<same subnets/SG as the schedule>' --region eu-west-1`
   Expected: within ~2 minutes Daniel's phone gets "New proposals. Tap to decide." with APPROVE/REJECT buttons, and `proposal#...` items exist in DynamoDB.
2. **Tap approves and auto-submits** - Daniel taps APPROVE on a `registered_rerun` proposal.
   Expected: toast "APPROVED and submitted: exp-...", the message edits to show the decision, the state machine runs, and the finalize ping (verdict + chart PNG) lands on his phone when it completes.
3. **Foreign tap does nothing** - covered by `test_foreign_tap_ignored` (unit) since no second Telegram account is on hand; ALSO verify a curl without the secret header gets 403:
   `curl -s -o /dev/null -w "%{http_code}" -X POST "$(terraform -chdir=infra output -raw approvals_webhook_url)" -d '{}'` -> `403`.
4. **Quiet hours hold** - covered by `test_notify_quiet_queues_instead_of_sending` and `test_flush_sends_in_order_and_deletes` (unit); live confirmation happens naturally the first night.
5. **Teardown stays clean** - `terraform -chdir=infra plan -destroy` must enumerate every v0.3 resource (do NOT apply the destroy); idle cost check: Lambda, Scheduler, SSM standard params, and stopped tasks all bill $0 idle.

- [ ] **Step 6: Record and push**

Append run evidence (experiment id, proposal ids, screenshots not needed) to the ledger, then:

```bash
git push origin main
```

---

## Self-Review Notes

- Spec coverage: product pings (Tasks 1, 3, 5), tap approvals + foreign-tap rejection (Task 6), scheduled proposer with sources + anti-collapse (Tasks 4, 5), quiet hours incl. the 08:00 flush schedule (Tasks 1, 7), ledger in the existing table (Task 2), SSM secrets (Tasks 7, 8), acceptance criteria (Task 8 Step 5). The spec's "chart image or video when one exists" is satisfied with the chart PNG; videos remain local-render (deferred rung, unchanged).
- Type consistency: `notify(table, ssm_client, text, buttons, photo_png)` and callback format `prop:<pid>:<action>` are used identically in Tasks 1, 5, and 6; `PairedResult.mean_delta/ci_low/ci_high` match `stats.py` as read on 2026-08-19.
- Known deliberate duplication: the Lambda re-implements `set_verdict` (single-file deployment package); both sites carry a comment pointing at each other.
- Open verification points for implementers (flagged in-task): the DeepSeek harness repo slug (Task 4 Step 1) and the `aws_scheduler_schedule` ECS `input` override shape (Task 7 Step 5).
