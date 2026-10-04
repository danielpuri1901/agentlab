"""Proposer tests, fully offline: moto stands in for SSM/DynamoDB/S3, a
monkeypatched `proposer._complete` stands in for the model, a monkeypatched
`proposer.gather` stands in for live fetchers, and a monkeypatched
`httpx.post` captures every Telegram call.

The proposer picks lessons with Daniel's taste profile in its prompt. With
no profile pointer the prompt falls back to docs/interests.md, which is
what these tests exercise unless they set a pointer.
"""

import json
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from agentlab import notify as notify_mod
from agentlab import proposals as proposals_mod
from agentlab import proposer as proposer_mod
from agentlab.notify import CHAT_ID_PARAM, PENDING_PARTITION, TOKEN_PARAM
from agentlab.profile import set_pointer, write_profile
from agentlab.proposals import DAILY_CAP, file_proposal
from agentlab.proposer import (
    DEFAULT_PROPOSER_MODEL,
    build_prompt,
    format_message,
    next_classic,
    parse_proposals,
    run_propose,
)

TABLE = "agentlab-state-test"
BUCKET = "agentlab-results-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")
NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)

VALID_LLM_JSON = """[
  {"title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
   "why": "You asked for self-improvement loops; this is the canonical one.",
   "citation": "https://arxiv.org/abs/2601.00001",
   "distance": "No archive overlap.",
   "lens": "implement"},
  {"title": "Agent Memory Consolidation at Scale",
   "why": "Memory is on your core list.",
   "citation": "https://arxiv.org/abs/2601.00002",
   "distance": "New topic.",
   "lens": "frontier"}
]"""
VALID_LLM_OUTPUT = "`" * 3 + "json\n" + VALID_LLM_JSON + "\n" + "`" * 3

FAKE_SOURCES = [
    {
        "source": "github",
        "title": "v0.4.0",
        "url": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v0.4.0",
    },
    {
        "source": "arxiv",
        "title": "Reflexion: Language Agents with Verbal Reinforcement Learning",
        "url": "https://arxiv.org/abs/2601.00001",
        "summary": "Agents reflect verbally on failures.",
    },
    {
        "source": "hf",
        "title": "Agent Memory Consolidation at Scale",
        "url": "https://arxiv.org/abs/2601.00002",
    },
]


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
    return calls


def _daytime(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )
    monkeypatch.setattr(proposals_mod, "now", lambda: NOW)


def _capture_complete(monkeypatch, output=VALID_LLM_OUTPUT):
    captured = {}

    def fake_complete(model, messages):
        captured["model"] = model
        captured["messages"] = messages
        return output

    monkeypatch.setattr(proposer_mod, "_complete", fake_complete)
    return captured


def test_parse_proposals_strips_fences_clips_and_defaults_lens():
    proposals = parse_proposals(VALID_LLM_OUTPUT)
    assert len(proposals) == 2
    assert proposals[0]["lens"] == "implement"
    assert proposals[0]["kind"] == "new_hypothesis"

    raw = json.dumps(
        [
            {
                "title": "x" * 500,
                "why": "w" * 500,
                "citation": "https://x/" + "y" * 2000,
                "distance": "d",
                "lens": "weird",
            },
            {"title": "no why", "citation": "https://x", "distance": "d"},
            "not a dict",
        ]
    )
    parsed = parse_proposals(raw)
    assert len(parsed) == 1
    assert len(parsed[0]["title"]) == 80
    assert len(parsed[0]["why"]) == 300
    assert len(parsed[0]["citation"]) == 300
    assert parsed[0]["lens"] == "frontier"
    assert parse_proposals("not json") == []


def test_parse_proposals_accepts_legacy_headline_key():
    raw = json.dumps(
        [{"title": "T", "headline": "H", "citation": "https://x", "distance": "d"}]
    )
    assert parse_proposals(raw)[0]["why"] == "H"


def test_build_prompt_carries_profile_sources_and_ignored_status():
    archive = [
        {"title": "Old untapped", "status": "PROPOSED", "created_ts": "2026-08-10T09:30:00.000000Z", "distance": "d1"},
        {"title": "Approved one", "status": "APPROVED", "created_ts": "2026-08-18T09:30:00.000000Z", "distance": "d2"},
    ]
    prompt = build_prompt(FAKE_SOURCES[1:], archive, 2, "PROFILE TEXT", NOW)
    assert prompt.startswith("Daniel's taste profile:\nPROFILE TEXT\n\n")
    assert "- [arxiv] Reflexion: Language Agents with Verbal Reinforcement Learning :: https://arxiv.org/abs/2601.00001 :: Agents reflect verbally on failures." in prompt
    assert "- [IGNORED] Old untapped :: d1" in prompt
    assert "- [APPROVED] Approved one :: d2" in prompt
    assert prompt.rstrip().endswith("Pick at most 2 lessons as a JSON array.")


def test_format_message_shows_lens_title_source_and_buttons():
    entries = [
        {"pid": "p1", "title": "Attention Is All You Need", "why": "Foundational.", "citation": "https://arxiv.org/abs/1706.03762", "lens": "foundational", "source_type": "classic"},
        {"pid": "p2", "title": "Agent Memory Consolidation at Scale", "why": "Memory.", "citation": "https://arxiv.org/abs/2601.00002", "lens": "frontier", "source_type": "arxiv"},
    ]
    text, buttons = format_message(entries)
    assert text.startswith("Today's lessons. Tap to decide.\n\n")
    assert "1. [FOUNDATIONAL] Attention Is All You Need (classic)\nWhy: Foundational.\nhttps://arxiv.org/abs/1706.03762" in text
    assert "2. [FRONTIER] Agent Memory Consolidation at Scale (arxiv)\nWhy: Memory.\nhttps://arxiv.org/abs/2601.00002" in text
    assert buttons == [
        [("APPROVE 1", "prop:p1:approve"), ("REJECT 1", "prop:p1:reject")],
        [("APPROVE 2", "prop:p2:approve"), ("REJECT 2", "prop:p2:reject")],
    ]


def test_next_classic_skips_seen_and_already_proposed(fabric):
    table, _ssm, _s3 = fabric
    classics = [
        {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "year": 2017},
        {"title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models", "url": "https://arxiv.org/abs/2201.11903", "year": 2022},
        {"title": "ReAct: Synergizing Reasoning and Acting in Language Models", "url": "https://arxiv.org/abs/2210.03629", "year": 2022},
    ]
    table.put_item(
        Item={
            "experiment_id": "seen_paper#arxiv:1706.03762",
            "sk": "paper",
            "url": classics[0]["url"],
            "title": classics[0]["title"],
            "source": "classic",
            "track": "classic",
            "first_seen": "2026-08-01T10:00:00.000000Z",
            "picked": True,
        }
    )
    file_proposal(table, "p-cot", classics[1]["title"], "w", classics[1]["url"], "d", "new_hypothesis")
    assert next_classic(table, classics)["title"].startswith("ReAct")
    assert next_classic(table, classics[:2]) is None


def test_next_classic_matches_a_proposal_filed_with_a_clipped_title(fabric):
    # run_propose files a classic with its title clipped to 80 characters.
    # docs/classics.json has a 92-character title (Switch Transformers), so a
    # rejected or ignored one would come back every day if the clip were not
    # part of the comparison.
    table, _ssm, _s3 = fabric
    long_title = "Switch Transformers: Scaling to Trillion Parameter Models with Simple and Efficient Sparsity"
    classics = [
        {"title": long_title, "url": "https://arxiv.org/abs/2101.03961", "year": 2021},
        {"title": "Short Classic", "url": "https://example.com/short", "year": 2020},
    ]
    file_proposal(table, "p-switch", long_title[:80], "w", classics[0]["url"], "d", "new_hypothesis")
    assert next_classic(table, classics)["title"] == "Short Classic"


def test_run_propose_files_classic_first_then_fresh_and_uploads(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    captured = _capture_complete(monkeypatch)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 3
    assert captured["model"] == DEFAULT_PROPOSER_MODEL
    prompt = captured["messages"][1]["content"]
    assert "github.com" not in prompt
    assert "Daniel wants the single best new paper" in prompt  # docs/interests.md seed
    assert "Pick at most 2 lessons" in prompt

    items = sorted(
        (i for i in table.scan()["Items"] if i.get("sk") == "proposal"),
        key=lambda i: i["pid"],
    )
    assert len(items) == 3
    classic = next(i for i in items if i["lens"] == "foundational")
    assert classic["title"] == "Attention Is All You Need"
    assert classic["source_type"] == "classic"
    assert classic["why"] == "Foundational paper from 2017. Everyone in the field builds on it."
    assert classic["profile_version"] == "seed"
    fresh = [i for i in items if i["lens"] != "foundational"]
    assert {i["lens"] for i in fresh} == {"implement", "frontier"}
    assert all(i["source_type"] == "arxiv" for i in fresh)
    assert all(i["status"] == "PROPOSED" for i in items)

    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendMessage")
    text = kwargs["json"]["text"]
    assert text.startswith("Today's lessons. Tap to decide.\n\n1. [FOUNDATIONAL] Attention Is All You Need (classic)\n")
    assert "Why: You asked for self-improvement loops" in text
    rows = kwargs["json"]["reply_markup"]["inline_keyboard"]
    assert len(rows) == 3
    for row in rows:
        assert [b["callback_data"].split(":")[2] for b in row] == ["approve", "reject"]

    for item in items:
        body = json.loads(
            s3.get_object(Bucket=BUCKET, Key=f"proposals/{item['pid']}/proposal.json")["Body"].read()
        )
        assert body["proposal"]["title"] == item["title"]
        assert body["profile_version"] == "seed"

    day = json.loads(
        s3.get_object(Bucket=BUCKET, Key="proposals/days/2026-08-19/sources.json")["Body"].read()
    )
    assert day["date"] == "2026-08-19"
    assert day["profile_version"] == "seed"
    assert [c["title"] for c in day["candidates"]] == [s["title"] for s in FAKE_SOURCES[1:]]
    assert [c["lens"] for c in day["chosen"]] == ["foundational", "implement", "frontier"]


def test_run_propose_reads_the_profile_pointer(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    write_profile(s3, BUCKET, "profile/v7.md", "PROFILE V7 TEXT")
    set_pointer(table, "v7", "profile/v7.md", None, "consolidate", {}, NOW)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    captured = _capture_complete(monkeypatch)

    run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert "PROFILE V7 TEXT" in captured["messages"][1]["content"]
    items = [i for i in table.scan()["Items"] if i.get("sk") == "proposal"]
    assert all(i["profile_version"] == "v7" for i in items)


def test_run_propose_without_classics_fills_every_slot_with_fresh(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "load_classics", list)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    captured = _capture_complete(monkeypatch)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 2
    assert "Pick at most 3 lessons" in captured["messages"][1]["content"]
    assert not any(i.get("lens") == "foundational" for i in table.scan()["Items"])


def test_run_propose_sends_only_the_classic_when_sources_are_empty(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", list)

    def exploding_complete(model, messages):
        raise AssertionError("_complete must not be called without sources")

    monkeypatch.setattr(proposer_mod, "_complete", exploding_complete)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 1
    text = telegram_calls[0][1]["json"]["text"]
    assert "[FOUNDATIONAL] Attention Is All You Need (classic)" in text


def test_run_propose_no_sources_and_no_classic_says_so(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "load_classics", list)
    monkeypatch.setattr(proposer_mod, "gather", list)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 0
    assert "no fresh sources" in telegram_calls[0][1]["json"]["text"]


def test_run_propose_respects_daily_cap(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    for i in range(DAILY_CAP):
        file_proposal(table, f"prop-precap-{i}", f"Title {i}", "Headline.", "https://x", "d", "new_hypothesis")

    def exploding_complete(model, messages):
        raise AssertionError("_complete must not be called when the daily cap is hit")

    monkeypatch.setattr(proposer_mod, "_complete", exploding_complete)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 0
    assert len(telegram_calls) == 1
    assert "daily cap" in telegram_calls[0][1]["json"]["text"]


def test_run_propose_queues_ping_when_notify_fails(fabric, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    _capture_complete(monkeypatch)

    def exploding_notify(*args, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(proposer_mod, "notify", exploding_notify)

    count = run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert count == 3
    pending = [i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION]
    assert len(pending) == 1
    assert "Today's lessons" in json.loads(pending[0]["payload"])["text"]


def test_parse_proposals_reads_a_fenced_array_after_prose():
    """The real Sonnet reply of 2026-10-04: reasoning prose, then a fenced
    JSON array. The old parser read from the first bracket to the end of the
    text, choked on the closing fence, and dropped both picks."""
    from pathlib import Path

    raw = (Path(__file__).parent / "fixtures" / "proposer_reply_prose_and_fence.txt").read_text(encoding="utf-8")

    picks = parse_proposals(raw)

    assert len(picks) == 2
    assert all("arxiv.org/abs/" in p["citation"] for p in picks)


def test_parse_proposals_reads_a_bare_array_between_prose():
    raw = (
        'Here are the picks [two of them]:\n'
        '[{"title": "T", "why": "W", "citation": "https://x", "distance": "d", "lens": "implement"}]\n'
        "Hope that helps."
    )

    assert [p["lens"] for p in parse_proposals(raw)] == ["implement"]


def test_run_propose_logs_a_reply_that_yields_no_picks(fabric, telegram_calls, monkeypatch, capsys):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    _capture_complete(monkeypatch, output="I could not find anything worth proposing today.")

    run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    assert "model reply had no valid proposals" in capsys.readouterr().out


def test_run_propose_files_one_classic_per_day(fabric, telegram_calls, monkeypatch):
    table, ssm, s3 = fabric
    _daytime(monkeypatch)
    monkeypatch.setattr(proposer_mod, "gather", lambda: list(FAKE_SOURCES))
    _capture_complete(monkeypatch, output="no picks")
    run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)
    _capture_complete(monkeypatch)

    run_propose(table, ssm, s3, BUCKET, DEFAULT_PROPOSER_MODEL, now=NOW)

    lenses = [i["lens"] for i in table.scan()["Items"] if i.get("sk") == "proposal"]
    assert lenses.count("foundational") == 1
    assert len(lenses) == 3
