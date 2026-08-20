"""The proposer: read fresh sources, draft cited proposals, ping Daniel.

Anti-collapse rules enforced here (docs/phase0-synthesis.md): every proposal
anchors to a fresh external source by URL; every proposal states its
distance from the recent archive (which includes rejected proposals, so
rejected ideas do not come back reworded); at most DAILY_CAP proposals per
day. Auto-submittable work is ONLY a registered compaction re-run whose
fields pass the same validation `agentlab cloud submit` applies, with hard
caps (tasks<=20, repeats<=5, model must be a bedrock/ id) bounding spend.
"""

import json
import os

from agentlab.cloud import (
    _MODEL_PATTERN,
    _valid_styles,
    build_message_body,
    generate_experiment_id,
)
from agentlab.notify import notify, queue_ping
from agentlab.proposals import (
    DAILY_CAP,
    count_created_today,
    file_proposal,
    generate_proposal_id,
    list_recent,
)
from agentlab.sources import gather

DEFAULT_PROPOSER_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"
MAX_TITLE = 80
MAX_HEADLINE = 300
MAX_CITATION = 300
MAX_DISTANCE = 300

PROPOSER_SYSTEM = """You are the proposer for AgentLab, an experimentation \
lab that measures agent techniques with paired statistics. You read fresh \
sources and propose experiments. Rules: every proposal cites exactly one \
source URL from the list you are given. Every proposal states its distance \
from the archive in one sentence. Do not repropose anything the archive \
already rejected or completed. Kind "registered_rerun" means a compaction \
suite run and must include an experiment object with model, tasks, repeats, \
baseline_style, candidate_style. Any new idea is kind "new_hypothesis" and \
carries no experiment object. Write titles and headlines in short plain \
sentences. Output ONLY a JSON array of proposal objects with keys: title, \
headline, citation, distance, kind, and optionally experiment."""


def _complete(model: str, messages: list[dict]) -> str:
    """The only litellm touchpoint; tests monkeypatch this."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    response = litellm.completion(model=model, messages=messages, max_tokens=3000)
    return response.choices[0].message.content


def build_prompt(sources: list[dict], archive: list[dict], slots: int) -> str:
    source_lines = "\n".join(
        f"- [{s['source']}] {s['title']} :: {s['url']}" for s in sources
    )
    archive_lines = "\n".join(
        f"- [{p.get('status')}] {p.get('title')} :: {p.get('distance', '')}"
        for p in archive
    ) or "- (archive is empty)"
    return (
        f"Fresh sources today:\n{source_lines}\n\n"
        f"Recent archive (do not repeat these):\n{archive_lines}\n\n"
        f"Propose at most {slots} experiments as a JSON array."
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
        required = ("title", "headline", "citation", "distance", "kind")
        if not all(isinstance(entry.get(k), str) and entry[k] for k in required):
            continue
        entry["title"] = entry["title"][:MAX_TITLE]
        entry["headline"] = entry["headline"][:MAX_HEADLINE]
        entry["citation"] = entry["citation"][:MAX_CITATION]
        entry["distance"] = entry["distance"][:MAX_DISTANCE]
        if entry["kind"] not in ("registered_rerun", "new_hypothesis"):
            entry["kind"] = "new_hypothesis"
        proposals.append(entry)
    return proposals


def _registered_submit_body(exp: dict) -> dict | None:
    """Validate a proposed registered re-run; None means 'not auto-submittable'."""
    try:
        model = str(exp["model"])
        baseline = str(exp["baseline_style"])
        candidate = str(exp["candidate_style"])
        tasks = int(exp.get("tasks", 20))
        repeats = int(exp.get("repeats", 5))
        n_facts = int(exp.get("n_facts", 12))
        filler_turns = int(exp.get("filler_turns", 40))
        summary_budget = int(exp.get("summary_budget", 150))
    except (KeyError, TypeError, ValueError):
        return None
    if not model.startswith("bedrock/") or not _MODEL_PATTERN.fullmatch(model):
        return None
    valid = _valid_styles()
    if baseline not in valid or candidate not in valid or baseline == candidate:
        return None
    if not (1 <= tasks <= 20 and 1 <= repeats <= 5):
        return None
    # n_facts multiplies model calls linearly and filler_turns/summary_budget
    # inflate tokens, so they are spend knobs exactly like tasks/repeats;
    # filler_turns must also be >= n_facts or the corpus generator rejects
    # the run.
    if not (
        1 <= n_facts <= 25
        and n_facts <= filler_turns <= 200
        and 1 <= summary_budget <= 500
    ):
        return None
    return build_message_body(
        generate_experiment_id(),
        model,
        tasks,
        repeats,
        n_facts,
        filler_turns,
        summary_budget,
        30,
        baseline,
        candidate,
    )


def run_propose(table, ssm_client, s3_client, bucket: str, model: str) -> int:
    slots = DAILY_CAP - count_created_today(table)
    if slots <= 0:
        return 0
    sources = gather()
    if not sources:
        notify(
            table,
            ssm_client,
            "The proposer ran but found no fresh sources. No proposals today.",
        )
        return 0
    archive = list_recent(table)
    raw = _complete(
        model,
        [
            {"role": "system", "content": PROPOSER_SYSTEM},
            {"role": "user", "content": build_prompt(sources, archive, slots)},
        ],
    )
    proposals = parse_proposals(raw)[:slots]
    if not proposals:
        notify(
            table,
            ssm_client,
            "The proposer ran but produced no valid proposals this time.",
        )
        return 0
    lines, buttons = [], []
    for index, proposal in enumerate(proposals, 1):
        pid = generate_proposal_id()
        submit_body = None
        if proposal["kind"] == "registered_rerun":
            submit_body = _registered_submit_body(proposal.get("experiment") or {})
            if submit_body is None:
                proposal["kind"] = "new_hypothesis"
        file_proposal(
            table,
            pid,
            proposal["title"],
            proposal["headline"],
            proposal["citation"],
            proposal["distance"],
            proposal["kind"],
            submit_body=submit_body,
        )
        s3_client.put_object(
            Bucket=bucket,
            Key=f"proposals/{pid}/proposal.json",
            Body=json.dumps(
                {"proposal": proposal, "sources_seen": len(sources)}
            ).encode("utf-8"),
        )
        lines.append(
            f"{index}. {proposal['title']}\n"
            f"{proposal['headline']}\n"
            f"Source: {proposal['citation']}\n"
            f"Distance: {proposal['distance']}\n"
            f"Kind: {proposal['kind']}"
        )
        buttons.append(
            [
                (f"APPROVE {index}", f"prop:{pid}:approve"),
                (f"REJECT {index}", f"prop:{pid}:reject"),
            ]
        )
    text = "New proposals. Tap to decide.\n\n" + "\n\n".join(lines)
    try:
        notify(table, ssm_client, text, buttons=buttons)
    except Exception:  # noqa: BLE001 - filed proposals must never be silently stranded
        queue_ping(table, text, buttons, None)
    return len(proposals)
