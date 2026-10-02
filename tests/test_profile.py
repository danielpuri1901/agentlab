"""The taste profile: versioned text in S3 behind one DynamoDB pointer item."""

from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from agentlab.profile import (
    POINTER_KEY,
    ProfilePointer,
    candidate_key,
    diff_bullets,
    get_pointer,
    load_profile_text,
    profile_key,
    render_profile,
    set_pointer,
    source_weights_section,
    validate_profile,
    version_now,
    write_profile,
)

TABLE = "agentlab-state-test"
BUCKET = "agentlab-results-test"
REGION = "us-east-1"
NOW = datetime(2026, 10, 5, 16, 0, 0, tzinfo=UTC)

GOOD_PROFILE = """# Daniel's taste profile
version: x

## Prefer
- Real papers on agent harnesses (evidence: 5 approved, 3 COOL)
- Foundational papers everyone cites (evidence: 4 COOL)
- Posts with measurements on agent evals (evidence: 2 approved)

## Avoid
- SDK release notes and changelogs (evidence: 10 rejected)
- Product throughput posts (evidence: 2 rejected, 3 ignored)
- Random HN links with no paper behind them (evidence: 4 ignored)

## Positive examples
- Cache-to-Cache: Direct Semantic Communication Between Large Language Models (cool, arxiv)
- Chain-of-Thought Prompting Elicits Reasoning in Large Language Models (cool, classic)
- Procedural Graphs: Self-Evolving Execution Structures for LLM Agents (cool, arxiv)
- Totally Made Up Paper That Never Existed (approved, arxiv)

## Negative examples
- LangGraph 1.2.12 Determinism Against Concurrent Tool Binding (rejected, github)
- Mercury 2.5 Throughput Variance in Tool Binding Latency (rejected, blog)
- Historical Document Decoding with LLM Agent Tracing Chains (rejected, blog)

## Source weights
- the model wrote something here that code replaces
"""

TRAIN_TITLES = [
    "Cache-to-Cache: Direct Semantic Communication Between Large Language Models",
    "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
    "Procedural Graphs: Self-Evolving Execution Structures for LLM Agents",
    "LangGraph 1.2.12 Determinism Against Concurrent Tool Binding",
    "Mercury 2.5 Throughput Variance in Tool Binding Latency",
    "Historical Document Decoding with LLM Agent Tracing Chains",
]


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
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        yield table, s3


def test_version_and_keys():
    assert version_now(NOW) == "20261005T160000Z"
    assert profile_key("v1") == "profile/v1.md"
    assert candidate_key("v1") == "profile/candidates/v1.md"


def test_pointer_round_trip_and_flip(fabric):
    table, _s3 = fabric
    assert get_pointer(table) is None
    first = set_pointer(table, "v1", "profile/v1.md", None, "consolidate", {"new_f1": 0.5}, NOW)
    assert first.previous_version is None
    stored = get_pointer(table)
    assert stored == ProfilePointer(
        version="v1",
        s3_key="profile/v1.md",
        previous_version=None,
        previous_s3_key=None,
        applied_ts="2026-10-05T16:00:00.000000Z",
        source="consolidate",
        eval={"new_f1": 0.5},
    )
    second = set_pointer(table, "v2", "profile/v2.md", stored, "consolidate", {}, NOW)
    assert second.previous_version == "v1"
    assert second.previous_s3_key == "profile/v1.md"
    item = table.get_item(Key=POINTER_KEY)["Item"]
    assert item["version"] == "v2"
    assert item["previous_s3_key"] == "profile/v1.md"


def test_load_profile_text_prefers_pointer_and_falls_back(fabric, tmp_path):
    table, s3 = fabric
    seed = tmp_path / "interests.md"
    seed.write_text("seed text", encoding="utf-8")
    assert load_profile_text(table, s3, BUCKET, seed) == ("seed text", "seed")
    write_profile(s3, BUCKET, "profile/v1.md", "profile v1")
    set_pointer(table, "v1", "profile/v1.md", None, "consolidate", {}, NOW)
    assert load_profile_text(table, s3, BUCKET, seed) == ("profile v1", "v1")


def test_load_profile_text_falls_back_when_the_object_is_missing(fabric, tmp_path):
    table, s3 = fabric
    seed = tmp_path / "interests.md"
    seed.write_text("seed text", encoding="utf-8")
    set_pointer(table, "v9", "profile/v9.md", None, "consolidate", {}, NOW)
    assert load_profile_text(table, s3, BUCKET, seed) == ("seed text", "seed")


def test_validate_profile_accepts_and_cleans():
    sections, problems = validate_profile(GOOD_PROFILE, TRAIN_TITLES)
    assert problems == []
    assert list(sections) == ["Prefer", "Avoid", "Positive examples", "Negative examples", "Source weights"]
    assert "Totally Made Up Paper" not in sections["Positive examples"]
    assert "Cache-to-Cache" in sections["Positive examples"]
    assert sections["Prefer"].count("\n- ") + sections["Prefer"].startswith("- ") == 3


def test_validate_profile_replaces_em_dashes():
    dashed = GOOD_PROFILE.replace("SDK release notes and changelogs", "SDK release notes \u2014 changelogs")
    sections, problems = validate_profile(dashed, TRAIN_TITLES)
    assert problems == []
    assert "\u2014" not in sections["Avoid"]
    assert "SDK release notes - changelogs" in sections["Avoid"]


def test_validate_profile_strips_fences_and_leading_text():
    wrapped = "Here is the profile:\n```markdown\n" + GOOD_PROFILE + "\n```\n"
    sections, problems = validate_profile(wrapped, TRAIN_TITLES)
    assert problems == []
    assert sections is not None


def test_validate_profile_rejects_missing_section_and_short_lists():
    missing = GOOD_PROFILE.replace("## Avoid", "## Dislike")
    sections, problems = validate_profile(missing, TRAIN_TITLES)
    assert sections is None
    assert any("Avoid" in p for p in problems)

    too_few = GOOD_PROFILE.replace("- Posts with measurements on agent evals (evidence: 2 approved)\n", "")
    sections, problems = validate_profile(too_few, TRAIN_TITLES)
    assert sections is None
    assert any("Prefer" in p for p in problems)

    hallucinated = GOOD_PROFILE.replace("Cache-to-Cache: Direct Semantic Communication Between Large Language Models", "Nope One").replace(
        "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models", "Nope Two"
    )
    sections, problems = validate_profile(hallucinated, TRAIN_TITLES)
    assert sections is None
    assert any("Positive examples" in p for p in problems)


def test_validate_profile_rejects_too_many_words():
    long_text = GOOD_PROFILE.replace("## Prefer\n", "## Prefer\n- " + "word " * 1300 + "\n")
    sections, problems = validate_profile(long_text, TRAIN_TITLES)
    assert sections is None
    assert any("words" in p for p in problems)


def test_render_and_source_weights():
    stats = {
        "weeks": [],
        "sources": {
            "arxiv": {"approved": 12, "decided": 15, "approval_rate": 0.8},
            "github": {"approved": 2, "decided": 12, "approval_rate": 2 / 12},
            "blog": {"approved": 0, "decided": 0, "approval_rate": None},
        },
        "totals": {"positives": 0, "negatives": 0, "golden": 0},
    }
    body = source_weights_section(stats)
    assert "- arxiv: approval 0.80 (12 of 15)" in body
    assert "- github: approval 0.17 (2 of 12)" in body
    assert "- blog: no decisions yet" in body
    sections, _ = validate_profile(GOOD_PROFILE, TRAIN_TITLES)
    sections["Source weights"] = body
    text = render_profile("v1", sections)
    assert text.startswith("# Daniel's taste profile\nversion: v1\n\n## Prefer\n")
    assert text.rstrip().endswith("(0 of 0)") or "no decisions yet" in text
    assert "## Source weights\n- arxiv: approval 0.80 (12 of 15)" in text


def test_diff_bullets_reports_added_and_removed():
    new = GOOD_PROFILE.replace(
        "- Random HN links with no paper behind them (evidence: 4 ignored)",
        "- Robotics demos without a method (evidence: 2 ignored)",
    )
    diff = diff_bullets(GOOD_PROFILE, new)
    assert diff["Prefer"] == {"added": [], "removed": []}
    assert diff["Avoid"]["added"] == ["Robotics demos without a method (evidence: 2 ignored)"]
    assert diff["Avoid"]["removed"] == ["Random HN links with no paper behind them (evidence: 4 ignored)"]
