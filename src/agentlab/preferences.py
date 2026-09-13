"""Conservative preference learning from Daniel's paper ratings.

The selector already uses a language model for semantic ranking.
This module keeps adaptation around that model deterministic and small:
ratings are normalized, papers are assigned to a fixed topic taxonomy,
and sparse topic estimates are pulled toward the global mean.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

POLICY_VERSION = "topic-feedback-v1"
MIN_TOTAL_LABELS = 30
MIN_NON_COOL_LABELS = 10
MIN_TOPIC_LABELS = 8
PRIOR_STRENGTH = 8
MIN_TOPIC_DELTA = 0.05
MAX_TOPICS_IN_CONTEXT = 6
FUZZY_ALIAS_THRESHOLD = 0.72
FUZZY_TITLE_THRESHOLD = 0.86

RATING_VALUE = {"COOL": 1.0, "MEH": 0.5, "SKIP": 0.0}

TOPIC_ALIASES: dict[str, tuple[str, ...]] = {
    "agent-evals": (
        "agent eval",
        "agent evaluation",
        "benchmark",
        "grader",
        "model grader",
        "model graders",
        "llm judge",
        "model judge",
        "verification",
        "test technique",
        "swe bench",
    ),
    "self-improvement": (
        "self improvement",
        "self improving",
        "recursive self improvement",
        "self play",
        "reflection",
        "reflexion",
        "policy optimization",
        "reinforcement learning",
    ),
    "agent-memory": (
        "agent memory",
        "memory",
        "retrieval",
        "rag",
        "compaction",
        "context compression",
    ),
    "agent-systems": (
        "agent system",
        "agent harness",
        "tool use",
        "multi agent",
        "computer use",
        "gui agent",
        "orchestration",
        "planning",
    ),
    "model-training": (
        "model training",
        "post training",
        "rlhf",
        "alignment",
        "scaling law",
        "transformer",
        "reasoning model",
        "chain of thought",
    ),
    "robotics-world-models": (
        "robotics",
        "robot",
        "world model",
        "simulation",
        "3d world",
        "embodied",
    ),
    "media-generation": (
        "image generation",
        "video generation",
        "audio video",
        "diffusion",
        "multimodal generation",
    ),
    "other": (),
}


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(text).lower()))


def normalise_topic(raw: str) -> str:
    """Map a fuzzy topic alias to one stable taxonomy value."""
    value = _plain(raw)
    candidates: list[tuple[float, str]] = []
    for topic, aliases in TOPIC_ALIASES.items():
        names = (topic.replace("-", " "), *aliases)
        for name in names:
            score = SequenceMatcher(None, value, _plain(name)).ratio()
            candidates.append((score, topic))
    score, topic = max(candidates, default=(0.0, "other"))
    return topic if score >= FUZZY_ALIAS_THRESHOLD else "other"


def candidate_topics(candidate: dict) -> list[str]:
    """Assign ordered topics using fixed rules over stored source text."""
    text = _plain(
        " ".join(
            str(candidate.get(key, ""))
            for key in ("title", "summary", "notes")
        )
    )
    matches: list[tuple[int, str]] = []
    for topic, aliases in TOPIC_ALIASES.items():
        if topic == "other":
            continue
        padded = f" {text} "
        hits = sum(1 for alias in aliases if f" {_plain(alias)} " in padded)
        if hits:
            matches.append((hits, topic))
    matches.sort(key=lambda pair: (-pair[0], pair[1]))
    return [topic for _hits, topic in matches] or ["other"]


def _canonical_rating(raw: object) -> str | None:
    value = str(raw or "").upper().strip()
    aliases = {
        "YES": "COOL",
        "YEAH": "COOL",
        "IMPLEMENT": "COOL",
        "LEARNED": "MEH",
        "NO": "SKIP",
    }
    value = aliases.get(value, value)
    return value if value in RATING_VALUE else None


def _topics(item: dict) -> list[str]:
    raw = item.get("topics")
    if isinstance(raw, (list, tuple, set)):
        normalized = [normalise_topic(value) for value in raw]
        return list(dict.fromkeys(normalized)) or ["other"]
    return candidate_topics(item)


def _same_title(left: str, right: str) -> bool:
    return SequenceMatcher(None, _plain(left), _plain(right)).ratio() >= FUZZY_TITLE_THRESHOLD


def merge_feedback(golden: list[dict], live: list[dict]) -> list[dict]:
    """Merge sources, preferring the latest live tap for near-duplicate titles."""
    merged = [
        item
        for item in golden
        if not any(_same_title(item.get("title", ""), current.get("title", "")) for current in live)
    ]
    merged.extend(live)
    return merged


def load_golden_feedback(path: str | Path) -> list[dict]:
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
        if isinstance(item, dict) and _canonical_rating(item.get("rating") or item.get("label")):
            items.append(item)
    return items


def load_live_feedback(table) -> list[dict]:
    items = []
    start_key = None
    while True:
        request = {
            "FilterExpression": (
                "begins_with(experiment_id, :prefix) AND attribute_exists(rating)"
            ),
            "ExpressionAttributeValues": {":prefix": "video#"},
            "ProjectionExpression": "title, rating, topics, track, rating_ts",
        }
        if start_key:
            request["ExclusiveStartKey"] = start_key
        response = table.scan(**request)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")
        if not start_key:
            return items


def build_preference_context(items: list[dict]) -> str:
    """Return gated topic evidence for the picker, or an empty string."""
    normalized = []
    for item in items:
        rating = _canonical_rating(item.get("rating") or item.get("label"))
        if rating is not None:
            normalized.append((item, rating, RATING_VALUE[rating]))
    if len(normalized) < MIN_TOTAL_LABELS:
        return ""
    if sum(rating != "COOL" for _item, rating, _value in normalized) < MIN_NON_COOL_LABELS:
        return ""

    global_mean = sum(value for _item, _rating, value in normalized) / len(normalized)
    by_topic: dict[str, list[float]] = {}
    for item, _rating, value in normalized:
        for topic in _topics(item):
            by_topic.setdefault(topic, []).append(value)

    evidence = []
    for topic, values in by_topic.items():
        if len(values) < MIN_TOPIC_LABELS:
            continue
        pooled = (PRIOR_STRENGTH * global_mean + sum(values)) / (
            PRIOR_STRENGTH + len(values)
        )
        delta = pooled - global_mean
        if abs(delta) < MIN_TOPIC_DELTA:
            continue
        direction = "prefer" if delta > 0 else "avoid"
        evidence.append((abs(delta), f"- {topic}: {direction} ({len(values)} labels)"))
    evidence.sort(reverse=True)
    if not evidence:
        return ""
    lines = "\n".join(line for _delta, line in evidence[:MAX_TOPICS_IN_CONTEXT])
    return (
        f"Preference evidence ({POLICY_VERSION}). Use this only as a small tie-breaker. "
        "The fixed CORE and NOVEL goals still control the pick.\n"
        f"{lines}"
    )


def load_preference_context(table, golden_path: str | Path) -> str:
    golden = load_golden_feedback(golden_path)
    live = load_live_feedback(table)
    return build_preference_context(merge_feedback(golden, live))
