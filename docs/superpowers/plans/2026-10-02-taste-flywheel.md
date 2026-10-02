# Taste Flywheel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every tap, rating, and silence from Daniel rewrites a taste profile that the proposer and the video picker read, so the next day's picks get better and SDK release notes stop showing up.

**Architecture:** Episodes are derived at read time from the existing DynamoDB ledger (`episodes.py`). A versioned profile text lives in S3 behind a DynamoDB pointer (`profile.py`). A weekly `worker consolidate` run rewrites the profile with a model and only swaps it in when a held-out probe eval does not get worse (`taste_eval.py`, `consolidate.py`). The proposer picks three lessons a day with the profile in its prompt, one of them a foundational classic, and the approval webhook links the video it starts back to the proposal.

**Tech Stack:** Python 3.12, uv, typer, boto3 + moto (tests), litellm on Bedrock (Claude Sonnet 4.6 and Haiku 4.5), Terraform for EventBridge Scheduler and IAM, a single-file Lambda for the Telegram webhook.

**Spec:** `docs/superpowers/specs/2026-10-02-taste-flywheel-design.md`

## Global Constraints

- Python 3.12. Run everything with `uv run ...` from the repo root of this worktree.
- All tests offline: moto for AWS, monkeypatched `_complete` for models, monkeypatched `httpx.post` for Telegram. Never call the network in a test.
- `uv run ruff check src tests scripts` must be clean and `uv run pytest -q` must be green before every commit.
- Telegram text, docs, and code comments: short sentences, no em dash ("—"), plain dash only.
- Markdown docs: one sentence per line.
- Never commit `infra/runtime.auto.tfvars`, `infra/image_tag.auto.tfvars`, or anything under `infra/.build`.
- Model id strings: consolidate default `bedrock/global.anthropic.claude-sonnet-4-6`, proposer default `bedrock/global.anthropic.claude-sonnet-4-6`, pick default stays `bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0`.
- Episode weights and thresholds are exactly the spec table: approved +1.0, rejected -1.0, ignored -0.5 after 48 hours, cool +1.0, meh -0.5, skip -1.0, unrated -0.5 after 72 hours, golden_yes +1.0, golden_meh -0.5, golden_no -1.0, times 0.5 when older than 60 days.
- Held-out rule: `sha1(identity)` as an integer modulo 10 is less than 3. Golden episodes are never held out.
- Swap rule: new F1 >= old F1 - 0.02 and new golden recall >= old golden recall.
- Commit messages: conventional prefix (`feat:`, `fix:`, `test:`, `docs:`, `chore:`), no co-author lines of any kind.
- Timestamps written to the ledger use `datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")`, the existing format.

## Review Focus

1. Ledger timestamps come in two shapes, `2026-09-19T08:30:00Z` and `2026-09-24T07:34:22.123456Z`. `parse_ts` must accept both. Test in Task 1.
2. DynamoDB returns `None` for NULL ratings and `Decimal` for numbers. `derive_episodes` must treat `rating=None` as unrated and never crash on `Decimal`. Test in Task 1.
3. The consolidation model may wrap its output in a ```` ```markdown ```` fence or add text before the first heading. `validate_profile` must strip fences and start at the first `## Prefer`. Test in Task 2.
4. When every classic is already seen or proposed, the proposer must fall back to three fresh picks and never crash. Test in Task 5.
5. A second REVERT tap, or a tap on a stale version, must answer "Nothing to revert" and leave the pointer unchanged. Test in Task 8.

## File structure

New files:

- `src/agentlab/repo_files.py`: paths to `docs/classics.json`, `docs/interests.md`, `docs/golden-papers.jsonl`.
- `src/agentlab/episodes.py`: `Episode`, `derive_episodes`, `proposal_display_status`, `split`, `weekly_stats`, `scan_all`, `load_golden`, `canonical_rating`, `source_type_from_url`, `parse_ts`.
- `src/agentlab/profile.py`: pointer item, S3 keys, `load_profile_text`, `validate_profile`, `render_profile`, `source_weights_section`, `diff_bullets`.
- `src/agentlab/taste_eval.py`: `probe_messages`, `parse_yes_no`, `evaluate`, `decide_swap`, `EvalResult`.
- `src/agentlab/consolidate.py`: `run_consolidate`, prompt, ping text.
- `scripts/build_golden_papers.py`: labeling sheet to jsonl.
- `docs/golden-papers.jsonl`: generated, committed.
- Tests: `tests/test_episodes.py`, `tests/test_profile.py`, `tests/test_taste_eval.py`, `tests/test_consolidate.py`, `tests/test_build_golden_papers.py`.

Modified files:

- `src/agentlab/sources.py`, `tests/test_sources.py`: GitHub fetcher gone, arXiv categories widened, `gather_proposer_pool`.
- `src/agentlab/proposals.py`, `src/agentlab/proposer.py`, `tests/test_proposer.py`: new fields, new prompt, classic slot, day record, message format.
- `src/agentlab/worker.py`, `src/agentlab/preferences.py`, `tests/test_worker.py`, `tests/test_preferences.py`: `consolidate` command, `PID` on videos, picker reads the profile, gated preference code removed.
- `infra/lambda/approvals_webhook.py`, `tests/test_approvals_webhook.py`: `PID` and `EXPLAIN_TITLE` overrides, `prof:` revert.
- `infra/scheduler.tf`, `infra/iam.tf`: weekly schedule, S3 and DynamoDB grants.
- `docs/classics.json`, `docs/golden-papers-labeling.md`: seven classics added, entry 26 renamed.

## Task waves

Tasks in the same wave touch disjoint files and can run in parallel in this worktree.

- Wave 1: Task 1 (episodes), Task 4 (sources), Task 8 (Lambda), Task 10 (infra).
- Wave 2: Task 2 (profile), Task 3 (taste_eval), Task 9 (golden converter and classics). All need Task 1.
- Wave 3: Task 5 (proposer), Task 6 (consolidate), Task 7 (worker). They need Tasks 1 to 4.
- Wave 4: Task 11 (integration and real path), done by the orchestrator.

---

### Task 1: Episodes derived from the ledger

**Files:**
- Create: `src/agentlab/repo_files.py`
- Create: `src/agentlab/episodes.py`
- Test: `tests/test_episodes.py`

**Interfaces:**
- Consumes: `agentlab.papers_db.paper_identity(url, title) -> str` (exists).
- Produces, used by Tasks 2, 3, 5, 6, 7, 9:
  - `Episode` frozen dataclass: `identity: str, title: str, url: str, source_type: str, kind: str, weight: float, ts: str, pid: str | None = None, why: str | None = None`, properties `positive` (weight > 0) and `golden` (kind starts with `golden_`).
  - `parse_ts(value: str) -> datetime` (UTC aware).
  - `source_type_from_url(url: str) -> str` returning `arxiv`, `hf`, `hn`, `github`, or `blog`.
  - `canonical_rating(raw) -> str | None` returning `COOL`, `MEH`, `SKIP`, or None.
  - `load_golden(path) -> list[dict]`.
  - `scan_all(table) -> list[dict]` (paginated scan).
  - `proposal_display_status(item: dict, now: datetime) -> str` returning `APPROVED`, `REJECTED`, `PROPOSED`, or `IGNORED`.
  - `derive_episodes(items: list[dict], golden: list[dict], now: datetime) -> list[Episode]`.
  - `is_held_out(identity: str) -> bool`, `split(episodes) -> tuple[list[Episode], list[Episode]]` (train, held_out).
  - `weekly_stats(episodes) -> dict` with keys `weeks` (list of dicts with `week`, counts per kind, `approval_rate`, `cool_rate`), `sources` (dict source_type -> `approved`, `decided`, `approval_rate`), `totals` (`positives`, `negatives`, `golden`).
  - `repo_files.REPO_ROOT`, `CLASSICS_PATH`, `INTERESTS_PATH`, `GOLDEN_PAPERS_PATH`.

- [ ] **Step 1: Write `src/agentlab/repo_files.py`**

```python
"""Paths to the data files that ship inside the worker images.

`docs/` lives at the repo root, not under src/agentlab/, so every path walks
up from this module (src/agentlab/repo_files.py -> src/agentlab -> src ->
repo root). Both Dockerfiles `COPY . .` the whole repo under /app, so the
same walk resolves inside the containers.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSICS_PATH = REPO_ROOT / "docs" / "classics.json"
INTERESTS_PATH = REPO_ROOT / "docs" / "interests.md"
GOLDEN_PAPERS_PATH = REPO_ROOT / "docs" / "golden-papers.jsonl"
```

- [ ] **Step 2: Write the failing tests `tests/test_episodes.py`**

```python
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
        _proposal("a", "APPROVED", "2026-10-01T09:30:00.000000Z", verdict_ts="2026-10-01T10:00:00.000000Z", pid="a", why="For you"),
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_episodes.py -q`
Expected: ImportError on `agentlab.episodes`.

- [ ] **Step 4: Write `src/agentlab/episodes.py`**

```python
"""Episodes: what Daniel did with each item, derived from the ledger.

Nothing here writes to DynamoDB. Silence is derived from timestamps at read
time (Daniel's ruling 2026-10-02: an untapped proposal or an unrated video
means "not interesting", a weak negative), so no status field ever has to
encode it. Weights follow the episode table in
docs/superpowers/specs/2026-10-02-taste-flywheel-design.md.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from agentlab.papers_db import paper_identity

IGNORE_AFTER = timedelta(hours=48)
UNRATED_AFTER = timedelta(hours=72)
STALE_AFTER = timedelta(days=60)
STALE_FACTOR = 0.5
HELD_OUT_BUCKETS = 3

PROPOSAL_KINDS = {"APPROVED": ("approved", 1.0), "REJECTED": ("rejected", -1.0)}
IGNORED_KIND = ("ignored", -0.5)
RATING_KINDS = {"COOL": ("cool", 1.0), "MEH": ("meh", -0.5), "SKIP": ("skip", -1.0)}
UNRATED_KIND = ("unrated", -0.5)
GOLDEN_KINDS = {
    "COOL": ("golden_yes", 1.0),
    "MEH": ("golden_meh", -0.5),
    "SKIP": ("golden_no", -1.0),
}
RATING_ALIASES = {
    "YES": "COOL",
    "YEAH": "COOL",
    "IMPLEMENT": "COOL",
    "LEARNED": "MEH",
    "NO": "SKIP",
}


@dataclass(frozen=True)
class Episode:
    identity: str
    title: str
    url: str
    source_type: str
    kind: str
    weight: float
    ts: str
    pid: str | None = None
    why: str | None = None

    @property
    def positive(self) -> bool:
        return self.weight > 0

    @property
    def golden(self) -> bool:
        return self.kind.startswith("golden_")


def parse_ts(value: str) -> datetime:
    """Accept both ledger shapes: with and without microseconds, Z suffix."""
    text = str(value or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def source_type_from_url(url: str) -> str:
    host = urlparse(str(url or "")).netloc.lower()
    if host.endswith("arxiv.org"):
        return "arxiv"
    if host.endswith("huggingface.co"):
        return "hf"
    if host.endswith("ycombinator.com"):
        return "hn"
    if host.endswith("github.com"):
        return "github"
    return "blog"


def canonical_rating(raw: object) -> str | None:
    """First word in the text that names a rating, through Daniel's aliases."""
    for token in re.findall(r"[a-z]+", str(raw or "").lower()):
        word = RATING_ALIASES.get(token.upper(), token.upper())
        if word in RATING_KINDS:
            return word
    return None


def load_golden(path: str | Path) -> list[dict]:
    source = Path(path)
    if not source.exists():
        return []
    items = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and canonical_rating(item.get("rating") or item.get("label")):
            items.append(item)
    return items


def scan_all(table) -> list[dict]:
    items: list[dict] = []
    start_key = None
    while True:
        request: dict = {}
        if start_key:
            request["ExclusiveStartKey"] = start_key
        response = table.scan(**request)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def proposal_display_status(item: dict, now: datetime) -> str:
    status = str(item.get("status") or "PROPOSED")
    if status == "PROPOSED" and now - parse_ts(item.get("created_ts")) >= IGNORE_AFTER:
        return "IGNORED"
    return status


def _age_factor(ts: str, now: datetime) -> float:
    return STALE_FACTOR if now - parse_ts(ts) >= STALE_AFTER else 1.0


def _proposal_episode(item: dict, now: datetime) -> Episode | None:
    status = proposal_display_status(item, now)
    if status == "PROPOSED":
        return None
    kind, weight = PROPOSAL_KINDS.get(status, IGNORED_KIND)
    ts = str(item.get("verdict_ts") or item.get("created_ts"))
    url = str(item.get("citation") or "")
    title = str(item.get("title") or "")
    source_type = str(item.get("source_type") or source_type_from_url(url))
    return Episode(
        identity=paper_identity(url, title),
        title=title,
        url=url,
        source_type=source_type,
        kind=kind,
        weight=weight * _age_factor(ts, now),
        ts=ts,
        pid=item.get("pid"),
        why=item.get("why"),
    )


def _video_episode(item: dict, now: datetime) -> Episode | None:
    rating = canonical_rating(item.get("rating"))
    sent_ts = str(item.get("sent_ts") or "")
    if rating is None:
        if now - parse_ts(sent_ts) < UNRATED_AFTER:
            return None
        kind, weight = UNRATED_KIND
        ts = sent_ts
    else:
        kind, weight = RATING_KINDS[rating]
        ts = str(item.get("rating_ts") or sent_ts)
    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    source_type = "classic" if item.get("track") == "classic" else source_type_from_url(url)
    return Episode(
        identity=paper_identity(url, title),
        title=title,
        url=url,
        source_type=source_type,
        kind=kind,
        weight=weight * _age_factor(ts, now),
        ts=ts,
        pid=item.get("pid"),
    )


def _golden_episode(item: dict) -> Episode | None:
    rating = canonical_rating(item.get("rating") or item.get("label"))
    if rating is None:
        return None
    kind, weight = GOLDEN_KINDS[rating]
    url = str(item.get("url") or "")
    title = str(item.get("title") or "")
    return Episode(
        identity=paper_identity(url, title),
        title=title,
        url=url,
        source_type="classic",
        kind=kind,
        weight=weight,
        ts="",
        why=item.get("why"),
    )


def derive_episodes(items: list[dict], golden: list[dict], now: datetime) -> list[Episode]:
    episodes: list[Episode] = []
    for item in items:
        sk = item.get("sk")
        episode = None
        if sk == "proposal":
            episode = _proposal_episode(item, now)
        elif sk == "video":
            episode = _video_episode(item, now)
        if episode is not None:
            episodes.append(episode)
    for item in golden:
        episode = _golden_episode(item)
        if episode is not None:
            episodes.append(episode)
    return episodes


def is_held_out(identity: str) -> bool:
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()  # noqa: S324 - a bucket hash, not security
    return int(digest, 16) % 10 < HELD_OUT_BUCKETS


def split(episodes: list[Episode]) -> tuple[list[Episode], list[Episode]]:
    """Training set and held-out set. Golden episodes always train."""
    train: list[Episode] = []
    held_out: list[Episode] = []
    for episode in episodes:
        if episode.golden or not is_held_out(episode.identity):
            train.append(episode)
        else:
            held_out.append(episode)
    return train, held_out


def iso_week(ts: str) -> str:
    year, week, _day = parse_ts(ts).isocalendar()
    return f"{year}-W{week:02d}"


_WEEK_COUNTS = ("approved", "rejected", "ignored", "cool", "meh", "skip", "unrated")


def weekly_stats(episodes: list[Episode]) -> dict:
    weeks: dict[str, dict] = {}
    sources: dict[str, dict] = {}
    for episode in episodes:
        if episode.golden:
            continue
        week = weeks.setdefault(iso_week(episode.ts), {kind: 0 for kind in _WEEK_COUNTS})
        week[episode.kind] += 1
        if episode.kind in ("approved", "rejected", "ignored"):
            source = sources.setdefault(episode.source_type, {"approved": 0, "decided": 0})
            source["decided"] += 1
            if episode.kind == "approved":
                source["approved"] += 1
    rows = []
    for name in sorted(weeks):
        counts = weeks[name]
        decided = counts["approved"] + counts["rejected"] + counts["ignored"]
        rated = counts["cool"] + counts["meh"] + counts["skip"] + counts["unrated"]
        rows.append(
            {
                "week": name,
                **counts,
                "approval_rate": counts["approved"] / decided if decided else None,
                "cool_rate": counts["cool"] / rated if rated else None,
            }
        )
    source_rows = {
        name: {
            **counts,
            "approval_rate": counts["approved"] / counts["decided"] if counts["decided"] else None,
        }
        for name, counts in sources.items()
    }
    live = [episode for episode in episodes if not episode.golden]
    totals = {
        "positives": sum(1 for episode in live if episode.positive),
        "negatives": sum(1 for episode in live if not episode.positive),
        "golden": sum(1 for episode in episodes if episode.golden),
    }
    return {"weeks": rows, "sources": source_rows, "totals": totals}
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_episodes.py -q`
Expected: all pass. If `test_split_is_stable_and_keeps_golden_in_train` fails on the 40 to 80 bound, print `len(held)` and check `is_held_out`; with 200 identities the expected count is about 60.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/agentlab/episodes.py src/agentlab/repo_files.py tests/test_episodes.py
git add src/agentlab/episodes.py src/agentlab/repo_files.py tests/test_episodes.py
git commit -m "feat: derive taste episodes from the ledger, silence included"
```

---

### Task 2: Versioned profile with a DynamoDB pointer

**Files:**
- Create: `src/agentlab/profile.py`
- Test: `tests/test_profile.py`

**Interfaces:**
- Consumes: nothing from other tasks. Stats dict shape from Task 1 `weekly_stats` for `source_weights_section`.
- Produces, used by Tasks 5, 6, 7, 8:
  - `POINTER_KEY = {"experiment_id": "profile#current", "sk": "profile"}`, `POLICY_VERSION = "taste-profile-v1"`.
  - `ProfilePointer` frozen dataclass: `version: str, s3_key: str, previous_version: str | None, previous_s3_key: str | None, applied_ts: str, source: str, eval: dict`.
  - `version_now(now: datetime) -> str` formatted `%Y%m%dT%H%M%SZ`.
  - `profile_key(version) -> str` = `profile/<version>.md`; `candidate_key(version) -> str` = `profile/candidates/<version>.md`.
  - `get_pointer(table) -> ProfilePointer | None`.
  - `set_pointer(table, version, s3_key, previous: ProfilePointer | None, source: str, eval_result: dict, now: datetime) -> ProfilePointer`.
  - `write_profile(s3_client, bucket, key, text) -> None`.
  - `load_profile_text(table, s3_client, bucket, fallback_path) -> tuple[str, str]` returning `(text, version)`; version is `"seed"` on fallback.
  - `validate_profile(text, train_titles: list[str]) -> tuple[dict[str, str] | None, list[str]]` returning cleaned sections keyed by heading, or None plus problems.
  - `render_profile(version, sections: dict[str, str]) -> str`.
  - `source_weights_section(stats) -> str` (body only, bullet lines).
  - `split_sections(text) -> dict[str, str]`, `bullets(body) -> list[str]`.
  - `diff_bullets(old_text, new_text) -> dict[str, dict[str, list[str]]]` for `Prefer` and `Avoid`.
  - The Lambda in Task 8 re-implements the pointer read and flip with stdlib + boto3 (it imports nothing from the package). The item shape here is the contract.

- [ ] **Step 1: Write the failing tests `tests/test_profile.py`**

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_profile.py -q`
Expected: ImportError on `agentlab.profile`.

- [ ] **Step 3: Write `src/agentlab/profile.py`**

```python
"""Daniel's taste profile: versioned text in S3 behind one DynamoDB pointer.

The profile is semantic memory. `docs/interests.md` is baked into the
images, so nothing can update it at runtime; the live profile lives in S3
and the pointer item on the state table names the current version. The
pointer keeps the previous version so the REVERT tap in the approvals
Lambda can flip back without copying any object. The Lambda re-implements
the pointer read and flip with stdlib + boto3 (it imports nothing from this
package), so the item shape here is a contract: change both or neither.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

from botocore.exceptions import ClientError

POINTER_KEY = {"experiment_id": "profile#current", "sk": "profile"}
POLICY_VERSION = "taste-profile-v1"
VERSION_FORMAT = "%Y%m%dT%H%M%SZ"
TS_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"
SECTIONS = ("Prefer", "Avoid", "Positive examples", "Negative examples", "Source weights")
EXAMPLE_SECTIONS = ("Positive examples", "Negative examples")
MAX_WORDS = 1200
MIN_BULLETS = 3
MAX_EXAMPLES = 10
MIN_EXAMPLES = 3
TITLE_MATCH = 0.86
TITLE_LINE = "# Daniel's taste profile"


@dataclass(frozen=True)
class ProfilePointer:
    version: str
    s3_key: str
    previous_version: str | None
    previous_s3_key: str | None
    applied_ts: str
    source: str
    eval: dict


def version_now(now: datetime) -> str:
    return now.strftime(VERSION_FORMAT)


def profile_key(version: str) -> str:
    return f"profile/{version}.md"


def candidate_key(version: str) -> str:
    return f"profile/candidates/{version}.md"


def get_pointer(table) -> ProfilePointer | None:
    item = table.get_item(Key=POINTER_KEY).get("Item")
    if not item:
        return None
    raw_eval = item.get("eval") or "{}"
    try:
        eval_result = json.loads(raw_eval) if isinstance(raw_eval, str) else dict(raw_eval)
    except json.JSONDecodeError:
        eval_result = {}
    return ProfilePointer(
        version=str(item["version"]),
        s3_key=str(item["s3_key"]),
        previous_version=item.get("previous_version") or None,
        previous_s3_key=item.get("previous_s3_key") or None,
        applied_ts=str(item.get("applied_ts") or ""),
        source=str(item.get("source") or ""),
        eval=eval_result,
    )


def set_pointer(
    table,
    version: str,
    s3_key: str,
    previous: ProfilePointer | None,
    source: str,
    eval_result: dict,
    now: datetime,
) -> ProfilePointer:
    pointer = ProfilePointer(
        version=version,
        s3_key=s3_key,
        previous_version=previous.version if previous else None,
        previous_s3_key=previous.s3_key if previous else None,
        applied_ts=now.strftime(TS_FORMAT),
        source=source,
        eval=eval_result,
    )
    table.put_item(
        Item={
            **POINTER_KEY,
            "version": pointer.version,
            "s3_key": pointer.s3_key,
            "previous_version": pointer.previous_version,
            "previous_s3_key": pointer.previous_s3_key,
            "applied_ts": pointer.applied_ts,
            "source": pointer.source,
            "eval": json.dumps(pointer.eval),
        }
    )
    return pointer


def write_profile(s3_client, bucket: str, key: str, text: str) -> None:
    s3_client.put_object(Bucket=bucket, Key=key, Body=text.encode("utf-8"))


def load_profile_text(table, s3_client, bucket: str, fallback_path: str | Path) -> tuple[str, str]:
    """The current profile text and its version, or the seed file and "seed"."""
    pointer = get_pointer(table)
    if pointer is not None:
        try:
            body = s3_client.get_object(Bucket=bucket, Key=pointer.s3_key)["Body"].read()
            return body.decode("utf-8"), pointer.version
        except ClientError:
            pass
    return Path(fallback_path).read_text(encoding="utf-8"), "seed"


_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*$")


def _strip_fences(text: str) -> str:
    lines = [line for line in text.splitlines() if not _FENCE_RE.match(line.strip())]
    return "\n".join(lines)


def split_sections(text: str) -> dict[str, str]:
    """Map each `## Heading` to the lines under it, in document order."""
    sections: dict[str, str] = {}
    current: str | None = None
    body: list[str] = []
    for line in _strip_fences(text).splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(body).strip()
            current = line[3:].strip()
            body = []
        elif current is not None:
            body.append(line)
    if current is not None:
        sections[current] = "\n".join(body).strip()
    return sections


def bullets(body: str) -> list[str]:
    return [line[2:].strip() for line in body.splitlines() if line.startswith("- ")]


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def _known_title(line: str, train_titles: list[str]) -> bool:
    candidate = _plain(line.split(" (", 1)[0])
    return any(
        SequenceMatcher(None, candidate, _plain(title)).ratio() >= TITLE_MATCH
        for title in train_titles
    )


def validate_profile(text: str, train_titles: list[str]) -> tuple[dict[str, str] | None, list[str]]:
    """Cleaned sections keyed by heading, or None with the list of problems."""
    problems: list[str] = []
    sections = split_sections(text)
    for heading in SECTIONS:
        if heading not in sections:
            problems.append(f"missing section: {heading}")
    if problems:
        return None, problems
    if list(sections)[: len(SECTIONS)] != list(SECTIONS):
        problems.append("sections out of order")
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"too many words: {words} > {MAX_WORDS}")
    cleaned: dict[str, str] = {}
    for heading in ("Prefer", "Avoid"):
        lines = bullets(sections[heading])
        if len(lines) < MIN_BULLETS:
            problems.append(f"{heading}: {len(lines)} bullets, need {MIN_BULLETS}")
        cleaned[heading] = "\n".join(f"- {line}" for line in lines)
    for heading in EXAMPLE_SECTIONS:
        kept = [line for line in bullets(sections[heading]) if _known_title(line, train_titles)]
        kept = kept[:MAX_EXAMPLES]
        if len(kept) < MIN_EXAMPLES:
            problems.append(f"{heading}: {len(kept)} known examples, need {MIN_EXAMPLES}")
        cleaned[heading] = "\n".join(f"- {line}" for line in kept)
    cleaned["Source weights"] = sections["Source weights"]
    if problems:
        return None, problems
    return cleaned, []


def render_profile(version: str, sections: dict[str, str]) -> str:
    parts = [TITLE_LINE, f"version: {version}", ""]
    for heading in SECTIONS:
        parts.append(f"## {heading}")
        parts.append(sections.get(heading, "").strip())
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def source_weights_section(stats: dict) -> str:
    lines = []
    for name in sorted(stats.get("sources", {})):
        counts = stats["sources"][name]
        if not counts.get("decided"):
            lines.append(f"- {name}: no decisions yet")
            continue
        lines.append(
            f"- {name}: approval {counts['approval_rate']:.2f} "
            f"({counts['approved']} of {counts['decided']})"
        )
    return "\n".join(lines)


def diff_bullets(old_text: str, new_text: str) -> dict[str, dict[str, list[str]]]:
    old_sections = split_sections(old_text)
    new_sections = split_sections(new_text)
    diff: dict[str, dict[str, list[str]]] = {}
    for heading in ("Prefer", "Avoid"):
        old_lines = bullets(old_sections.get(heading, ""))
        new_lines = bullets(new_sections.get(heading, ""))
        diff[heading] = {
            "added": [line for line in new_lines if line not in old_lines],
            "removed": [line for line in old_lines if line not in new_lines],
        }
    return diff
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_profile.py -q`
Expected: all pass. `test_validate_profile_accepts_and_cleans` counts three Prefer bullets through the expression `count("\n- ") + startswith("- ")`; if it fails, check that `cleaned["Prefer"]` starts with `- ` and joins with newlines.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/agentlab/profile.py tests/test_profile.py
git add src/agentlab/profile.py tests/test_profile.py
git commit -m "feat: versioned taste profile in S3 behind a DynamoDB pointer"
```

---

### Task 3: Probe eval and the swap rule

**Files:**
- Create: `src/agentlab/taste_eval.py`
- Test: `tests/test_taste_eval.py`

**Interfaces:**
- Consumes: `Episode` from Task 1 (`title`, `source_type`, `weight`, `positive`, `kind`).
- Produces, used by Task 6:
  - `F1_TOLERANCE = 0.02`.
  - `EvalResult` frozen dataclass: `precision: float, recall: float, f1: float, golden_recall: float, held_out_count: int, golden_count: int, yes_count: int`, plus `as_dict() -> dict`.
  - `probe_messages(profile_text, title, source_type) -> list[dict]`.
  - `parse_yes_no(raw) -> bool | None`.
  - `evaluate(held_out, golden, profile_text, complete, model) -> EvalResult` where `complete(model, messages) -> str`.
  - `decide_swap(old: EvalResult, new: EvalResult) -> tuple[bool, str]`.

- [ ] **Step 1: Write the failing tests `tests/test_taste_eval.py`**

```python
"""The probe eval is the gate on every profile swap."""

import pytest

from agentlab.episodes import Episode
from agentlab.taste_eval import (
    EvalResult,
    decide_swap,
    evaluate,
    parse_yes_no,
    probe_messages,
)


def _episode(title, weight, kind="approved", source_type="arxiv"):
    return Episode(
        identity=f"title:{title.lower()}",
        title=title,
        url="",
        source_type=source_type,
        kind=kind,
        weight=weight,
        ts="2026-10-01T10:00:00.000000Z",
    )


def test_probe_messages_put_profile_in_system_and_item_in_user():
    messages = probe_messages("PROFILE TEXT", "Some Paper", "arxiv")
    assert messages[0]["role"] == "system"
    assert "PROFILE TEXT" in messages[0]["content"]
    assert "YES or NO" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Some Paper (arxiv)" in messages[1]["content"]


def test_parse_yes_no():
    assert parse_yes_no("YES") is True
    assert parse_yes_no("  no.") is False
    assert parse_yes_no("Yes, Daniel would like it") is True
    assert parse_yes_no("I think not") is None
    assert parse_yes_no("") is None


def test_evaluate_metrics():
    held_out = [
        _episode("Good One", 1.0),
        _episode("Good Two", 1.0),
        _episode("Bad One", -1.0, kind="rejected", source_type="github"),
        _episode("Bad Two", -0.5, kind="ignored"),
    ]
    golden = [
        _episode("Golden One", 1.0, kind="golden_yes", source_type="classic"),
        _episode("Golden No", -1.0, kind="golden_no", source_type="classic"),
    ]
    answers = {
        "Good One": "YES",
        "Good Two": "NO",
        "Bad One": "YES",
        "Bad Two": "NO",
        "Golden One": "YES",
        "Golden No": "garbage",
    }
    seen_models = []

    def complete(model, messages):
        seen_models.append(model)
        title = messages[1]["content"].split("lesson on: ", 1)[1].split(" (", 1)[0]
        return answers[title]

    result = evaluate(held_out, golden, "profile", complete, "bedrock/probe")
    assert seen_models == ["bedrock/probe"] * 5  # 4 held-out probes + 1 golden_yes probe
    assert result.held_out_count == 4
    assert result.golden_count == 1
    assert result.yes_count == 2
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(0.5)
    assert result.f1 == pytest.approx(0.5)
    assert result.golden_recall == pytest.approx(1.0)
    assert result.as_dict()["f1"] == pytest.approx(0.5)


def test_evaluate_handles_empty_sets():
    result = evaluate([], [], "profile", lambda model, messages: "YES", "m")
    assert result.f1 == 0.0
    assert result.golden_recall == 1.0


def test_decide_swap_rule():
    old = EvalResult(precision=0.6, recall=0.6, f1=0.6, golden_recall=0.9, held_out_count=50, golden_count=20, yes_count=30)
    better = EvalResult(0.7, 0.7, 0.7, 0.9, 50, 20, 30)
    within = EvalResult(0.58, 0.58, 0.58, 0.9, 50, 20, 30)
    worse = EvalResult(0.57, 0.57, 0.57, 0.9, 50, 20, 30)
    golden_drop = EvalResult(0.8, 0.8, 0.8, 0.85, 50, 20, 30)
    assert decide_swap(old, better) == (True, "F1 0.60 -> 0.70, golden recall 0.90 -> 0.90")
    assert decide_swap(old, within)[0] is True
    assert decide_swap(old, worse) == (False, "F1 fell 0.60 -> 0.57, more than 0.02")
    assert decide_swap(old, golden_drop) == (False, "golden recall fell 0.90 -> 0.85")
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_taste_eval.py -q`
Expected: ImportError on `agentlab.taste_eval`.

- [ ] **Step 3: Write `src/agentlab/taste_eval.py`**

```python
"""Probe eval: does a profile predict Daniel's own verdicts on held-out items?

One small model call per item answers YES or NO with the profile as the
system prompt. F1 of YES against the positive items is the score; recall on
the golden set is the protected regression check (the spec's "misrank none
of a protected core subset" rule, measured as recall that must not fall).
The swap rule is a pure function so it has exact tests.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

from agentlab.episodes import Episode

F1_TOLERANCE = 0.02
PROBE_SYSTEM_SUFFIX = (
    "\n\nYou are Daniel's taste model. Use the profile above. "
    "Answer YES or NO only."
)
_ANSWER_RE = re.compile(r"\b(yes|no)\b", re.IGNORECASE)


@dataclass(frozen=True)
class EvalResult:
    precision: float
    recall: float
    f1: float
    golden_recall: float
    held_out_count: int
    golden_count: int
    yes_count: int

    def as_dict(self) -> dict:
        return asdict(self)


def probe_messages(profile_text: str, title: str, source_type: str) -> list[dict]:
    return [
        {"role": "system", "content": profile_text + PROBE_SYSTEM_SUFFIX},
        {
            "role": "user",
            "content": f"Would Daniel approve a lesson on: {title} ({source_type})? YES or NO.",
        },
    ]


def parse_yes_no(raw: str) -> bool | None:
    match = _ANSWER_RE.search(raw or "")
    if not match:
        return None
    return match.group(1).lower() == "yes"


def _predict(episode: Episode, profile_text: str, complete: Callable, model: str) -> bool:
    raw = complete(model, probe_messages(profile_text, episode.title, episode.source_type))
    return parse_yes_no(raw) is True


def evaluate(
    held_out: list[Episode],
    golden: list[Episode],
    profile_text: str,
    complete: Callable[[str, list[dict]], str],
    model: str,
) -> EvalResult:
    predictions = [(episode, _predict(episode, profile_text, complete, model)) for episode in held_out]
    yes_count = sum(1 for _episode, yes in predictions if yes)
    true_positives = sum(1 for episode, yes in predictions if yes and episode.positive)
    positives = sum(1 for episode in held_out if episode.positive)
    precision = true_positives / yes_count if yes_count else 0.0
    recall = true_positives / positives if positives else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    golden_yes = [episode for episode in golden if episode.positive]
    golden_hits = sum(1 for episode in golden_yes if _predict(episode, profile_text, complete, model))
    golden_recall = golden_hits / len(golden_yes) if golden_yes else 1.0
    return EvalResult(
        precision=precision,
        recall=recall,
        f1=f1,
        golden_recall=golden_recall,
        held_out_count=len(held_out),
        golden_count=len(golden_yes),
        yes_count=yes_count,
    )


def decide_swap(old: EvalResult, new: EvalResult) -> tuple[bool, str]:
    if new.golden_recall < old.golden_recall:
        return False, f"golden recall fell {old.golden_recall:.2f} -> {new.golden_recall:.2f}"
    if new.f1 < old.f1 - F1_TOLERANCE:
        return False, f"F1 fell {old.f1:.2f} -> {new.f1:.2f}, more than {F1_TOLERANCE:.2f}"
    return True, (
        f"F1 {old.f1:.2f} -> {new.f1:.2f}, "
        f"golden recall {old.golden_recall:.2f} -> {new.golden_recall:.2f}"
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_taste_eval.py -q`
Expected: all pass. Note `test_evaluate_metrics` expects golden_count to count only golden_yes items (1), and that the golden "garbage" answer never reaches the held-out metrics.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/agentlab/taste_eval.py tests/test_taste_eval.py
git add src/agentlab/taste_eval.py tests/test_taste_eval.py
git commit -m "feat: probe eval and swap rule for taste profiles"
```

---

### Task 4: Source pools without GitHub release notes

**Files:**
- Modify: `src/agentlab/sources.py`
- Modify: `tests/test_sources.py`

**Interfaces:**
- Consumes: `agentlab.papers_db.paper_identity` (exists).
- Produces, used by Tasks 5 and 7:
  - `fetch_arxiv(client, max_results=40)`: query `cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA`, same keyword filter.
  - `gather_exploit(client=None)`: arXiv plus HN keyword hits, every dict tagged `pool="exploit"`. The core video track keeps using it.
  - `gather_proposer_pool(client=None)`: arXiv plus Hugging Face daily plus HN keyword hits, deduplicated by `paper_identity`, tagged `pool="proposer"`.
  - `gather` is an alias of `gather_proposer_pool`.
  - `fetch_github_releases` and `TRACKED_REPOS` no longer exist.

- [ ] **Step 1: Update the tests in `tests/test_sources.py`**

Remove `fetch_github_releases` from the import block and add `gather_proposer_pool`.
Delete the whole function `test_github_releases_normalized`.
In `test_arxiv_filters_by_keyword`, make the fake client record the request and assert the categories, by replacing the `fake_client = FakeClient(...)` block and the call with:

```python
    seen = {}

    class RecordingClient(FakeClient):
        def get(self, url, **kwargs):
            seen["params"] = kwargs.get("params")
            return super().get(url, **kwargs)

    fake_client = RecordingClient(
        {
            "export.arxiv.org": FakeResponse(200, text=arxiv_xml),
        }
    )

    result = fetch_arxiv(fake_client)

    assert seen["params"]["search_query"] == "cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA"
    assert seen["params"]["max_results"] == 40
```

In `test_gather_survives_total_network_failure`, replace the docstring with `"""gather (the proposer pool alias) returns an empty list on complete network failure."""`, remove the `"api.github.com": raise_exception,` line, add `"huggingface.co/api/daily_papers": raise_exception,`, and change `assert gather is gather_exploit` to `assert gather is gather_proposer_pool`.

Add this test at the end of the file:

```python
def test_gather_proposer_pool_merges_arxiv_hf_hn_and_dedups():
    """The proposer pool has no GitHub releases and no duplicate papers."""
    arxiv_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Agent Memory Paper</title>
    <summary>An agent memory method.</summary>
  </entry>
</feed>"""
    fake_client = FakeClient(
        {
            "export.arxiv.org": FakeResponse(200, text=arxiv_xml),
            "huggingface.co/api/daily_papers": FakeResponse(
                200,
                json_data=[
                    {"paper": {"id": "2401.00001", "title": "Agent Memory Paper", "upvotes": 9}},
                    {"paper": {"id": "2401.00002", "title": "World Model Paper", "upvotes": 3}},
                ],
            ),
            "hn.algolia.com": FakeResponse(
                200,
                json_data={
                    "hits": [
                        {
                            "title": "New agent harness released",
                            "url": "https://example.com/harness",
                            "objectID": "1",
                            "points": 10,
                        }
                    ]
                },
            ),
        }
    )

    result = gather_proposer_pool(client=fake_client)

    assert [r["source"] for r in result] == ["arxiv", "hf", "hn"]
    assert [r["title"] for r in result] == [
        "Agent Memory Paper",
        "World Model Paper",
        "New agent harness released",
    ]
    assert all(r["pool"] == "proposer" for r in result)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_sources.py -q`
Expected: ImportError for `gather_proposer_pool`.

- [ ] **Step 3: Edit `src/agentlab/sources.py`**

Replace the module docstring's "Two pools" paragraph and everything from `TRACKED_REPOS` through the end of `fetch_github_releases` with:

```python
"""Fresh external sources: arXiv, HN, Hugging Face daily papers.

Each fetcher normalizes to small dicts and fails soft: a non-200, a
malformed payload, or a network error yields fewer sources, never an
exception out of a gather call. The proposer treats an empty list as "no
fresh sources today" and says so instead of inventing work (anti-collapse
rule: fresh-external-source anchoring).

GitHub release notes were removed on 2026-10-02: the ledger showed 10 of 12
release-note proposals rejected and three changelog videos nobody rated.

Three pools:
- proposer: arXiv + HF daily + HN keyword hits, deduplicated by paper
  identity. What `worker propose` reads.
- exploit: arXiv + HN keyword hits. What the core video track reads.
- explore: HN front page above a points threshold and HF daily, no keyword
  filter. What the novel video track reads.
"""

import xml.etree.ElementTree as ET

import httpx

from agentlab.papers_db import paper_identity

ARXIV_QUERY = "cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA"
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
```

Change `fetch_arxiv` to `def fetch_arxiv(client, max_results: int = 40) -> list[dict]:` and its `"search_query": "cat:cs.CL OR cat:cs.AI",` line to `"search_query": ARXIV_QUERY,`.

Replace `gather_exploit`, the `gather` alias, and leave `gather_explore` as is:

```python
def gather_exploit(client=None) -> list[dict]:
    client = client or httpx.Client()
    items = fetch_arxiv(client) + fetch_hn_front(client)
    for item in items:
        item["pool"] = "exploit"
    return items


def gather_proposer_pool(client=None) -> list[dict]:
    """Everything the proposer may cite today, one entry per paper."""
    client = client or httpx.Client()
    items = fetch_arxiv(client) + fetch_hf_daily(client) + fetch_hn_front(client)
    seen: set[str] = set()
    unique = []
    for item in items:
        identity = paper_identity(item.get("url", ""), item.get("title", ""))
        if identity in seen:
            continue
        seen.add(identity)
        item["pool"] = "proposer"
        unique.append(item)
    return unique


# The proposer imports `gather`; keep it as a working alias of the proposer pool.
gather = gather_proposer_pool
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_sources.py -q`
Expected: all pass.

- [ ] **Step 5: Grep for leftovers**

Run: `grep -rn "fetch_github_releases\|TRACKED_REPOS" src tests docs/superpowers/plans/2026-10-02-taste-flywheel.md`
Expected: only this plan file.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src/agentlab/sources.py tests/test_sources.py
git add src/agentlab/sources.py tests/test_sources.py
git commit -m "feat: drop GitHub release notes from every pool, add the proposer pool"
```

---

### Task 5: The proposer picks lessons with the profile

**Files:**
- Modify: `src/agentlab/proposals.py`
- Modify: `src/agentlab/proposer.py` (full rewrite)
- Modify: `tests/test_proposer.py` (full rewrite)
- Modify: `tests/test_proposals.py` (add two tests)

**Interfaces:**
- Consumes: Task 1 `proposal_display_status`, `source_type_from_url`; Task 2 `load_profile_text`; Task 4 `gather`; `repo_files.CLASSICS_PATH`, `INTERESTS_PATH`; `papers_db.is_seen`, `normalize_title`, `paper_identity`, `recent_seen_titles`.
- Produces, used by Tasks 7 and 8:
  - `proposals.file_proposal(table, pid, title, headline, citation, distance, kind, submit_body=None, *, why=None, lens=None, source_type=None, profile_version=None)`: the item now also carries `why`, `lens`, `source_type`, `profile_version`, `video_key` (None).
  - `proposals.set_video_key(table, pid, video_key) -> None`.
  - `proposals.list_all(table) -> list[dict]`.
  - `proposer.run_propose(table, ssm_client, s3_client, bucket, model, now=None) -> int`.
  - `proposer.DEFAULT_PROPOSER_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"`.
  - `proposer.next_classic(table, classics) -> dict | None`, `proposer.load_classics() -> list[dict]`.
  - Day record at `proposals/days/<YYYY-MM-DD>/sources.json`.
  - Telegram message format `n. [LENS] Title (source_type)\nWhy: ...\n<url>`, buttons unchanged.

- [ ] **Step 1: Add the ledger helpers to `src/agentlab/proposals.py`**

Add the import `from agentlab.episodes import source_type_from_url` after the botocore import.
Replace `file_proposal` with:

```python
def file_proposal(
    table,
    pid: str,
    title: str,
    headline: str,
    citation: str,
    distance: str,
    kind: str,
    submit_body: dict | None = None,
    *,
    why: str | None = None,
    lens: str | None = None,
    source_type: str | None = None,
    profile_version: str | None = None,
) -> None:
    item = {
        "experiment_id": f"proposal#{pid}",
        "sk": PROPOSAL_SK,
        "pid": pid,
        "status": "PROPOSED",
        "title": title,
        "headline": headline,
        "why": why or headline,
        "citation": citation,
        "distance": distance,
        "kind": kind,
        "lens": lens,
        "source_type": source_type or source_type_from_url(citation),
        "profile_version": profile_version,
        "video_key": None,
        "created_ts": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    if submit_body is not None:
        item["submit_body"] = json.dumps(submit_body)
    table.put_item(Item=item)
```

Add after `set_verdict`:

```python
def set_video_key(table, pid: str, video_key: str) -> None:
    """Record the video an approved proposal produced, so a rating can reach it."""
    table.update_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": PROPOSAL_SK},
        UpdateExpression="SET video_key = :k",
        ExpressionAttributeValues={":k": video_key},
    )


def list_all(table) -> list[dict]:
    return table.scan(FilterExpression=Attr("sk").eq(PROPOSAL_SK))["Items"]
```

Change `list_recent` to use it:

```python
def list_recent(table, limit: int = 20) -> list[dict]:
    items = list_all(table)
    return sorted(items, key=lambda i: i["created_ts"], reverse=True)[:limit]
```

Add two tests to `tests/test_proposals.py` (its `fabric` fixture yields `(table, None)`) and add `set_video_key` to the import block:

```python
def test_file_proposal_stores_taste_fields(fabric):
    table, _ = fabric
    file_proposal(
        table,
        "p-taste",
        "Real Title",
        "Why line",
        "https://arxiv.org/abs/2501.00001",
        "d",
        "new_hypothesis",
        lens="frontier",
        profile_version="v1",
    )
    item = get_proposal(table, "p-taste")
    assert item["why"] == "Why line"
    assert item["lens"] == "frontier"
    assert item["source_type"] == "arxiv"
    assert item["profile_version"] == "v1"
    assert item["video_key"] is None


def test_set_video_key(fabric):
    table, _ = fabric
    file_proposal(table, "p-vid", "T", "H", "https://x", "d", "new_hypothesis")
    set_video_key(table, "p-vid", "videos/core-1.mp4")
    assert get_proposal(table, "p-vid")["video_key"] == "videos/core-1.mp4"
```

Run: `uv run pytest tests/test_proposals.py -q`
Expected: pass after the edit (the import of `set_video_key` must be added at the top of the test file).

- [ ] **Step 2: Write the new `tests/test_proposer.py`**

Replace the whole file with:

```python
"""Proposer tests, fully offline: moto stands in for SSM/DynamoDB/S3, a
monkeypatched `proposer._complete` stands in for the model, a monkeypatched
`proposer.gather` stands in for live fetchers, and a monkeypatched
`httpx.post` captures every Telegram call.

The proposer picks lessons with Daniel's taste profile in its prompt. With
no profile pointer the prompt falls back to docs/interests.md, which is
what these tests exercise unless they set a pointer.
"""

import json
from datetime import UTC
from datetime import datetime
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_proposer.py -q`
Expected: ImportError for `build_prompt`/`format_message`/`next_classic` signatures or failures on the old prompt.

- [ ] **Step 4: Rewrite `src/agentlab/proposer.py`**

```python
"""The proposer: read fresh sources, pick today's lessons with Daniel's
taste profile, ping Daniel.

Anti-collapse rules enforced here (docs/phase0-synthesis.md): every
proposal anchors to a fresh external source by URL; every proposal states
its distance from the recent archive (which includes rejected and ignored
proposals, so dead ideas do not come back reworded); at most DAILY_CAP
proposals per day.

Since 2026-10-02 the proposer picks lessons, not experiments. One slot a
day is the next foundational paper from docs/classics.json, filed by code.
The other slots are fresh picks by the model with the taste profile
(docs/superpowers/specs/2026-10-02-taste-flywheel-design.md) in the prompt.
The profile is the only thing that learns; this module never writes it.
"""

import json
import os
from datetime import UTC, datetime

from agentlab.episodes import proposal_display_status, source_type_from_url
from agentlab.notify import notify, queue_ping
from agentlab.papers_db import is_seen, normalize_title, paper_identity, recent_seen_titles
from agentlab.profile import load_profile_text
from agentlab.proposals import (
    DAILY_CAP,
    count_created_today,
    file_proposal,
    generate_proposal_id,
    list_all,
    list_recent,
)
from agentlab.repo_files import CLASSICS_PATH, INTERESTS_PATH
from agentlab.sources import gather

DEFAULT_PROPOSER_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"
MAX_TITLE = 80
MAX_WHY = 300
MAX_CITATION = 300
MAX_DISTANCE = 300
MAX_SUMMARY = 200
LENSES = ("foundational", "frontier", "implement")
DEFAULT_LENS = "frontier"
CLASSIC_WHY = "Foundational paper from {year}. Everyone in the field builds on it."
CLASSIC_DISTANCE = "Foundational slot, not from today's sources."

PROPOSER_SYSTEM = """You choose what Daniel learns today. AgentLab turns one \
source into a short video lesson when Daniel taps APPROVE, so you pick \
lessons, not experiments. Daniel approves a lesson when it fits one of three \
lenses. foundational: a paper everyone in the field knows and builds on. \
frontier: a new paper on his core interests, which are agents, agent \
harnesses, evals, agent memory, recursive self-improvement, verification, \
and multi-agent coordination. implement: something he could build into his \
own agent harness this month. Daniel rejects changelogs, SDK release notes, \
version announcements, product throughput posts, and generic news. Use the \
source's real title, shortened only if longer than 80 characters. Write one \
line why Daniel wants this lesson, specific to him and to the taste profile \
you are given. Tag exactly one lens. Cite exactly one URL from the list you \
are given. State the distance from the recent archive in one sentence and \
never repropose anything the archive shows. Your why may claim only what \
the source's own title or summary supports; if your idea goes beyond the \
source, say "building on" the source. Never attribute a method or result to \
a source unless its listed title or summary states it. A proposal that \
misstates its source is worse than no proposal (a hallucinated claim was \
caught on 2026-08-21 and wasted a day). Short plain sentences. Output ONLY a \
JSON array of objects with keys: title, why, citation, distance, lens."""


def _complete(model: str, messages: list[dict]) -> str:
    """The only litellm touchpoint; tests monkeypatch this."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    response = litellm.completion(model=model, messages=messages, max_tokens=3000)
    return response.choices[0].message.content


def _source_line(source: dict) -> str:
    line = f"- [{source['source']}] {source['title']} :: {source['url']}"
    summary = " ".join(str(source.get("summary") or source.get("notes") or "").split())
    if summary:
        line += f" :: {summary[:MAX_SUMMARY]}"
    return line


def build_prompt(
    sources: list[dict], archive: list[dict], slots: int, profile_text: str, now: datetime
) -> str:
    source_lines = "\n".join(_source_line(s) for s in sources)
    archive_lines = "\n".join(
        f"- [{proposal_display_status(p, now)}] {p.get('title')} :: {p.get('distance', '')}"
        for p in archive
    ) or "- (archive is empty)"
    return (
        f"Daniel's taste profile:\n{profile_text}\n\n"
        f"Fresh sources today:\n{source_lines}\n\n"
        f"Recent archive (do not repeat these):\n{archive_lines}\n\n"
        f"Pick at most {slots} lessons as a JSON array."
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
        why = entry.get("why") or entry.get("headline")
        fields = {
            "title": entry.get("title"),
            "why": why,
            "citation": entry.get("citation"),
            "distance": entry.get("distance"),
        }
        if not all(isinstance(value, str) and value for value in fields.values()):
            continue
        lens = str(entry.get("lens") or "").strip().lower()
        proposals.append(
            {
                "title": fields["title"][:MAX_TITLE],
                "why": fields["why"][:MAX_WHY],
                "citation": fields["citation"][:MAX_CITATION],
                "distance": fields["distance"][:MAX_DISTANCE],
                "lens": lens if lens in LENSES else DEFAULT_LENS,
                "kind": "new_hypothesis",
            }
        )
    return proposals


def load_classics() -> list[dict]:
    return json.loads(CLASSICS_PATH.read_text(encoding="utf-8"))


def next_classic(table, classics: list[dict]) -> dict | None:
    """First classic neither in the seen-papers store nor already proposed."""
    fuzzy_titles = recent_seen_titles(table)
    proposed = {normalize_title(str(p.get("title") or "")) for p in list_all(table)}
    for entry in classics:
        identity = paper_identity(entry["url"], entry["title"])
        if is_seen(table, identity, fuzzy_titles):
            continue
        if normalize_title(entry["title"]) in proposed:
            continue
        return entry
    return None


def format_message(entries: list[dict]) -> tuple[str, list]:
    lines, buttons = [], []
    for index, entry in enumerate(entries, 1):
        lines.append(
            f"{index}. [{entry['lens'].upper()}] {entry['title']} ({entry['source_type']})\n"
            f"Why: {entry['why']}\n"
            f"{entry['citation']}"
        )
        buttons.append(
            [
                (f"APPROVE {index}", f"prop:{entry['pid']}:approve"),
                (f"REJECT {index}", f"prop:{entry['pid']}:reject"),
            ]
        )
    return "Today's lessons. Tap to decide.\n\n" + "\n\n".join(lines), buttons


def write_day_record(
    s3_client, bucket: str, date: str, profile_version: str, candidates: list[dict], chosen: list[dict]
) -> None:
    record = {
        "date": date,
        "profile_version": profile_version,
        "candidates": [
            {
                "title": c.get("title", ""),
                "url": c.get("url", ""),
                "source": c.get("source", ""),
                "pool": c.get("pool", ""),
                "summary": str(c.get("summary") or "")[:MAX_SUMMARY],
            }
            for c in candidates
        ],
        "chosen": [
            {"pid": e["pid"], "title": e["title"], "url": e["citation"], "lens": e["lens"]}
            for e in chosen
        ],
    }
    s3_client.put_object(
        Bucket=bucket,
        Key=f"proposals/days/{date}/sources.json",
        Body=json.dumps(record).encode("utf-8"),
    )


def _file(
    table, s3_client, bucket: str, proposal: dict, source_type: str, profile_version: str, sources_seen: int
) -> dict:
    pid = generate_proposal_id()
    file_proposal(
        table,
        pid,
        proposal["title"],
        proposal["why"],
        proposal["citation"],
        proposal["distance"],
        "new_hypothesis",
        why=proposal["why"],
        lens=proposal["lens"],
        source_type=source_type,
        profile_version=profile_version,
    )
    s3_client.put_object(
        Bucket=bucket,
        Key=f"proposals/{pid}/proposal.json",
        Body=json.dumps(
            {"proposal": proposal, "sources_seen": sources_seen, "profile_version": profile_version}
        ).encode("utf-8"),
    )
    return {**proposal, "pid": pid, "source_type": source_type}


def run_propose(table, ssm_client, s3_client, bucket: str, model: str, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    slots = DAILY_CAP - count_created_today(table)
    if slots <= 0:
        # Silence must always mean "not running", never "ran with nothing to
        # say" (Daniel mistook a capped run for a dead lab, 2026-08-21).
        notify(
            table,
            ssm_client,
            "The proposer ran. The daily cap of "
            f"{DAILY_CAP} proposals is already used. Nothing new today.",
        )
        return 0
    profile_text, profile_version = load_profile_text(table, s3_client, bucket, INTERESTS_PATH)
    sources = [s for s in gather() if s.get("source") != "github"]
    entries: list[dict] = []

    classic = next_classic(table, load_classics())
    if classic is not None:
        proposal = {
            "title": classic["title"][:MAX_TITLE],
            "why": CLASSIC_WHY.format(year=classic.get("year", "")),
            "citation": classic["url"],
            "distance": CLASSIC_DISTANCE,
            "lens": "foundational",
            "kind": "new_hypothesis",
        }
        entries.append(_file(table, s3_client, bucket, proposal, "classic", profile_version, len(sources)))
        slots -= 1

    if slots > 0 and sources:
        archive = list_recent(table)
        raw = _complete(
            model,
            [
                {"role": "system", "content": PROPOSER_SYSTEM},
                {"role": "user", "content": build_prompt(sources, archive, slots, profile_text, now)},
            ],
        )
        for proposal in parse_proposals(raw)[:slots]:
            source_type = source_type_from_url(proposal["citation"])
            entries.append(_file(table, s3_client, bucket, proposal, source_type, profile_version, len(sources)))

    if not entries:
        reason = (
            "found no fresh sources. No proposals today."
            if not sources
            else "produced no valid proposals this time."
        )
        notify(table, ssm_client, f"The proposer ran but {reason}")
        return 0

    write_day_record(s3_client, bucket, now.strftime("%Y-%m-%d"), profile_version, sources, entries)
    text, buttons = format_message(entries)
    try:
        notify(table, ssm_client, text, buttons=buttons)
    except Exception:  # noqa: BLE001 - filed proposals must never be silently stranded
        queue_ping(table, text, buttons, None)
    return len(entries)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_proposer.py tests/test_proposals.py -q`
Expected: all pass. `test_run_propose_files_classic_first_then_fresh_and_uploads` reads the real `docs/classics.json` and `docs/interests.md`; the first classic is "Attention Is All You Need" and the seed text contains "Daniel wants the single best new paper".

- [ ] **Step 6: Check nothing else imports the removed names**

Run: `grep -rn "_registered_submit_body\|build_message_body\|from agentlab.cloud" src/agentlab/proposer.py tests/test_proposer.py`
Expected: no output.

- [ ] **Step 7: Lint and commit**

```bash
uv run ruff check src/agentlab/proposer.py src/agentlab/proposals.py tests/test_proposer.py tests/test_proposals.py
git add src/agentlab/proposer.py src/agentlab/proposals.py tests/test_proposer.py tests/test_proposals.py
git commit -m "feat: proposer picks lessons with the taste profile, one classic a day"
```

---

### Task 6: Weekly consolidation job

**Files:**
- Create: `src/agentlab/consolidate.py`
- Test: `tests/test_consolidate.py`

**Interfaces:**
- Consumes: Task 1 `derive_episodes`, `load_golden`, `parse_ts`, `scan_all`, `split`, `weekly_stats`, `Episode`; Task 2 everything listed there; Task 3 `evaluate`, `decide_swap`, `EvalResult`; `agentlab.notify.notify`.
- Produces, used by Task 7:
  - `DEFAULT_CONSOLIDATE_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"`, `MIN_NEW_EPISODES = 5`.
  - `run_consolidate(table, s3_client, ssm_client, bucket, model, probe_model, golden_path, seed_path, now=None, dry_run=False) -> dict` with keys `applied: bool`, `tally_only: bool`, `version: str | None`, `reason: str`, `text: str` (the ping text), `candidate: str | None`, `old_eval: dict | None`, `new_eval: dict | None`, `problems: list[str]`.
  - `_complete(model, messages) -> str` is the litellm touchpoint, monkeypatched in tests.

- [ ] **Step 1: Write the failing tests `tests/test_consolidate.py`**

```python
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
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_consolidate.py -q`
Expected: ImportError on `agentlab.consolidate`.

- [ ] **Step 3: Write `src/agentlab/consolidate.py`**

```python
"""Weekly consolidation: episodes in, a gated taste profile swap out.

Runs on Sunday evening as `worker consolidate` (scheduler.tf). Code
computes every fact (counts, rates, the held-out split, the swap decision);
the model only writes the prefer and avoid sentences and picks examples.
The new profile replaces the old one only when the probe eval
(taste_eval.py) says it is not worse on held-out verdicts and not worse on
the golden set. Every run pings, applied or not, because silence must never
mean broken.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from agentlab.episodes import (
    Episode,
    derive_episodes,
    load_golden,
    parse_ts,
    scan_all,
    split,
    weekly_stats,
)
from agentlab.notify import notify
from agentlab.profile import (
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
from agentlab.taste_eval import EvalResult, decide_swap, evaluate

DEFAULT_CONSOLIDATE_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"
MIN_NEW_EPISODES = 5
MAX_LINES_PER_SIDE = 75
RECENT_WEEKS = 8

CONSOLIDATE_SYSTEM = """You maintain Daniel's taste profile for AgentLab, a \
lab that turns research papers into short video lessons for him. You get the \
current profile, the statistics, and the episodes: what Daniel approved, \
rejected, ignored, rated COOL, MEH, or SKIP, or left unrated. Ignored and \
unrated mean "not interesting" by his own ruling. Write the new profile \
about Daniel, not about the lab. Each Prefer and Avoid bullet states one \
pattern and ends with evidence counts in parentheses, like (evidence: 4 \
approved, 2 COOL). Choose examples only from the episodes you are given and \
copy their titles exactly. Never invent a title. Keep the three lenses in \
mind: foundational papers he should know, frontier papers on agents, evals, \
memory, and self-improvement, and things he would implement in his own \
harness. At least 3 bullets under Prefer and under Avoid. At most 10 \
examples per examples section. At most 1200 words in total. Output ONLY the \
profile in exactly this markdown shape and nothing else:

# Daniel's taste profile
version: pending

## Prefer
- <pattern> (evidence: <counts>)

## Avoid
- <pattern> (evidence: <counts>)

## Positive examples
- <exact title> (<kind>, <source_type>)

## Negative examples
- <exact title> (<kind>, <source_type>)

## Source weights
- (code fills this section)"""


def _complete(model: str, messages: list[dict]) -> str:
    """The only litellm touchpoint; tests monkeypatch this."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    response = litellm.completion(model=model, messages=messages, max_tokens=4000)
    return response.choices[0].message.content


def episode_line(episode: Episode) -> str:
    why = f" :: {episode.why}" if episode.why else ""
    return (
        f"- {episode.title} ({episode.kind}, {episode.source_type}, "
        f"weight {episode.weight:+.1f}){why}"
    )


def _ordered(episodes: list[Episode]) -> list[Episode]:
    """Golden first, then most recent first."""
    ordered = sorted(episodes, key=lambda e: e.ts, reverse=True)
    ordered.sort(key=lambda e: not e.golden)
    return ordered


def _rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def stats_lines(stats: dict) -> list[str]:
    lines = []
    for week in stats["weeks"][-RECENT_WEEKS:]:
        decided = week["approved"] + week["rejected"] + week["ignored"]
        rated = week["cool"] + week["meh"] + week["skip"] + week["unrated"]
        lines.append(
            f"- {week['week']}: approvals {week['approved']} of {decided} "
            f"({_rate(week['approval_rate'])}), COOL {week['cool']} of {rated} "
            f"({_rate(week['cool_rate'])})"
        )
    for name in sorted(stats["sources"]):
        counts = stats["sources"][name]
        lines.append(f"- source {name}: {counts['approved']} of {counts['decided']} approved")
    return lines


def build_consolidate_prompt(current_profile: str, stats: dict, train: list[Episode]) -> str:
    positives = _ordered([e for e in train if e.positive])[:MAX_LINES_PER_SIDE]
    negatives = _ordered([e for e in train if not e.positive])[:MAX_LINES_PER_SIDE]
    return (
        f"Current profile:\n{current_profile}\n\n"
        "Statistics:\n" + "\n".join(stats_lines(stats)) + "\n\n"
        "Positive episodes (Daniel wanted these):\n" + "\n".join(episode_line(e) for e in positives) + "\n\n"
        "Negative episodes (Daniel did not want these):\n" + "\n".join(episode_line(e) for e in negatives) + "\n\n"
        "Write the new profile."
    )


def new_episode_count(episodes: list[Episode], since_ts: str) -> int:
    since = parse_ts(since_ts)
    return sum(1 for e in episodes if e.ts and parse_ts(e.ts) > since)


def _week_line(stats: dict) -> str:
    if not stats["weeks"]:
        return "No decided episodes yet."
    week = stats["weeks"][-1]
    decided = week["approved"] + week["rejected"] + week["ignored"]
    rated = week["cool"] + week["meh"] + week["skip"] + week["unrated"]
    return (
        f"This week {week['week']}: approvals {week['approved']} of {decided} "
        f"({_rate(week['approval_rate'])}), COOL {week['cool']} of {rated} "
        f"({_rate(week['cool_rate'])})."
    )


def _sources_line(stats: dict) -> str:
    parts = [
        f"{name} {counts['approved']} of {counts['decided']}"
        for name, counts in sorted(stats["sources"].items())
        if counts["decided"]
    ]
    return "Sources: " + (", ".join(parts) if parts else "none decided") + "."


def tally_text(stats: dict, current_version: str, new_count: int) -> str:
    return (
        f"Taste profile unchanged: {new_count} new episodes since {current_version}, "
        f"need {MIN_NEW_EPISODES}.\n{_week_line(stats)}\n{_sources_line(stats)}"
    )


def ping_text(
    stats: dict,
    version: str,
    applied: bool,
    reason: str,
    old_eval: EvalResult | None,
    new_eval: EvalResult | None,
    diff: dict | None,
    problems: list[str],
) -> str:
    lines = ["Taste profile update."]
    if applied:
        lines.append(f"Applied: yes, version {version}. Tap REVERT to undo.")
    else:
        lines.append(f"Applied: no. {reason}")
    lines.append(_week_line(stats))
    lines.append(_sources_line(stats))
    if old_eval and new_eval:
        lines.append(
            f"Eval on {new_eval.held_out_count} held-out: F1 {old_eval.f1:.2f} -> {new_eval.f1:.2f}, "
            f"golden recall {old_eval.golden_recall:.2f} -> {new_eval.golden_recall:.2f}."
        )
    for problem in problems:
        lines.append(f"Problem: {problem}")
    if diff:
        for heading in ("Prefer", "Avoid"):
            for change in ("added", "removed"):
                for bullet in diff[heading][change]:
                    lines.append(f"{heading} {change}: {bullet}")
    return "\n".join(lines)


def run_consolidate(
    table,
    s3_client,
    ssm_client,
    bucket: str,
    model: str,
    probe_model: str,
    golden_path: str | Path,
    seed_path: str | Path,
    now: datetime | None = None,
    dry_run: bool = False,
) -> dict:
    now = now or datetime.now(UTC)
    episodes = derive_episodes(scan_all(table), load_golden(golden_path), now)
    stats = weekly_stats(episodes)
    pointer = get_pointer(table)
    current_text, current_version = load_profile_text(table, s3_client, bucket, seed_path)

    if pointer is not None:
        new_count = new_episode_count(episodes, pointer.applied_ts)
        if new_count < MIN_NEW_EPISODES:
            text = tally_text(stats, current_version, new_count)
            if not dry_run:
                notify(table, ssm_client, text)
            return {
                "applied": False,
                "tally_only": True,
                "version": None,
                "reason": "too few new episodes",
                "text": text,
                "candidate": None,
                "old_eval": None,
                "new_eval": None,
                "problems": [],
            }

    train, held_out = split(episodes)
    golden = [e for e in episodes if e.golden]
    version = version_now(now)
    raw = _complete(
        model,
        [
            {"role": "system", "content": CONSOLIDATE_SYSTEM},
            {"role": "user", "content": build_consolidate_prompt(current_text, stats, train)},
        ],
    )
    sections, problems = validate_profile(raw, [e.title for e in train])
    candidate = raw
    applied = False
    reason = "validation failed"
    old_eval = new_eval = None
    diff = None
    if sections is not None:
        sections["Source weights"] = source_weights_section(stats)
        candidate = render_profile(version, sections)
        old_eval = evaluate(held_out, golden, current_text, _complete, probe_model)
        new_eval = evaluate(held_out, golden, candidate, _complete, probe_model)
        applied, reason = decide_swap(old_eval, new_eval)
        diff = diff_bullets(current_text, candidate)

    if not dry_run:
        write_profile(s3_client, bucket, candidate_key(version), candidate)
        if applied:
            write_profile(s3_client, bucket, profile_key(version), candidate)
            set_pointer(
                table,
                version,
                profile_key(version),
                pointer,
                "consolidate",
                {
                    "old_f1": old_eval.f1,
                    "new_f1": new_eval.f1,
                    "old_golden_recall": old_eval.golden_recall,
                    "new_golden_recall": new_eval.golden_recall,
                    "held_out_count": new_eval.held_out_count,
                },
                now,
            )
    text = ping_text(stats, version, applied, reason, old_eval, new_eval, diff, problems)
    if not dry_run:
        buttons = [[("REVERT", f"prof:{version}:revert")]] if applied else None
        notify(table, ssm_client, text, buttons=buttons)
    return {
        "applied": applied,
        "tally_only": False,
        "version": version if applied else None,
        "reason": reason,
        "text": text,
        "candidate": candidate,
        "old_eval": old_eval.as_dict() if old_eval else None,
        "new_eval": new_eval.as_dict() if new_eval else None,
        "problems": problems,
    }
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_consolidate.py -q`
Expected: all pass. If `test_first_run_applies_profile_and_pings_with_revert` fails on `Prefer added:`, check that `diff_bullets` compares against the seed text (which has no `## Prefer` section, so every new bullet counts as added).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src/agentlab/consolidate.py tests/test_consolidate.py
git add src/agentlab/consolidate.py tests/test_consolidate.py
git commit -m "feat: weekly consolidation rewrites the taste profile behind an eval gate"
```

---

### Task 7: Worker reads the profile, links videos to proposals, gains `consolidate`

**Files:**
- Modify: `src/agentlab/worker.py`
- Modify: `src/agentlab/preferences.py`
- Modify: `tests/test_worker.py`
- Modify: `tests/test_preferences.py`

**Interfaces:**
- Consumes: Task 1 `repo_files` paths; Task 2 `POLICY_VERSION`, `load_profile_text`; Task 5 `proposals.set_video_key`; Task 6 `run_consolidate`, `DEFAULT_CONSOLIDATE_MODEL`.
- Produces: `agentlab worker consolidate [--dry-run]`; video items carry `pid`; proposals get `video_key`; `_run_explain_track(..., forced_candidate=None, pid=None)`.

- [ ] **Step 1: Trim `src/agentlab/preferences.py`**

Keep only the topic taxonomy. The file must contain exactly: the docstring below, the imports `re` and `SequenceMatcher`, `FUZZY_ALIAS_THRESHOLD`, `TOPIC_ALIASES`, `_plain`, `normalise_topic`, `candidate_topics`. Delete `POLICY_VERSION`, `MIN_TOTAL_LABELS`, `MIN_NON_COOL_LABELS`, `MIN_TOPIC_LABELS`, `PRIOR_STRENGTH`, `MIN_TOPIC_DELTA`, `MAX_TOPICS_IN_CONTEXT`, `FUZZY_TITLE_THRESHOLD`, `RATING_VALUE`, `_canonical_rating`, `_topics`, `_same_title`, `merge_feedback`, `load_golden_feedback`, `load_live_feedback`, `build_preference_context`, `load_preference_context`, and the now unused imports (`json`, `Path`).

New docstring:

```python
"""Fixed topic taxonomy for video ledger items.

Each video item records `topics` so the ledger can be grouped by subject
without embeddings. The gated statistical preference context that used to
live here was replaced by the taste profile (profile.py, consolidate.py)
on 2026-10-02: it needed 30 ratings with 10 non-COOL before it did
anything, and Daniel never taps SKIP.
"""
```

In `tests/test_preferences.py`, change the import block to `from agentlab.preferences import candidate_topics, normalise_topic` (keep `normalise_topic` only if the first test uses it; read the file), and delete every test from `test_preference_context_stays_off_with_too_few_total_labels` to the end of the file, plus any `boto3`/`moto` imports they used.

Run: `uv run pytest tests/test_preferences.py -q`
Expected: the three topic tests pass.

- [ ] **Step 2: Update the worker tests**

In `tests/test_worker.py`:

1. In `_patch_explain_pools`, delete the line `monkeypatch.setattr("agentlab.worker.pick_paper", _fake_pick_paper)`.
2. In `test_explain_track_core_runs_only_core`, delete the line `monkeypatch.setattr("agentlab.worker.pick_paper", _fake_pick_paper)`.
3. Delete `_fake_pick_paper` entirely.
4. Add to the import block: `from agentlab.profile import set_pointer, write_profile` and `from agentlab.proposals import file_proposal, get_proposal`.
5. Replace `test_explain_uses_gated_feedback_and_collects_clarity` with:

```python
def test_explain_ranks_with_the_profile_and_collects_clarity(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="core")
    _set_daytime(monkeypatch)
    write_profile(s3, BUCKET, "profile/v3.md", "PROFILE V3 TEXT")
    set_pointer(table, "v3", "profile/v3.md", None, "consolidate", {}, datetime(2026, 8, 18, 16, 0, tzinfo=UTC))
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    captured = {}

    def rank(candidates, interests_text, complete, mode="core", model=None, limit=3):
        captured["interests"] = interests_text
        captured["mode"] = mode
        return candidates[:limit]

    monkeypatch.setattr("agentlab.worker.rank_papers", rank)
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)
    real_notify = worker_mod.notify

    def notify_after_ledger(*args, **kwargs):
        assert any(item.get("sk") == "video" for item in table.scan()["Items"])
        return real_notify(*args, **kwargs)

    monkeypatch.setattr("agentlab.worker.notify", notify_after_ledger)

    result = runner.invoke(app, ["worker", "explain"])

    assert result.exit_code == 0, result.output
    assert captured["interests"] == "PROFILE V3 TEXT"
    assert captured["mode"] == "core"
    _url, kwargs = next(call for call in telegram_calls if call[0].endswith("/sendVideo"))
    assert kwargs["data"]["caption"].startswith("[CORE] Core Candidate Paper\n")
    keyboard = json.loads(kwargs["data"]["reply_markup"])["inline_keyboard"]
    assert [button["text"] for button in keyboard[0]] == ["COOL", "MEH", "SKIP"]
    assert [button["text"] for button in keyboard[1]] == ["CLEAR", "UNCLEAR"]
    row = next(item for item in table.scan()["Items"] if item.get("sk") == "video")
    assert row["topics"]
    assert row["selection_policy"] == "taste-profile-v1"
    assert row["feedback_status"] == "profile:v3"
    assert row["baseline_rank"] == 1
    assert row["pid"] is None
    assert row["clarity"] is None


def test_explain_falls_back_to_the_seed_profile_without_a_pointer(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="core")
    _set_daytime(monkeypatch)
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    captured = {}

    def rank(candidates, interests_text, complete, mode="core", model=None, limit=3):
        captured["interests"] = interests_text
        return candidates[:limit]

    monkeypatch.setattr("agentlab.worker.rank_papers", rank)
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)

    result = runner.invoke(app, ["worker", "explain"])

    assert result.exit_code == 0, result.output
    assert "Daniel wants the single best new paper" in captured["interests"]
    row = next(item for item in table.scan()["Items"] if item.get("sk") == "video")
    assert row["feedback_status"] == "profile:seed"
```

6. After `test_explain_operator_replay_bypasses_seen_filter`, add:

```python
def test_explain_operator_replay_links_video_to_proposal(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    replay_url = "https://arxiv.org/abs/2510.03215"
    _set_explain_env(monkeypatch, track="core")
    monkeypatch.setenv("EXPLAIN_URL", replay_url)
    monkeypatch.setenv("EXPLAIN_TITLE", "Cache-to-Cache")
    monkeypatch.setenv("PID", "prop-1")
    _set_daytime(monkeypatch)
    file_proposal(table, "prop-1", "Cache-to-Cache", "why", replay_url, "d", "new_hypothesis")
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)

    result = runner.invoke(app, ["worker", "explain"])

    assert result.exit_code == 0, result.output
    video = next(item for item in table.scan()["Items"] if item.get("sk") == "video")
    assert video["pid"] == "prop-1"
    assert get_proposal(table, "prop-1")["video_key"] == video["video_key"]
```

7. After `test_propose_command_flushes_then_proposes`, add:

```python
def test_consolidate_command_wires_env_and_dry_run(monkeypatch):
    seen = {}

    def fake_run(table, s3_client, ssm_client, bucket, model, probe_model, golden_path, seed_path, now=None, dry_run=False):
        seen.update(bucket=bucket, model=model, probe_model=probe_model, dry_run=dry_run)
        seen["golden"] = str(golden_path)
        seen["seed"] = str(seed_path)
        return {"text": "Taste profile update.\nApplied: no. test", "candidate": "# Daniel's taste profile\nx"}

    monkeypatch.setattr("agentlab.consolidate.run_consolidate", fake_run)

    result = runner.invoke(
        app,
        ["worker", "consolidate", "--dry-run"],
        env={
            "STATE_TABLE": TABLE,
            "RESULTS_BUCKET": BUCKET,
            "CONSOLIDATE_MODEL": "bedrock/sonnet-x",
            "PROPOSER_MODEL": "bedrock/probe-x",
        },
    )

    assert result.exit_code == 0, result.output
    assert seen["bucket"] == BUCKET
    assert seen["model"] == "bedrock/sonnet-x"
    assert seen["probe_model"] == "bedrock/probe-x"
    assert seen["dry_run"] is True
    assert seen["golden"].endswith("docs/golden-papers.jsonl")
    assert seen["seed"].endswith("docs/interests.md")
    assert "Applied: no. test" in result.output
    assert "candidate profile" in result.output
```

Run: `uv run pytest tests/test_worker.py -q -k "explain or consolidate"`
Expected: the new tests fail (AttributeError on `pid`, missing command).

- [ ] **Step 3: Edit `src/agentlab/worker.py`**

Imports: delete the block

```python
from agentlab.preferences import (
    POLICY_VERSION,
    candidate_topics,
    load_preference_context,
)
```

and add

```python
from agentlab.preferences import candidate_topics
from agentlab.profile import POLICY_VERSION, load_profile_text
from agentlab.proposals import set_video_key
from agentlab.repo_files import CLASSICS_PATH, GOLDEN_PAPERS_PATH, INTERESTS_PATH
```

Remove `pick_paper` from the `agentlab.scene_plan` import.
Delete the four lines `_REPO_ROOT = ...`, `CLASSICS_PATH = ...`, `INTERESTS_PATH = ...`, `GOLDEN_PAPERS_PATH = ...` and the comment above them, and delete `_load_interests`.
Keep `GOLDEN_PAPERS_PATH` imported even though the worker no longer reads it directly, because the consolidate command passes it on.

In `_run_explain_track`, add the keyword parameter `pid: str | None = None` after `forced_candidate`, and replace the block from `interests = _load_interests()` through `selection_probability = None` (the whole `else:` branch body after `mode = ...`) with:

```python
        mode = "novel" if track == "novel" else "core"
        profile_text, profile_version = load_profile_text(table, s3_client, bucket, INTERESTS_PATH)
        baseline_ranking = rank_papers(
            fresh, profile_text, complete, mode=mode, model=pick_model, limit=len(fresh)
        )
        if not baseline_ranking:
            return "empty"
        candidate = baseline_ranking[0]
        feedback_status = f"profile:{profile_version}"
        candidate_set = [
            _candidate_record(
                item,
                source_rank,
                baseline_ranking.index(item) + 1 if item in baseline_ranking else None,
            )
            for source_rank, item in enumerate(fresh, start=1)
        ]
        baseline_selection = next(
            record for record in candidate_set if record["baseline_rank"] == 1
        )
        baseline_rank = 1
        exploration_status = "none"
        # The model ranker does not expose a calibrated choice probability.
        # Store that fact explicitly instead of inventing a propensity.
        selection_probability = None
```

In the video ledger `put_item`, add `"pid": pid,` right after `"track": track,`.
After the `notify(...)` call at the end of the `with tempfile.TemporaryDirectory(...)` block, add:

```python
        if pid:
            set_video_key(table, pid, video_key)
```

In `explain_command`, after the `forced_candidate = {...}` block, add `pid = os.environ.get("PID", "").strip() or None`, and before the loop set `pid = None` when `forced_candidate is None`:

```python
    pid = os.environ.get("PID", "").strip() or None
    if forced_candidate is None:
        pid = None
```

Pass it to the track call: `_run_explain_track(..., aws_mtd_cost, forced_candidate, pid=pid)`.

Add the command after `propose_command`:

```python
@worker_app.command("consolidate")
def consolidate_command(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Print the candidate profile and eval. Write nothing. Ping nobody."
    ),
) -> None:
    """Weekly taste profile rewrite (Sunday 18:00 Amsterdam), gated by the probe eval."""
    from agentlab.consolidate import DEFAULT_CONSOLIDATE_MODEL, run_consolidate
    from agentlab.proposer import DEFAULT_PROPOSER_MODEL

    state_table = _require_env("STATE_TABLE")
    results_bucket = _require_env("RESULTS_BUCKET")
    model = os.environ.get("CONSOLIDATE_MODEL", DEFAULT_CONSOLIDATE_MODEL)
    probe_model = os.environ.get("PROPOSER_MODEL", DEFAULT_PROPOSER_MODEL)
    table = boto3.resource("dynamodb").Table(state_table)
    result = run_consolidate(
        table,
        boto3.client("s3"),
        boto3.client("ssm"),
        results_bucket,
        model,
        probe_model,
        GOLDEN_PAPERS_PATH,
        INTERESTS_PATH,
        dry_run=dry_run,
    )
    typer.echo(result["text"])
    if result.get("candidate"):
        typer.echo("\n--- candidate profile ---\n" + result["candidate"])
```

- [ ] **Step 4: Run the worker and preference tests**

Run: `uv run pytest tests/test_worker.py tests/test_preferences.py -q`
Expected: all pass. The classic-track tests read `docs/classics.json` through the imported `CLASSICS_PATH`.

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: green. If `tests/test_cli.py` or any other file imported a removed name from `preferences`, fix that import.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests
git add src/agentlab/worker.py src/agentlab/preferences.py tests/test_worker.py tests/test_preferences.py
git commit -m "feat: picker reads the taste profile, approval videos carry their proposal id, worker consolidate"
```

---

### Task 8: Approvals Lambda passes the proposal into the video and handles REVERT

**Files:**
- Modify: `infra/lambda/approvals_webhook.py`
- Modify: `tests/test_approvals_webhook.py`

**Interfaces:**
- Consumes: the pointer item shape from Task 2 (`experiment_id="profile#current"`, `sk="profile"`, fields `version`, `s3_key`, `previous_version`, `previous_s3_key`, `applied_ts`, `source`, `eval`). The Lambda imports nothing from the package; it re-implements the flip.
- Produces: ECS override env entries `PID` and `EXPLAIN_TITLE` on approval; callback namespace `prof:<version>:revert`.

- [ ] **Step 1: Add the failing tests to `tests/test_approvals_webhook.py`**

Add after `_vid_keyboard`:

```python
_POINTER_KEY = {"experiment_id": "profile#current", "sk": "profile"}


def _file_pointer(table, version, previous_version):
    table.put_item(
        Item={
            **_POINTER_KEY,
            "version": version,
            "s3_key": f"profile/{version}.md",
            "previous_version": previous_version,
            "previous_s3_key": f"profile/{previous_version}.md" if previous_version else None,
            "applied_ts": "2026-10-05T16:00:00.000000Z",
            "source": "consolidate",
            "eval": "{}",
        }
    )


def _get_pointer(table):
    return table.get_item(Key=_POINTER_KEY).get("Item")
```

In `test_approve_without_submit_body_builds_a_video_of_the_citation`, add after the `EXPLAIN_URL` assertion:

```python
    assert {"name": "PID", "value": "p1"} in env
    assert {"name": "EXPLAIN_TITLE", "value": "Title"} in env
```

Add at the end of the file:

```python
def test_prof_revert_flips_pointer_to_previous(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_pointer(table, "v2", "v1")
    keyboard = [[{"text": "REVERT", "callback_data": "prof:v2:revert"}]]

    response = webhook.handler(make_event(data="prof:v2:revert", keyboard=keyboard), None)

    assert response["statusCode"] == 200
    item = _get_pointer(table)
    assert item["version"] == "v1"
    assert item["s3_key"] == "profile/v1.md"
    assert item["previous_version"] is None
    assert item["previous_s3_key"] is None
    assert item["source"] == "revert"
    toasts = [c[1]["text"] for c in recorder if c[0] == "answerCallbackQuery"]
    assert toasts == ["Reverted to v1"]
    edits = [c[1] for c in recorder if c[0] == "editMessageReplyMarkup"]
    assert edits == [{"chat_id": 123456789, "message_id": 7, "reply_markup": {"inline_keyboard": []}}]


def test_prof_revert_stale_version_or_second_tap_does_nothing(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_pointer(table, "v2", "v1")

    webhook.handler(make_event(data="prof:v1:revert"), None)
    assert _get_pointer(table)["version"] == "v2"

    webhook.handler(make_event(data="prof:v2:revert"), None)
    assert _get_pointer(table)["version"] == "v1"

    webhook.handler(make_event(data="prof:v2:revert"), None)
    assert _get_pointer(table)["version"] == "v1"

    toasts = [c[1]["text"] for c in recorder if c[0] == "answerCallbackQuery"]
    assert toasts == ["Nothing to revert", "Reverted to v1", "Nothing to revert"]


def test_prof_revert_without_previous_does_nothing(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_pointer(table, "v1", None)

    webhook.handler(make_event(data="prof:v1:revert"), None)

    assert _get_pointer(table)["version"] == "v1"
    toasts = [c[1]["text"] for c in recorder if c[0] == "answerCallbackQuery"]
    assert toasts == ["Nothing to revert"]


def test_prof_revert_foreign_user_ignored(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_pointer(table, "v2", "v1")

    response = webhook.handler(make_event(data="prof:v2:revert", from_id=999), None)

    assert response["statusCode"] == 200
    assert _get_pointer(table)["version"] == "v2"
    assert recorder == []
```

Run: `uv run pytest tests/test_approvals_webhook.py -q`
Expected: the four new tests fail and the PID assertion fails.

- [ ] **Step 2: Edit `infra/lambda/approvals_webhook.py`**

In `_build_video`, read the title with the citation and pass both:

```python
    citation = str(item.get("citation") or "").strip()
    title = str(item.get("title") or "").strip() or citation
    if not citation.startswith("http"):
        return None
```

and change the `environment` list to:

```python
                    "environment": [
                        {"name": "TRACK", "value": "core"},
                        {"name": "EXPLAIN_URL", "value": citation},
                        {"name": "EXPLAIN_TITLE", "value": title},
                        {"name": "PID", "value": pid},
                    ],
```

Add after `_strip_vid_buttons`:

```python
# prof:<version>:revert - the taste profile REVERT tap. The pointer item
# mirrors src/agentlab/profile.py (profile#current / profile); the flip
# below is the only write path that does not go through that module, so
# change both in the same commit or neither.
_POINTER_KEY = {"experiment_id": "profile#current", "sk": "profile"}


def _revert_profile(table, version: str) -> str | None:
    """Flip the pointer back to its previous version. Returns the version
    now current, or None when the tap names a stale version or there is
    nothing to go back to. The conditional update makes a double tap safe."""
    item = table.get_item(Key=_POINTER_KEY).get("Item") or {}
    previous_version = item.get("previous_version")
    previous_key = item.get("previous_s3_key")
    if item.get("version") != version or not previous_version or not previous_key:
        return None
    try:
        table.update_item(
            Key=_POINTER_KEY,
            UpdateExpression=(
                "SET #v = :pv, s3_key = :pk, previous_version = :none, "
                "previous_s3_key = :none, applied_ts = :ts, #src = :src"
            ),
            ConditionExpression="#v = :cur",
            ExpressionAttributeNames={"#v": "version", "#src": "source"},
            ExpressionAttributeValues={
                ":pv": previous_version,
                ":pk": previous_key,
                ":none": None,
                ":ts": _now(),
                ":src": "revert",
                ":cur": version,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return None
        raise
    return previous_version


def _strip_prof_buttons(markup: dict) -> list:
    rows = markup.get("inline_keyboard") or []
    return [
        row
        for row in rows
        if not any(str(button.get("callback_data", "")).startswith("prof:") for button in row)
    ]


def _handle_prof(callback, table, version: str) -> dict:
    reverted = _revert_profile(table, version)
    toast = f"Reverted to {reverted}" if reverted else "Nothing to revert"
    try:
        _telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback["id"], "text": toast[:200]},
        )
        message = callback.get("message")
        if message and reverted:
            _telegram(
                "editMessageReplyMarkup",
                {
                    "chat_id": message["chat"]["id"],
                    "message_id": message["message_id"],
                    "reply_markup": {
                        "inline_keyboard": _strip_prof_buttons(message.get("reply_markup") or {})
                    },
                },
            )
    except Exception as exc:  # noqa: BLE001 - message cosmetics must never undo a recorded flip
        print(f"telegram call failed after profile revert: {exc}")
    return _ok()
```

In `handler`, after the `vid` branch and before the `prop` check, add:

```python
    if len(parts) == 3 and parts[0] == "prof" and parts[2] == "revert":
        table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
        return _handle_prof(callback, table, parts[1])
```

Update the module docstring's first paragraph to mention the third namespace: "Three callback namespaces: prop:* verdicts, vid:* video ratings, prof:* taste profile revert."

- [ ] **Step 3: Run the tests**

Run: `uv run pytest tests/test_approvals_webhook.py -q`
Expected: all pass.

- [ ] **Step 4: Lint and commit**

```bash
uv run ruff check infra/lambda/approvals_webhook.py tests/test_approvals_webhook.py
git add infra/lambda/approvals_webhook.py tests/test_approvals_webhook.py
git commit -m "feat: approval video carries its proposal id and title, REVERT tap flips the profile pointer"
```

---

### Task 9: Golden sheet converter, classics list, committed golden jsonl

**Files:**
- Create: `scripts/build_golden_papers.py`
- Create: `docs/golden-papers.jsonl` (generated by the script)
- Modify: `docs/classics.json`
- Modify: `docs/golden-papers-labeling.md` (entry 26 title only)
- Test: `tests/test_build_golden_papers.py`

**Interfaces:**
- Consumes: Task 1 `agentlab.episodes.canonical_rating`.
- Produces: `docs/golden-papers.jsonl` lines `{"title", "url", "rating", "why"}` read by `episodes.load_golden`; seven more classics for the proposer's foundational slot.

- [ ] **Step 1: Extend `docs/classics.json`**

Append these seven objects to the JSON array, after the DPO entry, keeping the existing format:

```json
  {
    "title": "SWE-bench: Can Language Models Resolve Real-World GitHub Issues?",
    "url": "https://arxiv.org/abs/2310.06770",
    "year": 2023
  },
  {
    "title": "Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena",
    "url": "https://arxiv.org/abs/2306.05685",
    "year": 2023
  },
  {
    "title": "STaR: Bootstrapping Reasoning With Reasoning",
    "url": "https://arxiv.org/abs/2203.14465",
    "year": 2022
  },
  {
    "title": "Evaluating Large Language Models Trained on Code",
    "url": "https://arxiv.org/abs/2107.03374",
    "year": 2021
  },
  {
    "title": "DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines",
    "url": "https://arxiv.org/abs/2310.03714",
    "year": 2023
  },
  {
    "title": "MemGPT: Towards LLMs as Operating Systems",
    "url": "https://arxiv.org/abs/2310.08560",
    "year": 2023
  },
  {
    "title": "The Curse of Recursion: Training on Generated Data Makes Models Forget",
    "url": "https://arxiv.org/abs/2305.17493",
    "year": 2023
  }
```

Run: `uv run python -c "import json; d=json.load(open('docs/classics.json')); print(len(d))"`
Expected: `27`.

- [ ] **Step 2: Rename entry 26 in `docs/golden-papers-labeling.md`**

Change the line `26. model collapse` to `26. The Curse of Recursion: Training on Generated Data Makes Models Forget`. Leave its label line as it is.

- [ ] **Step 3: Write the failing tests `tests/test_build_golden_papers.py`**

```python
"""The golden sheet converter: Daniel's markdown labels become the jsonl the
consolidator and the probe eval read."""

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_SCRIPT = Path(__file__).parent.parent / "scripts" / "build_golden_papers.py"
spec = importlib.util.spec_from_file_location("build_golden_papers", _SCRIPT)
build_golden_papers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_golden_papers)

SHEET = """# Golden paper set: labeling sheet

Intro text with no numbers.

## Pre-filled

1. Recursive Self-Improvement in AI (the RSI survey, arXiv 2607.07663)
   Label: IMPLEMENT. Why: adopted as the lab's design rubric.
2. Eureka-style meta-agent orchestration (arXiv 2608.19047)
   Label: LEARNED. Why: interesting frame, nothing implementable.

## For you to label

6. Attention Is All You Need (the Transformer paper)
   Label: yes. Why: The GOAT paper.
7. Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
   Label: Yes. . Why: Very important.
8. Constitutional AI: Harmlessness from AI Feedback
   Label: no. Why: Doesn't really have a catchy title.
9. Something Unlabelled
   Label: ___. Why: ___
10. STaR: Self-Taught Reasoner (bootstrapping reasoning with reasoning)
    Label: Yeah,  . Why: GOAT thing.

## Your own additions

26. The Curse of Recursion: Training on Generated Data Makes Models Forget
    Label: seems cool.implement
27. ___
    Label: ___. Why: ___
"""

CLASSICS = [
    {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "year": 2017},
    {"title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models", "url": "https://arxiv.org/abs/2201.11903", "year": 2022},
    {"title": "Constitutional AI: Harmlessness from AI Feedback", "url": "https://arxiv.org/abs/2212.08073", "year": 2022},
    {"title": "The Curse of Recursion: Training on Generated Data Makes Models Forget", "url": "https://arxiv.org/abs/2305.17493", "year": 2023},
]


def test_parse_sheet_reads_titles_labels_and_whys():
    entries = build_golden_papers.parse_sheet(SHEET)
    assert [e["number"] for e in entries] == [1, 2, 6, 7, 8, 9, 10, 26, 27]
    assert entries[2]["raw_title"] == "Attention Is All You Need (the Transformer paper)"
    assert entries[2]["label"] == "yes."
    assert entries[2]["why"] == "The GOAT paper."
    assert entries[7]["label"] == "seems cool.implement"
    assert entries[7]["why"] == ""


def test_build_maps_labels_attaches_urls_and_warns():
    records, warnings = build_golden_papers.build(build_golden_papers.parse_sheet(SHEET), CLASSICS)
    by_title = {r["title"]: r for r in records}
    assert by_title["Recursive Self-Improvement in AI"] == {
        "title": "Recursive Self-Improvement in AI",
        "url": "https://arxiv.org/abs/2607.07663",
        "rating": "COOL",
        "why": "adopted as the lab's design rubric.",
    }
    assert by_title["Eureka-style meta-agent orchestration"]["rating"] == "MEH"
    assert by_title["Attention Is All You Need"]["url"] == "https://arxiv.org/abs/1706.03762"
    assert by_title["Attention Is All You Need"]["rating"] == "COOL"
    assert by_title["Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"]["rating"] == "COOL"
    assert by_title["Constitutional AI: Harmlessness from AI Feedback"]["rating"] == "SKIP"
    assert by_title["STaR: Self-Taught Reasoner"]["rating"] == "COOL"
    assert by_title["STaR: Self-Taught Reasoner"]["url"] is None
    assert by_title["The Curse of Recursion: Training on Generated Data Makes Models Forget"]["rating"] == "COOL"
    assert "Something Unlabelled" not in by_title
    assert len(records) == 7
    assert any("entry 9" in w for w in warnings)
    assert any("entry 27" in w for w in warnings)


def test_main_writes_jsonl(tmp_path, monkeypatch):
    sheet = tmp_path / "sheet.md"
    sheet.write_text(SHEET, encoding="utf-8")
    classics = tmp_path / "classics.json"
    classics.write_text(json.dumps(CLASSICS), encoding="utf-8")
    out = tmp_path / "golden.jsonl"
    monkeypatch.setattr(build_golden_papers, "SHEET_PATH", sheet)
    monkeypatch.setattr(build_golden_papers, "CLASSICS_PATH", classics)
    monkeypatch.setattr(build_golden_papers, "OUT_PATH", out)

    build_golden_papers.main()

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 7
    assert {"title", "url", "rating", "why"} == set(lines[0])


def test_repo_sheet_builds_without_warnings():
    """The committed sheet must convert cleanly, so the image ships every label."""
    records, warnings = build_golden_papers.build(
        build_golden_papers.parse_sheet(build_golden_papers.SHEET_PATH.read_text(encoding="utf-8")),
        json.loads(build_golden_papers.CLASSICS_PATH.read_text(encoding="utf-8")),
    )
    assert warnings == []
    assert len(records) == 26
    assert sum(1 for r in records if r["rating"] == "SKIP") == 1
    committed = [json.loads(line) for line in build_golden_papers.OUT_PATH.read_text(encoding="utf-8").splitlines()]
    assert committed == records
```

Run: `uv run pytest tests/test_build_golden_papers.py -q`
Expected: fails because the script does not exist.

- [ ] **Step 4: Write `scripts/build_golden_papers.py`**

```python
"""Build docs/golden-papers.jsonl from Daniel's labeling sheet.

The sheet (docs/golden-papers-labeling.md) is hand-edited markdown: a
numbered title line, then a `Label: <word>. Why: <sentence>` line. Labels
map through agentlab.episodes.canonical_rating, so "yes", "Yeah,",
"IMPLEMENT", and "seems cool.implement" all become COOL, "learned" becomes
MEH, and "no" becomes SKIP. A URL is attached when the title names an arXiv
id or fuzzy-matches an entry in docs/classics.json; otherwise it is null and
the paper is identified by title. Run from the repo root:

    uv run python scripts/build_golden_papers.py

Commit the jsonl; both worker images ship it.
"""

from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentlab.episodes import canonical_rating  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
SHEET_PATH = REPO_ROOT / "docs" / "golden-papers-labeling.md"
CLASSICS_PATH = REPO_ROOT / "docs" / "classics.json"
OUT_PATH = REPO_ROOT / "docs" / "golden-papers.jsonl"

ENTRY_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
LABEL_RE = re.compile(r"^\s*Label:\s*(?P<label>.*?)\s*(?:Why:\s*(?P<why>.*))?$")
ARXIV_RE = re.compile(r"arXiv\s+(\d{4}\.\d{4,5})", re.IGNORECASE)
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
TITLE_MATCH = 0.86


def parse_sheet(text: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("Label:"):
            match = LABEL_RE.match(line)
            if match and current is not None:
                current["label"] = (match.group("label") or "").strip()
                current["why"] = (match.group("why") or "").strip()
            continue
        match = ENTRY_RE.match(line)
        if match:
            current = {
                "number": int(match.group(1)),
                "raw_title": match.group(2).strip(),
                "label": "",
                "why": "",
            }
            entries.append(current)
    return entries


def clean_title(raw_title: str) -> str:
    return TRAILING_PAREN_RE.sub("", raw_title).strip()


def arxiv_url(raw_title: str) -> str | None:
    match = ARXIV_RE.search(raw_title)
    return f"https://arxiv.org/abs/{match.group(1)}" if match else None


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def match_classic(title: str, classics: list[dict]) -> str | None:
    best_ratio, best_url = 0.0, None
    for entry in classics:
        ratio = SequenceMatcher(None, _plain(title), _plain(entry["title"])).ratio()
        if ratio > best_ratio:
            best_ratio, best_url = ratio, entry["url"]
    return best_url if best_ratio >= TITLE_MATCH else None


def build(entries: list[dict], classics: list[dict]) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    warnings: list[str] = []
    for entry in entries:
        title = clean_title(entry["raw_title"])
        rating = canonical_rating(entry["label"])
        if not title or title.strip("_") == "" or rating is None:
            warnings.append(f"entry {entry['number']}: label {entry['label']!r} not mapped, skipped")
            continue
        records.append(
            {
                "title": title,
                "url": arxiv_url(entry["raw_title"]) or match_classic(title, classics),
                "rating": rating,
                "why": entry["why"],
            }
        )
    return records, warnings


def main() -> None:
    entries = parse_sheet(SHEET_PATH.read_text(encoding="utf-8"))
    classics = json.loads(CLASSICS_PATH.read_text(encoding="utf-8"))
    records, warnings = build(entries, classics)
    OUT_PATH.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    for warning in warnings:
        print(warning, file=sys.stderr)
    print(f"wrote {len(records)} golden papers to {OUT_PATH}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Generate the jsonl and run the tests**

Run: `uv run python scripts/build_golden_papers.py && uv run pytest tests/test_build_golden_papers.py -q`
Expected: `wrote 26 golden papers`, no warnings on stderr, all tests pass. If `test_repo_sheet_builds_without_warnings` reports a warning, read the sheet line it names and fix the parser, not the sheet (the sheet is Daniel's data; only entry 26's title was renamed).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check scripts/build_golden_papers.py tests/test_build_golden_papers.py
git add scripts/build_golden_papers.py tests/test_build_golden_papers.py docs/golden-papers.jsonl docs/classics.json docs/golden-papers-labeling.md
git commit -m "feat: golden sheet converter, seven more classics, committed golden labels"
```

---

### Task 10: Infra for the weekly schedule and the profile prefix

**Files:**
- Modify: `infra/scheduler.tf`
- Modify: `infra/iam.tf`

**Interfaces:**
- Produces: schedule `agentlab-consolidate-weekly`; proposer task role may read and write `profile/*` and read `proposals/*` and may `GetItem` on the state table; explain task role may read `profile/*`.

- [ ] **Step 1: Add the schedule to `infra/scheduler.tf`**

In `locals.proposer_schedules`, add after `propose-midday`:

```hcl
    consolidate-weekly = {
      cron    = "cron(0 18 ? * SUN *)"
      command = ["worker", "consolidate"]
    }
```

- [ ] **Step 2: Grant the proposer role what consolidate needs in `infra/iam.tf`**

In `aws_iam_role_policy.proposer_task`, replace the `Ledger` statement's comment and action list with:

```hcl
      {
        # GetItem reads the taste profile pointer (profile.py get_pointer);
        # PutItem/Scan/Query/Delete cover propose, flush-pings, and consolidate.
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
```

Replace the `ProposalDocs` statement with:

```hcl
      {
        Sid    = "ProposalDocs"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.results.arn}/proposals/*"
      },
      {
        # The taste profile: consolidate writes versions and candidates,
        # propose reads the current version named by the pointer.
        Sid    = "TasteProfile"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject"]
        Resource = "${aws_s3_bucket.results.arn}/profile/*"
      },
```

In `aws_iam_role_policy.explain_task`, add `"${aws_s3_bucket.results.arn}/profile/*",` to the `VideoArtifacts` resource list, after the `stories/*` line. The explain task only reads it, and the statement already allows GetObject; the extra PutObject grant on the prefix is accepted to keep one statement.

- [ ] **Step 3: Format check**

Run: `cd infra && terraform fmt -check -diff scheduler.tf iam.tf; cd ..`
Expected: no diff. If `terraform fmt` rewrites alignment, accept its output.

- [ ] **Step 4: Commit**

```bash
git add infra/scheduler.tf infra/iam.tf
git commit -m "feat: weekly consolidate schedule and profile prefix grants"
```

---

### Task 11: Integration, real path, deploy (orchestrator)

**Files:** none new.

- [ ] **Step 1: Whole suite and lint**

Run: `uv run ruff check src tests scripts && uv run pytest -q`
Expected: clean and green.

- [ ] **Step 2: Dry run against the live table**

Run from the worktree with live AWS credentials:

```bash
STATE_TABLE=agentlab-state RESULTS_BUCKET=<results bucket from infra/outputs> \
PROPOSER_MODEL=bedrock/arn:aws:bedrock:eu-west-1:891377302765:application-inference-profile/kpbqsnaqf2ti \
CONSOLIDATE_MODEL=bedrock/arn:aws:bedrock:eu-west-1:891377302765:application-inference-profile/mfzwa25maf8z \
uv run agentlab worker consolidate --dry-run
```

Expected: stats for every week since 2026-W34, a candidate profile with real titles, an eval line. Read the candidate. If the Avoid list is dominated by the August silent stretch, that is the known risk; it is acceptable for version 1 because the diff is visible and REVERT exists.

- [ ] **Step 3: Merge and build**

```bash
git -C /Users/danielpuri/Desktop/Projects/agentlab fetch origin
git -C /Users/danielpuri/Desktop/Projects/agentlab/.worktrees/taste-flywheel rebase origin/main
```

Then merge `taste-flywheel` into `main` fast-forward, push, and from the main checkout run `scripts/build_and_push_image.sh` and `scripts/build_and_push_video_image.sh`.

- [ ] **Step 4: Terraform**

Write `infra/runtime.auto.tfvars` in the main checkout with `proposer_model` set to the `agentlab-sonnet` profile ARN and `deep_read_model` and `pick_model` set to the values the live task definitions carry (`aws ecs describe-task-definition --task-definition agentlab-explain`). Run `terraform plan`, read every change, then `terraform apply`.

- [ ] **Step 5: First real consolidate**

Run the schedule target once by hand (`aws scheduler` cannot trigger on demand; use `aws ecs run-task` with the proposer task definition and command override `["worker", "consolidate"]`), or run the command locally with live credentials. Confirm the pointer item exists, the S3 version exists, and the Telegram ping arrived with a REVERT button.

- [ ] **Step 6: First real propose**

Run the proposer once by hand the same way. Confirm the message shows three real titles with lenses and that `proposals/days/<today>/sources.json` exists. Approve one from the phone and confirm the video item carries the `pid`.
