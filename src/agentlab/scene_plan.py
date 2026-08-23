"""Deep read: one source becomes a full digest plus a validated scene plan.

Anti-collapse rules enforced here (docs/specs/2026-08-23-daily-paper-videos.md
stage 4): every number in the digest and scene plan must appear in the
fetched source text; the model must never attribute to the source anything
it does not itself state; the digest always carries a "Limits" section
(same grounding discipline as proposer.py). The scene plan is a strict,
pydantic-validated JSON object because it drives a deterministic Manim
render (Task 4) that never sees freeform model output.

Style follows proposer.py: prompt constants, a parse function tolerant of
fenced/dirty model output, no module-level litellm import. Unlike
proposer.py's private `_complete`, this module takes `complete` as an
injected callable (model, messages) -> str, since both `deep_read` and
`pick_paper` are called by the worker (Task 5) which owns model selection
and env overrides.
"""

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError

DEFAULT_DEEP_READ_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"
DEFAULT_PICK_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"

MAX_TITLE = 70
MAX_CLAIM = 200
MAX_LABEL = 40
MAX_DETAIL = 200
MAX_NARRATION = 280
MAX_KEY_VALUE = 20
MAX_KEY_MEANING = 90
MAX_LIMITS = 200
MAX_QUESTION = 200
MIN_MECHANISM_STEPS = 3
MAX_MECHANISM_STEPS = 6
MAX_KEY_NUMBERS = 3


class MechanismStep(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    detail: str = Field(min_length=1, max_length=MAX_DETAIL)
    narration: str = Field(min_length=1, max_length=MAX_NARRATION)


class KeyNumber(BaseModel):
    value: str = Field(min_length=1, max_length=MAX_KEY_VALUE)
    meaning: str = Field(min_length=1, max_length=MAX_KEY_MEANING)


class ScenePlan(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    one_line_claim: str = Field(min_length=1, max_length=MAX_CLAIM)
    mechanism_steps: list[MechanismStep] = Field(
        min_length=MIN_MECHANISM_STEPS, max_length=MAX_MECHANISM_STEPS
    )
    key_numbers: list[KeyNumber] = Field(default_factory=list, max_length=MAX_KEY_NUMBERS)
    limits_or_caveats: str = Field(min_length=1, max_length=MAX_LIMITS)
    street_test_question: str = Field(min_length=1, max_length=MAX_QUESTION)
    citation_url: str = Field(min_length=1)


DEEP_READ_SYSTEM = """You are the deep-read writer for AgentLab's daily \
paper videos. You are given the full text of one source. In a single reply \
you produce TWO things, in order: first a complete digest in markdown, then \
a fenced ```json block containing the scene plan.

Grounding rules, non-negotiable: every number you write, in the digest or \
the scene plan, must appear in the source text you were given. Never \
attribute to the source anything it does not itself state; if an idea goes \
beyond what the source says, say so explicitly instead of implying the \
source said it. The digest must include a section titled "Limits".

Digest sections, in this order: headline, what the paper shows (with its \
numbers), mechanism, how it connects to AgentLab, limits.

Write in plain language, short sentences, one idea per sentence \
(Simplified Technical English style). Never use an em dash anywhere in \
your output; use a plain dash or a period instead.

After the digest, output a fenced ```json block containing ONLY the scene \
plan object, nothing before or after it, with these keys: title, \
one_line_claim, mechanism_steps (3 to 6 objects with label, detail, \
narration), key_numbers (0 to 3 objects with value, meaning), \
limits_or_caveats, street_test_question, citation_url. The narration field \
of each mechanism step is read aloud as the video's voiceover: write it as \
short, spoken-style sentences a person would actually say out loud, plain \
language, one idea per sentence, never an em dash. Every key_numbers value \
must be a number that appears in the source text. limits_or_caveats is one \
sentence about what the paper does NOT claim."""


def build_deep_read_prompt(url: str, source_text: str) -> str:
    return (
        f"Source URL: {url}\n\n"
        f"Source text:\n{source_text}\n\n"
        "Write the full digest, then the fenced json scene plan, following "
        "every rule above exactly."
    )


PLAN_FENCE = "```json"


def split_digest_and_plan(raw: str) -> tuple[str, str]:
    """Split a deep-read completion on the LAST ```json fence.

    Everything before the fence is the digest; everything between the fence
    and its closing ``` (or end of string, if the model forgot to close it)
    is the scene-plan JSON text.
    """
    idx = raw.rfind(PLAN_FENCE)
    if idx == -1:
        return raw.strip(), ""
    digest = raw[:idx].strip()
    rest = raw[idx + len(PLAN_FENCE) :]
    close_idx = rest.find("```")
    plan_raw = rest[:close_idx] if close_idx != -1 else rest
    return digest, plan_raw.strip()


def _extract_json_object(raw: str) -> str:
    """Pull a JSON object out of possibly fenced, possibly dirty text."""
    text = raw.strip()
    if "```" in text:
        parts = text.split("```")
        # parts alternate: outside, fenced, outside, fenced, ... Take the
        # last non-empty fenced block, tolerant of stray fences.
        fenced = [parts[i] for i in range(1, len(parts), 2) if parts[i].strip()]
        if fenced:
            text = fenced[-1]
            text = text.removeprefix("json").strip()
    if not text.startswith("{"):
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            text = text[start : end + 1]
    return text


def _parse_scene_plan_verbose(raw: str) -> tuple[ScenePlan | None, str]:
    text = _extract_json_object(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "scene plan JSON must be an object"
    try:
        return ScenePlan(**data), ""
    except ValidationError as exc:
        return None, str(exc)


def parse_scene_plan(raw: str) -> ScenePlan | None:
    """Parse a scene plan out of raw model text, tolerant of fences and junk.

    Returns None on any parse or validation failure rather than raising, so
    callers can treat "garbage" and "invalid schema" the same way.
    """
    plan, _ = _parse_scene_plan_verbose(raw)
    return plan


def deep_read(
    url: str,
    fetch_text: Callable[[str], str],
    complete: Callable[[str, list[dict]], str],
    model: str = DEFAULT_DEEP_READ_MODEL,
) -> tuple[str, ScenePlan]:
    """Fetch a source and turn it into (digest markdown, validated ScenePlan).

    One completion is asked to produce both artifacts at once (the digest is
    the depth layer, the plan drives the video); if the trailing JSON fails
    schema validation, retry once with the pydantic error appended so the
    model can fix only the JSON.
    """
    source_text = fetch_text(url)
    messages = [
        {"role": "system", "content": DEEP_READ_SYSTEM},
        {"role": "user", "content": build_deep_read_prompt(url, source_text)},
    ]
    raw = complete(model, messages)
    digest, plan_raw = split_digest_and_plan(raw)
    plan, error = _parse_scene_plan_verbose(plan_raw)
    if plan is None:
        retry_messages = messages + [
            {
                "role": "user",
                "content": (
                    "Your last reply's scene plan JSON was invalid. "
                    f"Validation error: {error}\n"
                    "Send the full digest again, then a corrected "
                    "```json fenced scene plan at the very end that fixes "
                    "only the JSON so it matches the schema exactly."
                ),
            }
        ]
        raw = complete(model, retry_messages)
        digest, plan_raw = split_digest_and_plan(raw)
        plan, error = _parse_scene_plan_verbose(plan_raw)
    if plan is None:
        raise ValueError(f"scene plan invalid after retry for {url}: {error}")
    return digest, plan


PICK_SYSTEM = """You are picking ONE candidate from a numbered list for \
Daniel to watch a video about today. Answer with ONLY the number of your \
pick, nothing else: no words, no punctuation, just the number."""

_NOVEL_INSTRUCTION = (
    "Pick the candidate Daniel is LEAST likely to already know about, the "
    "one that sounds the coolest and most frontier-opening. Ignore "
    "Daniel's interests below except to avoid picking something that just "
    "overlaps what he already tracks; the goal here is surprise, not "
    "relevance."
)
_CORE_INSTRUCTION = "Pick the single candidate MOST relevant to Daniel's interests below."


def _candidate_signal(candidate: dict) -> str:
    if "points" in candidate:
        return f"{candidate['points']} points"
    if "upvotes" in candidate:
        return f"{candidate['upvotes']} upvotes"
    if candidate.get("summary"):
        return candidate["summary"][:80]
    if candidate.get("notes"):
        return candidate["notes"][:80]
    return "no signal"


def build_pick_prompt(candidates: list[dict], interests_text: str, mode: str) -> str:
    lines = "\n".join(
        f"{i}. [{c.get('pool', '?')}/{c.get('source', '?')}] "
        f"{c.get('title', '')} ({_candidate_signal(c)})"
        for i, c in enumerate(candidates, 1)
    )
    instruction = _NOVEL_INSTRUCTION if mode == "novel" else _CORE_INSTRUCTION
    return (
        f"Candidates:\n{lines}\n\n"
        f"Daniel's interests:\n{interests_text}\n\n"
        f"{instruction}\n\n"
        "Answer with ONLY the number, nothing else."
    )


def _parse_pick_index(raw: str, count: int) -> int | None:
    match = re.search(r"-?\d+", raw or "")
    if not match:
        return None
    index = int(match.group())
    if 1 <= index <= count:
        return index
    return None


def pick_paper(
    candidates: list[dict],
    interests_text: str,
    complete: Callable[[str, list[dict]], str],
    mode: str = "core",
    model: str = DEFAULT_PICK_MODEL,
) -> dict | None:
    """Rank candidates with one model call, return the chosen dict or None.

    mode="core" asks for the candidate most relevant to interests_text.
    mode="novel" asks for the candidate Daniel is least likely to already
    know that sounds the coolest, explicitly told to ignore interests_text
    except to dodge overlap (the explore-day rule; which pool to pass in is
    the caller's decision, not this function's).
    """
    if not candidates:
        return None
    raw = complete(
        model,
        [
            {"role": "system", "content": PICK_SYSTEM},
            {"role": "user", "content": build_pick_prompt(candidates, interests_text, mode)},
        ],
    )
    index = _parse_pick_index(raw, len(candidates))
    if index is None:
        return None
    return candidates[index - 1]
