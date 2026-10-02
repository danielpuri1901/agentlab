"""The weekly consolidation run: episodes in, a gated profile swap out."""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from agentlab import consolidate as consolidate_mod
from agentlab import notify as notify_mod
from agentlab.consolidate import MIN_NEW_EPISODES, run_consolidate
from agentlab.notify import CHAT_ID_PARAM, TOKEN_PARAM
from agentlab.profile import POINTER_KEY, get_pointer, set_pointer, write_profile

TABLE = "agentlab-state-test"
BUCKET = "agentlab-results-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 10, 5, 16, 0, tzinfo=UTC)

MODEL_PROFILE = """# Daniel's taste profile
version: pending

## Prefer
- Real papers on agent harnesses (evidence: 2 approved)
- Foundational papers (evidence: 1 golden)
- Agent memory papers (evidence: 1 COOL)

## Avoid
- SDK release notes (evidence: 2 rejected)
- Product throughput posts (evidence: 1 ignored)
- Random HN links (evidence: 1 unrated)

## Positive examples
- Good Eval Paper (approved, arxiv)
- Good Memory Paper (cool, arxiv)
- Attention Is All You Need (golden_yes, classic)

## Negative examples
- LangGraph 1.2.12 Release (rejected, github)
- Mercury Throughput Post (rejected, blog)
- Filler Reject 6 (rejected, arxiv)

## Source weights
- placeholder
"""


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def fabric():
    with mock_aws():
        table = boto3.resource("dynamodb", region_name=REGION).create_table(
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
        ssm.put_parameter(Name=CHAT_ID_PARAM, Value="123456789", Type="String")
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        yield table, ssm, s3


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
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 10, 5, 18, 0, tzinfo=AMS)
    )
    return calls


def _proposal(table, pid, status, title, citation, created="2026-10-01T09:30:00.000000Z"):
    table.put_item(
        Item={
            "experiment_id": f"proposal#{pid}",
            "sk": "proposal",
            "pid": pid,
            "status": status,
            "title": title,
            "citation": citation,
            "created_ts": created,
            "verdict_ts": created if status != "PROPOSED" else None,
        }
    )


def _video(table, key, rating, title, sent="2026-10-01T10:30:00.000000Z"):
    table.put_item(
        Item={
            "experiment_id": f"video#{key}",
            "sk": "video",
            "track": "core",
            "url": f"https://arxiv.org/abs/2509.{key[-5:]}",
            "title": title,
            "sent_ts": sent,
            "rating": rating,
            "rating_ts": sent if rating else None,
        }
    )


def _seed_ledger(table):
    # Identities hash into fixed splits (sha1 mod 10 < 3 is held out):
    # 2509.00001 held out, 2509.00002 train, 2509.00005 held out, 2509.00011
    # train, 2509.00012 held out, fillers 6 to 11 train, 12 held out.
    _proposal(table, "p1", "APPROVED", "Good Harness Paper", "https://arxiv.org/abs/2509.00001")
    _proposal(table, "p2", "APPROVED", "Good Eval Paper", "https://arxiv.org/abs/2509.00002")
    _proposal(table, "p3", "REJECTED", "LangGraph 1.2.12 Release", "https://github.com/x/y/releases/tag/1.2.12")
    _proposal(table, "p4", "REJECTED", "Mercury Throughput Post", "https://example.com/mercury")
    _proposal(table, "p5", "PROPOSED", "Ignored Thing", "https://arxiv.org/abs/2509.00005")
    _video(table, "core-00011", "COOL", "Good Memory Paper")
    _video(table, "core-00012", None, "Unrated Thing")
    for i in range(6, 30):
        _proposal(table, f"q{i}", "REJECTED", f"Filler Reject {i}", f"https://arxiv.org/abs/2509.{i:05d}")
    for i in range(30, 50):
        _proposal(table, f"a{i}", "APPROVED", f"Filler Approve {i}", f"https://arxiv.org/abs/2509.{i:05d}")


def _golden_file(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text(
        json.dumps({"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "rating": "COOL", "why": "GOAT"}) + "\n"
        + json.dumps({"title": "Constitutional AI", "url": None, "rating": "SKIP", "why": "no"}) + "\n",
        encoding="utf-8",
    )
    return path


def _seed_file(tmp_path):
    path = tmp_path / "interests.md"
    path.write_text("SEED PROFILE TEXT", encoding="utf-8")
    return path


def _fake_complete(profile_output=MODEL_PROFILE, new_says_yes=None, old_says_yes=None):
    """Consolidation call returns `profile_output`. Probe calls answer YES for
    titles containing 'Good', 'Approve', or 'Attention' and NO otherwise,
    unless an override callable is given per profile text."""
    calls = []

    def complete(model, messages):
        calls.append((model, messages))
        system = messages[0]["content"]
        if system.startswith("You maintain Daniel's taste profile"):
            return profile_output
        title = messages[1]["content"].split("lesson on: ", 1)[1].split(" (", 1)[0]
        override = new_says_yes if "## Prefer" in system else old_says_yes
        if override is not None:
            return "YES" if override(title) else "NO"
        return "YES" if any(word in title for word in ("Good", "Approve", "Attention")) else "NO"

    complete.calls = calls
    return complete


def test_first_run_applies_profile_and_pings_with_revert(fabric, telegram_calls, monkeypatch, tmp_path):
    table, ssm, s3 = fabric
    _seed_ledger(table)
    fake = _fake_complete()
    monkeypatch.setattr(consolidate_mod, "_complete", fake)

    result = run_consolidate(
        table, s3, ssm, BUCKET, "bedrock/sonnet", "bedrock/haiku",
        _golden_file(tmp_path), _seed_file(tmp_path), now=NOW,
    )

    assert result["applied"] is True
    assert result["tally_only"] is False
    assert result["version"] == "20261005T160000Z"
    consolidate_calls = [c for c in fake.calls if c[1][0]["content"].startswith("You maintain")]
    assert len(consolidate_calls) == 1
    assert consolidate_calls[0][0] == "bedrock/sonnet"
    user_prompt = consolidate_calls[0][1][1]["content"]
    assert "SEED PROFILE TEXT" in user_prompt
    assert "Good Eval Paper (approved, arxiv" in user_prompt  # arxiv:2509.00002 hashes into the training split
    assert "Attention Is All You Need (golden_yes, classic" in user_prompt
    probe_models = {c[0] for c in fake.calls if not c[1][0]["content"].startswith("You maintain")}
    assert probe_models == {"bedrock/haiku"}

    pointer = get_pointer(table)
    assert pointer.version == "20261005T160000Z"
    assert pointer.s3_key == "profile/20261005T160000Z.md"
    assert pointer.previous_version is None
    assert pointer.source == "consolidate"
    assert pointer.eval["new_f1"] == pytest.approx(result["new_eval"]["f1"])
    body = s3.get_object(Bucket=BUCKET, Key="profile/20261005T160000Z.md")["Body"].read().decode()
    assert body.startswith("# Daniel's taste profile\nversion: 20261005T160000Z\n")
    assert "## Source weights\n- arxiv: approval" in body
    assert "placeholder" not in body
    s3.get_object(Bucket=BUCKET, Key="profile/candidates/20261005T160000Z.md")

    assert len(telegram_calls) == 1
    _url, kwargs = telegram_calls[0]
    text = kwargs["json"]["text"]
    assert text.startswith("Taste profile update.\nApplied: yes")
    assert "F1" in text and "golden recall" in text
    assert "Prefer added:" in text
    buttons = kwargs["json"]["reply_markup"]["inline_keyboard"]
    assert buttons == [[{"text": "REVERT", "callback_data": "prof:20261005T160000Z:revert"}]]


def test_tally_only_when_fewer_than_five_new_episodes(fabric, telegram_calls, monkeypatch, tmp_path):
    table, ssm, s3 = fabric
    _seed_ledger(table)
    write_profile(s3, BUCKET, "profile/v1.md", "V1 TEXT")
    set_pointer(table, "v1", "profile/v1.md", None, "consolidate", {}, datetime(2026, 10, 4, 12, 0, tzinfo=UTC))
    _proposal(table, "new1", "APPROVED", "After pointer", "https://arxiv.org/abs/2510.00001", created="2026-10-05T09:30:00.000000Z")

    def exploding(model, messages):
        raise AssertionError("no model call on a tally-only run")

    monkeypatch.setattr(consolidate_mod, "_complete", exploding)

    result = run_consolidate(
        table, s3, ssm, BUCKET, "m", "p", _golden_file(tmp_path), _seed_file(tmp_path), now=NOW
    )

    assert result["tally_only"] is True
    assert result["applied"] is False
    assert get_pointer(table).version == "v1"
    text = telegram_calls[0][1]["json"]["text"]
    assert text.startswith(f"Taste profile unchanged: 1 new episodes since v1, need {MIN_NEW_EPISODES}.")
    assert "reply_markup" not in telegram_calls[0][1]["json"]


def test_dry_run_writes_nothing_and_pings_nobody(fabric, telegram_calls, monkeypatch, tmp_path):
    table, ssm, s3 = fabric
    _seed_ledger(table)
    monkeypatch.setattr(consolidate_mod, "_complete", _fake_complete())

    result = run_consolidate(
        table, s3, ssm, BUCKET, "m", "p", _golden_file(tmp_path), _seed_file(tmp_path), now=NOW, dry_run=True
    )

    assert result["applied"] is True
    assert result["candidate"].startswith("# Daniel's taste profile")
    assert get_pointer(table) is None
    assert "Contents" not in s3.list_objects_v2(Bucket=BUCKET)
    assert telegram_calls == []


def test_invalid_model_output_keeps_old_profile(fabric, telegram_calls, monkeypatch, tmp_path):
    table, ssm, s3 = fabric
    _seed_ledger(table)
    monkeypatch.setattr(consolidate_mod, "_complete", _fake_complete(profile_output="nonsense"))

    result = run_consolidate(
        table, s3, ssm, BUCKET, "m", "p", _golden_file(tmp_path), _seed_file(tmp_path), now=NOW
    )

    assert result["applied"] is False
    assert result["problems"]
    assert get_pointer(table) is None
    keys = [o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]]
    assert keys == ["profile/candidates/20261005T160000Z.md"]
    text = telegram_calls[0][1]["json"]["text"]
    assert "Applied: no" in text
    assert "missing section" in text
    assert "reply_markup" not in telegram_calls[0][1]["json"]


def test_worse_eval_keeps_old_profile(fabric, telegram_calls, monkeypatch, tmp_path):
    table, ssm, s3 = fabric
    _seed_ledger(table)
    write_profile(s3, BUCKET, "profile/v1.md", "V1 TEXT")
    set_pointer(table, "v1", "profile/v1.md", None, "consolidate", {}, datetime(2026, 9, 1, 12, 0, tzinfo=UTC))
    fake = _fake_complete(new_says_yes=lambda title: False)
    monkeypatch.setattr(consolidate_mod, "_complete", fake)

    result = run_consolidate(
        table, s3, ssm, BUCKET, "m", "p", _golden_file(tmp_path), _seed_file(tmp_path), now=NOW
    )

    assert result["applied"] is False
    assert "fell" in result["reason"]
    assert get_pointer(table).version == "v1"
    assert "Applied: no" in telegram_calls[0][1]["json"]["text"]
    assert table.get_item(Key=POINTER_KEY)["Item"]["version"] == "v1"
