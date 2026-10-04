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
        # Silence counts against a pick the lab chose or Daniel approved. An
        # operator replay with no proposal behind it is a test run, so its
        # silence says nothing about taste. A rating on it still counts.
        if item.get("feedback_status") == "operator-replay" and not item.get("pid"):
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
    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()  # a bucket hash, not security
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
