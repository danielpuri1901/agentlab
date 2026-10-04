"""Episode derivation is pure: ledger dicts in, Episode objects out.

Silence is derived from timestamps (Daniel's ruling 2026-10-02), so the
48 hour and 72 hour edges are the heart of this module.
"""

from datetime import UTC, datetime
from decimal import Decimal

import boto3
import pytest
from moto import mock_aws

from agentlab.episodes import (
    Episode,
    canonical_rating,
    derive_episodes,
    is_held_out,
    load_golden,
    parse_ts,
    proposal_display_status,
    scan_all,
    source_type_from_url,
    split,
    weekly_stats,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _proposal(pid, status, created, title="Paper", citation="https://arxiv.org/abs/2501.00001", **extra):
    item = {
        "experiment_id": f"proposal#{pid}",
        "sk": "proposal",
        "pid": pid,
        "status": status,
        "title": title,
        "citation": citation,
        "created_ts": created,
    }
    item.update(extra)
    return item


def _video(key, rating, sent, title="Video paper", url="https://arxiv.org/abs/2501.00002", track="core", **extra):
    item = {
        "experiment_id": f"video#{key}",
        "sk": "video",
        "track": track,
        "url": url,
        "title": title,
        "sent_ts": sent,
        "rating": rating,
        "rating_ts": None,
        "attempts": Decimal(2),
    }
    item.update(extra)
    return item


def test_parse_ts_accepts_both_ledger_shapes():
    assert parse_ts("2026-09-19T08:30:00Z") == datetime(2026, 9, 19, 8, 30, tzinfo=UTC)
    assert parse_ts("2026-09-24T07:34:22.123456Z") == datetime(
        2026, 9, 24, 7, 34, 22, 123456, tzinfo=UTC
    )


def test_source_type_from_url():
    assert source_type_from_url("https://arxiv.org/abs/2501.00001") == "arxiv"
    assert source_type_from_url("https://huggingface.co/apple/LensVLM-9B") == "hf"
    assert source_type_from_url("https://news.ycombinator.com/item?id=1") == "hn"
    assert source_type_from_url("https://github.com/langchain-ai/langgraph/releases/tag/1.2.12") == "github"
    assert source_type_from_url("https://danluu.com/agent-tests/") == "blog"
    assert source_type_from_url("") == "blog"


def test_canonical_rating_maps_daniels_words():
    assert canonical_rating("yes") == "COOL"
    assert canonical_rating("Yeah, .") == "COOL"
    assert canonical_rating("IMPLEMENT") == "COOL"
    assert canonical_rating("learned") == "MEH"
    assert canonical_rating("no") == "SKIP"
    assert canonical_rating("seems cool.implement") == "COOL"
    assert canonical_rating(None) is None
    assert canonical_rating("whatever") is None


def test_proposal_display_status_marks_old_untapped_as_ignored():
    fresh = _proposal("p1", "PROPOSED", "2026-10-04T09:30:00.000000Z")
    old = _proposal("p2", "PROPOSED", "2026-10-03T11:59:00.000000Z")
    edge = _proposal("p3", "PROPOSED", "2026-10-03T12:00:00.000000Z")
    assert proposal_display_status(fresh, NOW) == "PROPOSED"
    assert proposal_display_status(old, NOW) == "IGNORED"
    assert proposal_display_status(edge, NOW) == "IGNORED"
    assert proposal_display_status(_proposal("p4", "APPROVED", "2026-10-01T09:30:00.000000Z"), NOW) == "APPROVED"


def test_derive_episodes_every_row_of_the_table():
    items = [
        _proposal("a", "APPROVED", "2026-10-01T09:30:00.000000Z", verdict_ts="2026-10-01T10:00:00.000000Z", why="For you"),
        _proposal("r", "REJECTED", "2026-10-01T09:30:00.000000Z", citation="https://github.com/x/y/releases/tag/1"),
        _proposal("i", "PROPOSED", "2026-10-02T09:30:00.000000Z"),
        _proposal("f", "PROPOSED", "2026-10-05T09:30:00.000000Z"),
        _video("v-cool", "COOL", "2026-10-01T10:30:00.000000Z", pid="a"),
        _video("v-meh", "MEH", "2026-10-01T10:30:00.000000Z"),
        _video("v-skip", "SKIP", "2026-10-01T10:30:00.000000Z"),
        _video("v-unrated", None, "2026-10-01T10:30:00.000000Z"),
        _video("v-fresh", None, "2026-10-04T10:30:00.000000Z"),
        _video("v-classic", "COOL", "2026-10-01T10:30:00.000000Z", track="classic"),
    ]
    golden = [
        {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "rating": "COOL", "why": "GOAT"},
        {"title": "Constitutional AI", "url": None, "rating": "SKIP", "why": "not catchy"},
        {"title": "Eureka", "url": None, "label": "LEARNED", "why": "frame only"},
    ]
    episodes = derive_episodes(items, golden, NOW)
    by_kind = {e.kind: e for e in episodes}
    assert set(by_kind) == {
        "approved", "rejected", "ignored", "cool", "meh", "skip", "unrated",
        "golden_yes", "golden_no", "golden_meh",
    }
    assert by_kind["approved"].weight == 1.0
    assert by_kind["approved"].pid == "a"
    assert by_kind["approved"].why == "For you"
    assert by_kind["approved"].source_type == "arxiv"
    assert by_kind["rejected"].weight == -1.0
    assert by_kind["rejected"].source_type == "github"
    assert by_kind["ignored"].weight == -0.5
    cool_arxiv = next(e for e in episodes if e.kind == "cool" and e.source_type == "arxiv")
    assert cool_arxiv.weight == 1.0
    assert cool_arxiv.pid == "a"
    assert by_kind["meh"].weight == -0.5
    assert by_kind["skip"].weight == -1.0
    assert by_kind["unrated"].weight == -0.5
    assert by_kind["golden_yes"].weight == 1.0
    assert by_kind["golden_yes"].source_type == "classic"
    assert by_kind["golden_no"].weight == -1.0
    assert by_kind["golden_meh"].weight == -0.5
    classic_cools = [e for e in episodes if e.kind == "cool" and e.source_type == "classic"]
    assert len(classic_cools) == 1
    assert all(e.identity for e in episodes)


def test_derive_episodes_halves_stale_weights():
    items = [
        _proposal("old", "APPROVED", "2026-07-01T09:30:00.000000Z", verdict_ts="2026-07-01T10:00:00.000000Z"),
        _proposal("edge", "APPROVED", "2026-08-06T12:00:00.000000Z", verdict_ts="2026-08-06T12:00:00.000000Z"),
        _proposal("new", "APPROVED", "2026-10-01T09:30:00.000000Z", verdict_ts="2026-10-01T10:00:00.000000Z"),
    ]
    weights = {e.title + e.ts: e.weight for e in derive_episodes(items, [], NOW)}
    assert sorted(weights.values()) == [0.5, 0.5, 1.0]


def test_derive_episodes_uses_stored_source_type_and_classic_track():
    items = [
        _proposal("c", "APPROVED", "2026-10-01T09:30:00.000000Z", source_type="classic"),
    ]
    episodes = derive_episodes(items, [], NOW)
    assert episodes[0].source_type == "classic"


def test_split_is_stable_and_keeps_golden_in_train():
    episodes = [
        Episode(identity=f"arxiv:2501.{i:05d}", title=f"T{i}", url="", source_type="arxiv", kind="approved", weight=1.0, ts="2026-10-01T10:00:00.000000Z")
        for i in range(200)
    ] + [
        Episode(identity="title:golden", title="Golden", url="", source_type="classic", kind="golden_yes", weight=1.0, ts="")
    ]
    train, held = split(episodes)
    train_again, held_again = split(episodes)
    assert [e.identity for e in train] == [e.identity for e in train_again]
    assert [e.identity for e in held] == [e.identity for e in held_again]
    assert 40 <= len(held) <= 80
    assert any(e.kind == "golden_yes" for e in train)
    assert not any(e.golden for e in held)
    assert all(is_held_out(e.identity) for e in held)


def test_weekly_stats_counts_and_rates():
    items = [
        _proposal("a", "APPROVED", "2026-09-28T09:30:00.000000Z", verdict_ts="2026-09-28T10:00:00.000000Z"),
        _proposal("r", "REJECTED", "2026-09-28T09:30:00.000000Z", verdict_ts="2026-09-28T10:00:00.000000Z", citation="https://github.com/x/y/releases/tag/1"),
        _proposal("i", "PROPOSED", "2026-09-29T09:30:00.000000Z"),
        _video("v1", "COOL", "2026-09-28T10:30:00.000000Z"),
        _video("v2", None, "2026-09-28T10:30:00.000000Z"),
    ]
    golden = [{"title": "G", "url": None, "rating": "COOL", "why": ""}]
    stats = weekly_stats(derive_episodes(items, golden, NOW))
    week = stats["weeks"][0]
    assert week["week"] == "2026-W40"
    assert week["approved"] == 1 and week["rejected"] == 1 and week["ignored"] == 1
    assert week["approval_rate"] == pytest.approx(1 / 3)
    assert week["cool_rate"] == pytest.approx(0.5)
    assert stats["sources"]["arxiv"] == {"approved": 1, "decided": 2, "approval_rate": 0.5}
    assert stats["sources"]["github"] == {"approved": 0, "decided": 1, "approval_rate": 0.0}
    assert stats["totals"] == {"positives": 2, "negatives": 3, "golden": 1}


def test_load_golden_skips_bad_lines(tmp_path):
    path = tmp_path / "golden.jsonl"
    path.write_text(
        '{"title": "A", "url": null, "rating": "COOL", "why": "x"}\n'
        "not json\n"
        '{"title": "B", "label": "weird"}\n'
        '{"title": "C", "label": "LEARNED", "why": "y"}\n',
        encoding="utf-8",
    )
    assert [g["title"] for g in load_golden(path)] == ["A", "C"]
    assert load_golden(tmp_path / "missing.jsonl") == []


def test_scan_all_reads_every_page(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        table = boto3.resource("dynamodb", region_name="us-east-1").create_table(
            TableName="t",
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
        for i in range(5):
            table.put_item(Item={"experiment_id": f"x#{i}", "sk": "y"})
        pages = []
        real_scan = table.scan

        def paged_scan(**kwargs):
            kwargs["Limit"] = 2
            response = real_scan(**kwargs)
            pages.append(len(response["Items"]))
            return response

        monkeypatch.setattr(table, "scan", paged_scan)
        assert len(scan_all(table)) == 5
        assert len(pages) >= 3


def test_an_unrated_operator_test_run_is_not_a_taste_signal():
    sent = "2026-10-01T10:30:00.000000Z"
    items = [
        _video("v-test", None, sent, feedback_status="operator-replay"),
        _video("v-approved", None, sent, feedback_status="operator-replay", pid="p1"),
        _video("v-rated", "COOL", sent, feedback_status="operator-replay"),
    ]

    kinds = sorted(e.kind for e in derive_episodes(items, [], NOW))

    assert kinds == ["cool", "unrated"]
