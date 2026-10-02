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
    return response.choices[0].message.content or ""


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
