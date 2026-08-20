"""Proposer module tests, fully offline: moto stands in for SSM/DynamoDB/S3,
a monkeypatched `proposer._complete` stands in for the model provider, a
monkeypatched `proposer.gather` stands in for live HTTP fetchers, and a
monkeypatched `agentlab.notify.now_amsterdam`/`httpx.post` captures every
Telegram call without touching the network.
"""

import json
from datetime import UTC as _UTC
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from agentlab import notify as notify_mod
from agentlab import proposals as proposals_mod
from agentlab import proposer as proposer_mod
from agentlab.notify import CHAT_ID_PARAM, TOKEN_PARAM
from agentlab.proposals import DAILY_CAP, file_proposal
from agentlab.proposer import (
    DEFAULT_PROPOSER_MODEL,
    _registered_submit_body,
    parse_proposals,
    run_propose,
)

TABLE = "agentlab-state-test"
BUCKET = "agentlab-results-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")

# Built with explicit \n joins (NOT a triple-quoted block containing literal
# fence lines) so markdown tooling that scans this plan never desyncs on it;
# the VALUE is a fenced ```json block exactly as a model would emit it.
VALID_LLM_JSON = """[
  {"title": "Haiku vs codes_first rerun", "headline": "Test the champion on Haiku again.",
   "citation": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v0.4.0",
   "distance": "Repeats tournament 001 round 1 on a new inspect version.",
   "kind": "registered_rerun",
   "experiment": {"model": "bedrock/eu.amazon.nova-lite-v1:0", "tasks": 10, "repeats": 3,
                   "baseline_style": "truncate", "candidate_style": "codes_first"}},
  {"title": "Reflection on arithmetic", "headline": "New hypothesis needs your call.",
   "citation": "https://arxiv.org/abs/2601.00001", "distance": "No archive overlap.",
   "kind": "new_hypothesis"}
]"""
VALID_LLM_OUTPUT = "`" * 3 + "json\n" + VALID_LLM_JSON + "\n" + "`" * 3


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
    return calls


def _daytime(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )


FAKE_SOURCES = [
    {
        "source": "github",
        "title": "v0.4.0",
        "url": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v0.4.0",
    },
    {
        "source": "arxiv",
        "title": "Reflection helps arithmetic",
        "url": "https://arxiv.org/abs/2601.00001",
    },
]


def test_parse_proposals_strips_fences_and_clips():
    proposals = parse_proposals(VALID_LLM_OUTPUT)
    assert len(proposals) == 2
    assert proposals[0]["kind"] == "registered_rerun"
    assert proposals[1]["kind"] == "new_hypothesis"

    long_title = "x" * 500
    raw = json.dumps(
        [
            {
                "title": long_title,
                "headline": "h",
                "citation": "https://x",
                "distance": "d",
                "kind": "weird",
            },
            "not a dict",
        ]
    )
    parsed = parse_proposals(raw)
    assert len(parsed) == 1
    assert parsed[0]["title"] == long_title[:80]
    assert len(parsed[0]["title"]) == 80
    assert parsed[0]["kind"] == "new_hypothesis"

    assert parse_proposals("not json") == []


def test_registered_submit_body_valid():
    exp = {
        "model": "bedrock/eu.amazon.nova-lite-v1:0",
        "tasks": 10,
        "repeats": 3,
        "baseline_style": "truncate",
        "candidate_style": "codes_first",
    }
    body = _registered_submit_body(exp)
    assert body is not None
    assert body["experiment_id"].startswith("exp-")
    assert body["max_connections"] == 30
    assert body["baseline_style"] == "truncate"
    assert body["candidate_style"] == "codes_first"


def test_registered_submit_body_rejects():
    base = {
        "model": "bedrock/eu.amazon.nova-lite-v1:0",
        "tasks": 10,
        "repeats": 3,
        "baseline_style": "truncate",
        "candidate_style": "codes_first",
    }

    no_prefix = dict(base, model="eu.amazon.nova-lite-v1:0")
    assert _registered_submit_body(no_prefix) is None

    unknown_style = dict(base, baseline_style="not_a_style")
    assert _registered_submit_body(unknown_style) is None

    equal_styles = dict(base, candidate_style=base["baseline_style"])
    assert _registered_submit_body(equal_styles) is None

    too_many_tasks = dict(base, tasks=50)
    assert _registered_submit_body(too_many_tasks) is None

    too_many_repeats = dict(base, repeats=9)
    assert _registered_submit_body(too_many_repeats) is None

    missing_model = {k: v for k, v in base.items() if k != "model"}
    assert _registered_submit_body(missing_model) is None


def test_run_propose_files_pings_and_uploads(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", lambda: FAKE_SOURCES)
    monkeypatch.setattr(proposer_mod, "_complete", lambda model, messages: VALID_LLM_OUTPUT)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL)

    assert count == 2

    items = table.scan()["Items"]
    proposed = [i for i in items if i.get("sk") == "proposal"]
    assert len(proposed) == 2
    assert all(i["status"] == "PROPOSED" for i in proposed)

    rerun_items = [i for i in proposed if i["kind"] == "registered_rerun"]
    assert len(rerun_items) == 1
    assert "submit_body" in rerun_items[0]
    submit_body = json.loads(rerun_items[0]["submit_body"])
    assert submit_body["max_connections"] == 30

    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendMessage")
    reply_markup = kwargs["json"]["reply_markup"]
    rows = reply_markup["inline_keyboard"]
    assert len(rows) == 2
    for row in rows:
        assert len(row) == 2
        for button in row:
            assert button["callback_data"].startswith("prop:")
            assert button["callback_data"].endswith(":approve") or button[
                "callback_data"
            ].endswith(":reject")

    pids = [i["pid"] for i in proposed]
    for pid in pids:
        obj = s3.get_object(Bucket=BUCKET, Key=f"proposals/{pid}/proposal.json")
        body = json.loads(obj["Body"].read().decode("utf-8"))
        assert "proposal" in body
        assert body["sources_seen"] == len(FAKE_SOURCES)


def test_run_propose_respects_daily_cap(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)

    # Freeze proposals_mod.now() to a fixed UTC instant so created_ts falls
    # under "today" for count_created_today regardless of the wall clock.
    fixed_now = datetime(2026, 8, 19, 9, 0, tzinfo=_UTC)
    monkeypatch.setattr(proposals_mod, "now", lambda: fixed_now)

    for i in range(DAILY_CAP):
        file_proposal(
            table,
            f"prop-precap-{i}",
            f"Title {i}",
            "Headline.",
            "https://x",
            "d",
            "new_hypothesis",
        )

    def exploding_complete(model, messages):
        raise AssertionError("_complete must not be called when the daily cap is hit")

    monkeypatch.setattr(proposer_mod, "_complete", exploding_complete)
    monkeypatch.setattr(proposer_mod, "gather", lambda: FAKE_SOURCES)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL)

    assert count == 0
    assert telegram_calls == []


def test_run_propose_no_sources_says_so(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", list)

    def exploding_complete(model, messages):
        raise AssertionError("_complete must not be called when there are no sources")

    monkeypatch.setattr(proposer_mod, "_complete", exploding_complete)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL)

    assert count == 0
    assert len(telegram_calls) == 1
    _url, kwargs = telegram_calls[0]
    assert "no fresh sources" in kwargs["json"]["text"]
