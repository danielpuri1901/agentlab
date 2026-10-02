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
from agentlab.papers_db import (
    is_seen,
    normalize_title,
    paper_identity,
    recent_seen_titles,
)
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
        # run_propose files a classic with its title clipped to MAX_TITLE.
        if normalize_title(entry["title"][:MAX_TITLE]) in proposed:
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
