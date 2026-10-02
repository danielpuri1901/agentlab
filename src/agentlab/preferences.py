"""Fixed topic taxonomy for video ledger items.

Each video item records `topics` so the ledger can be grouped by subject
without embeddings. The gated statistical preference context that used to
live here was replaced by the taste profile (profile.py, consolidate.py)
on 2026-10-02: it needed 30 ratings with 10 non-COOL before it did
anything, and Daniel never taps SKIP.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

FUZZY_ALIAS_THRESHOLD = 0.72

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
