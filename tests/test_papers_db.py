"""Paper identity and seen-store module tests, fully offline with moto."""

from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from agentlab import papers_db as papers_db_mod
from agentlab.papers_db import (
    is_seen,
    mark_seen,
    normalize_title,
    paper_identity,
    recent_seen_titles,
)

TABLE = "agentlab-state-test"
REGION = "us-east-1"


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
        yield table, None


# --- paper_identity: arXiv id extraction ---


def test_identity_arxiv_abs_url():
    assert (
        paper_identity("https://arxiv.org/abs/2607.07663", "Some Title")
        == "arxiv:2607.07663"
    )


def test_identity_arxiv_pdf_url_with_version():
    assert (
        paper_identity("https://arxiv.org/pdf/2607.07663v2", "Some Title")
        == "arxiv:2607.07663"
    )


def test_identity_arxiv_html_url_with_version():
    assert (
        paper_identity("https://arxiv.org/html/2607.07663v1", "Some Title")
        == "arxiv:2607.07663"
    )


def test_identity_arxiv_http_abs_url_with_version():
    assert (
        paper_identity("http://arxiv.org/abs/2608.19047v1", "Some Title")
        == "arxiv:2608.19047"
    )


def test_identity_non_arxiv_url_falls_back_to_title():
    identity = paper_identity("https://example.com/paper", "A Great Paper: Results!")
    assert identity == "title:" + normalize_title("A Great Paper: Results!")
    assert identity.startswith("title:")
    assert "arxiv" not in identity


# --- normalize_title ---


def test_normalize_title_collapses_case_punctuation_whitespace():
    assert (
        normalize_title("  The   Paper's Title!!  Results?  ")
        == "the papers title results"
    )


def test_normalize_title_equivalent_variants_match():
    a = normalize_title("Attention Is All You Need")
    b = normalize_title("attention is all you need!!!")
    c = normalize_title("  Attention   is  ALL you need  ")
    assert a == b == c


# --- fuzzy title matching via is_seen ---


def test_is_seen_fuzzy_hit_above_threshold(fabric):
    table, _ = fabric
    identity = paper_identity("https://example.com/x", "Scaling Laws for Neural Language Models")
    # A near-duplicate title (ratio ~0.93, above the 0.92 threshold) that was
    # never exactly recorded.
    near_dup_title = "Scaling Laws for Large Neural Language Models"
    assert (
        is_seen(table, identity, fuzzy_titles=[near_dup_title]) is True
    )


def test_is_seen_fuzzy_miss_below_threshold(fabric):
    table, _ = fabric
    identity = paper_identity("https://example.com/x", "Scaling Laws for Neural Language Models")
    # A title similar but dissimilar enough (ratio ~0.85, below the 0.92
    # threshold) to miss.
    dissimilar_title = "Scaling Behavior for Neural Language Models"
    assert (
        is_seen(table, identity, fuzzy_titles=[dissimilar_title]) is False
    )


def test_is_seen_arxiv_identity_ignores_fuzzy_titles(fabric):
    table, _ = fabric
    identity = paper_identity("https://arxiv.org/abs/2607.07663", "Some Title")
    # arXiv identities never fall back to fuzzy matching, even with a
    # perfect-ratio decoy in fuzzy_titles.
    assert is_seen(table, identity, fuzzy_titles=["Some Title"]) is False


# --- moto roundtrip: mark_seen / is_seen ---


def test_mark_seen_then_is_seen_true(fabric):
    table, _ = fabric
    identity = paper_identity("https://arxiv.org/abs/2607.07663", "A Paper")
    assert is_seen(table, identity) is False
    mark_seen(
        table,
        identity,
        url="https://arxiv.org/abs/2607.07663",
        title="A Paper",
        source="arxiv",
        track="fresh",
    )
    assert is_seen(table, identity) is True


def test_unseen_paper_is_not_seen(fabric):
    table, _ = fabric
    identity = paper_identity("https://arxiv.org/abs/9999.99999", "Never Seen")
    assert is_seen(table, identity) is False


def test_mark_seen_item_shape(fabric):
    table, _ = fabric
    identity = "arxiv:2607.07663"
    mark_seen(
        table,
        identity,
        url="https://arxiv.org/abs/2607.07663",
        title="A Paper",
        source="arxiv",
        track="fresh",
    )
    item = table.get_item(
        Key={"experiment_id": f"seen_paper#{identity}", "sk": "paper"}
    )["Item"]
    assert item["url"] == "https://arxiv.org/abs/2607.07663"
    assert item["title"] == "A Paper"
    assert item["source"] == "arxiv"
    assert item["track"] == "fresh"
    assert item["picked"] is True
    assert "first_seen" in item


# --- recent_seen_titles ---


def test_recent_seen_titles_newest_first(fabric, monkeypatch):
    table, _ = fabric
    monkeypatch.setattr(
        papers_db_mod, "now", lambda: datetime(2026, 8, 20, 9, 0, tzinfo=UTC)
    )
    mark_seen(
        table, "arxiv:1111.11111", url="https://arxiv.org/abs/1111.11111",
        title="First Paper", source="arxiv", track="fresh",
    )

    monkeypatch.setattr(
        papers_db_mod, "now", lambda: datetime(2026, 8, 21, 9, 0, tzinfo=UTC)
    )
    mark_seen(
        table, "arxiv:2222.22222", url="https://arxiv.org/abs/2222.22222",
        title="Second Paper", source="arxiv", track="fresh",
    )

    monkeypatch.setattr(
        papers_db_mod, "now", lambda: datetime(2026, 8, 22, 9, 0, tzinfo=UTC)
    )
    mark_seen(
        table, "arxiv:3333.33333", url="https://arxiv.org/abs/3333.33333",
        title="Third Paper", source="arxiv", track="fresh",
    )

    titles = recent_seen_titles(table, limit=200)
    assert titles == ["Third Paper", "Second Paper", "First Paper"]


def test_recent_seen_titles_respects_limit(fabric, monkeypatch):
    table, _ = fabric
    for i, ts_day in enumerate([20, 21, 22], start=1):
        monkeypatch.setattr(
            papers_db_mod, "now", lambda d=ts_day: datetime(2026, 8, d, 9, 0, tzinfo=UTC)
        )
        mark_seen(
            table, f"arxiv:{i}111.1111{i}", url=f"https://arxiv.org/abs/{i}111.1111{i}",
            title=f"Paper {i}", source="arxiv", track="fresh",
        )
    titles = recent_seen_titles(table, limit=2)
    assert titles == ["Paper 3", "Paper 2"]


def test_non_ascii_titles_do_not_collide():
    from agentlab.papers_db import paper_identity

    a = paper_identity("https://example.com/a", "深層学習の新手法")
    b = paper_identity("https://example.com/b", "強化学習と探索")
    assert a != b
    assert a.startswith("title:x") and b.startswith("title:x")


def test_release_after_failure_gives_a_paper_one_more_chance(fabric):
    table, _ = fabric
    papers_db_mod.mark_seen(table, "id-1", "https://x/1", "Paper One", "arxiv", "core")

    assert papers_db_mod.release_after_failure(table, "id-1") is True
    assert papers_db_mod.is_seen(table, "id-1") is False


def test_release_after_failure_keeps_a_paper_that_failed_twice(fabric):
    table, _ = fabric
    papers_db_mod.mark_seen(table, "id-1", "https://x/1", "Paper One", "arxiv", "core")
    papers_db_mod.release_after_failure(table, "id-1")
    papers_db_mod.mark_seen(table, "id-1", "https://x/1", "Paper One", "arxiv", "core")

    assert papers_db_mod.release_after_failure(table, "id-1") is False
    assert papers_db_mod.is_seen(table, "id-1") is True
