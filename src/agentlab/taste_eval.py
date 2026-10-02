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
    if old.f1 - new.f1 > F1_TOLERANCE + 1e-9:  # float-safe: an exact 0.02 drop still passes
        return False, f"F1 fell {old.f1:.2f} -> {new.f1:.2f}, more than {F1_TOLERANCE:.2f}"
    return True, (
        f"F1 {old.f1:.2f} -> {new.f1:.2f}, "
        f"golden recall {old.golden_recall:.2f} -> {new.golden_recall:.2f}"
    )
