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
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

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

# Diagram: the paper's OWN mechanism as data (Daniel's ruling 2026-08-25,
# after the first three live videos all rendered the same abstract stage
# pipeline -- "if it's about a harness, there should be a graphical image
# of how the harness works; for the moon one, actually the moon"). The
# model writes nodes/edges/icons and which ones each mechanism step
# activates; the template (video_scenes.py) owns every pixel of layout and
# animation. See DEEP_READ_SYSTEM's diagram design rules below.
MAX_NODE_ID = 40
MAX_NODE_LABEL = 24
MAX_NODE_ICON = 4
MAX_EDGE_LABEL = 20
MIN_DIAGRAM_NODES = 2
MAX_DIAGRAM_NODES = 8
MIN_DIAGRAM_EDGES = 1
MAX_DIAGRAM_EDGES = 10
MAX_STEP_ACTIVATES = MAX_DIAGRAM_NODES + MAX_DIAGRAM_EDGES
_NODE_ID_PATTERN = r"^[a-z0-9]+(-[a-z0-9]+)*$"


class DiagramNode(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_NODE_ID, pattern=_NODE_ID_PATTERN)
    label: str = Field(min_length=1, max_length=MAX_NODE_LABEL)
    icon: str | None = Field(default=None, max_length=MAX_NODE_ICON)


class DiagramEdge(BaseModel):
    source: str
    target: str
    label: str | None = Field(default=None, max_length=MAX_EDGE_LABEL)


class Diagram(BaseModel):
    nodes: list[DiagramNode] = Field(min_length=MIN_DIAGRAM_NODES, max_length=MAX_DIAGRAM_NODES)
    edges: list[DiagramEdge] = Field(min_length=MIN_DIAGRAM_EDGES, max_length=MAX_DIAGRAM_EDGES)

    @model_validator(mode="after")
    def _edges_reference_known_nodes(self) -> "Diagram":
        node_ids = [n.id for n in self.nodes]
        if len(set(node_ids)) != len(node_ids):
            raise ValueError("diagram node ids must be unique")
        node_id_set = set(node_ids)
        for edge in self.edges:
            if edge.source not in node_id_set or edge.target not in node_id_set:
                raise ValueError(
                    f"diagram edge references an unknown node id: "
                    f"{edge.source}->{edge.target}"
                )
        return self


class MechanismStep(BaseModel):
    label: str = Field(min_length=1, max_length=MAX_LABEL)
    detail: str = Field(min_length=1, max_length=MAX_DETAIL)
    narration: str = Field(min_length=1, max_length=MAX_NARRATION)
    # The visual motif the template animates for this step. The model picks
    # the fitting one; it never writes animation code (Daniel's feedback
    # 2026-08-23: show the mechanism operating, not text about it).
    kind: Literal["transform", "gate", "loop", "split", "store", "compare"] = "transform"
    # Which of the plan's OWN diagram nodes/edges this step is about (edge
    # ids are written "source->target"); validated against the plan's
    # diagram by ScenePlan._activates_reference_known_ids below, since a
    # single step can't validate this against sibling data on its own.
    activates: list[str] = Field(default_factory=list, max_length=MAX_STEP_ACTIVATES)


class KeyNumber(BaseModel):
    value: str = Field(min_length=1, max_length=MAX_KEY_VALUE)
    meaning: str = Field(min_length=1, max_length=MAX_KEY_MEANING)


class ScenePlan(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    one_line_claim: str = Field(min_length=1, max_length=MAX_CLAIM)
    diagram: Diagram
    mechanism_steps: list[MechanismStep] = Field(
        min_length=MIN_MECHANISM_STEPS, max_length=MAX_MECHANISM_STEPS
    )
    key_numbers: list[KeyNumber] = Field(default_factory=list, max_length=MAX_KEY_NUMBERS)
    limits_or_caveats: str = Field(min_length=1, max_length=MAX_LIMITS)
    street_test_question: str = Field(min_length=1, max_length=MAX_QUESTION)
    citation_url: str = Field(min_length=1)

    @model_validator(mode="after")
    def _activates_reference_known_ids(self) -> "ScenePlan":
        # Unknown-id violations stay fatal (never clipped by _clip_lengths
        # below): a step pointing at a diagram element that doesn't exist
        # is structurally broken, the same bucket as a bad step count.
        node_ids = {n.id for n in self.diagram.nodes}
        edge_ids = {f"{e.source}->{e.target}" for e in self.diagram.edges}
        known = node_ids | edge_ids
        for step in self.mechanism_steps:
            unknown = [a for a in step.activates if a not in known]
            if unknown:
                raise ValueError(
                    f"mechanism step {step.label!r} activates unknown diagram "
                    f"id(s): {unknown}"
                )
        return self


_DEEP_READ_MAIN = """You are the deep-read writer for AgentLab's daily \
paper videos. You are given the full text of one source inside a \
<source_text> tag. In a single reply you produce TWO things, in order: \
first a complete digest in markdown, then a fenced ```json block \
containing the scene plan.

Grounding rules, non-negotiable: every number you write, in the digest or \
the scene plan, must appear in the source text you were given. Never \
attribute to the source anything it does not itself state; if an idea goes \
beyond what the source says, say so explicitly instead of implying the \
source said it. The digest must include a section titled "Limits".

Digest sections, in this order: headline, what the paper shows (with its \
numbers), mechanism, how it connects to AgentLab, limits. The mechanism \
section must open with a line starting "Components:" listing the \
mechanism's real parts, the actual nouns that make it work (not stage \
names like "step one"). Those exact components are what you turn into \
diagram nodes below, so name them here first and reuse the same names.

Write in plain language, short sentences, one idea per sentence \
(Simplified Technical English style). Never use an em dash anywhere in \
your output; use a plain dash or a period instead.

After the digest, output a fenced ```json block containing ONLY the scene \
plan object, nothing before or after it, with these keys: title, \
one_line_claim, diagram (see below), mechanism_steps (3 to 6 (three to \
six) objects with label, detail, narration, kind, activates), key_numbers \
(0 to 3 (zero to three) objects with value, meaning), limits_or_caveats, \
street_test_question, citation_url. The narration field of each mechanism \
step is read aloud as the video's voiceover: write it as short, \
spoken-style sentences a person would actually say out loud, plain \
language, one idea per sentence, never an em dash. Every key_numbers value \
must be a number that appears in the source text. limits_or_caveats is one \
sentence about what the paper does NOT claim.

Each mechanism step also has a kind field, the visual motif the video \
animates for that step. Pick the one that fits what actually happens: \
transform (input becomes output), gate (something is accepted or rejected), \
loop (a cycle repeats), split (one path becomes several), store (something \
is saved for later), compare (two things are measured against each other). \
The motif plays out ON the diagram elements you name in that step's \
activates field, so keep kind and activates pointing at the same part of \
the mechanism.

Diagram design rules, the most important part of this prompt: draw THIS \
paper's own mechanism, never a generic pipeline. Every video before this \
rule looked the same because the template only ever drew abstract stage \
boxes; you fix that by giving it real content. Use the paper's own \
components as nodes, the exact nouns from the Components: line above, not \
abstract stage labels. A harness paper needs nodes like user, llm, tools, \
memory. A paper about a lunar mission needs nodes like moon, lander, \
sensor. A chain-of-thought paper needs nodes like prompt, \
reasoning-step-1, reasoning-step-2, answer. Node ids are lowercase slugs: \
letters, digits, and single hyphens only, no spaces (e.g. "reasoning-step-1"). \
Give each node a natural emoji icon where one obviously fits the concept \
(a moon node gets a moon emoji, a tool node gets a wrench); leave icon \
empty when nothing natural fits rather than forcing one. Diagram needs 2 \
to 8 (two to eight) nodes and 1 to 10 (one to ten) edges; each edge is an \
arrow from one node id to another, with an optional short label naming \
what moves along it (e.g. "reads", "writes", "checks").

Each mechanism step's activates field is a list of the diagram's own node \
ids and edge ids (edge ids are written source->target, exactly matching \
one of your edges) that this exact step is about. Use only ids that exist \
in your diagram; every id you invent here must already be a node or edge \
above. A step usually activates one to three ids: the node or nodes doing \
the work, plus the edge between them if something moves from one node to \
another."""

_EXAMPLE_DIGEST = (
    "# Headline\n"
    "SortNet sorts by comparing at inference time, not by memorizing an order.\n\n"
    "## What the paper shows\n"
    "100% correct across 200 held-out lists, averaging 12 comparisons each.\n\n"
    "## Mechanism\n"
    "Components: input list, comparator, sorted output.\n"
    "The comparator reads two items from the input list, decides their "
    "order, and writes the result into the sorted output.\n\n"
    "## How it connects to AgentLab\n"
    "Similar to a judge model comparing two candidate outputs.\n\n"
    "## Limits\n"
    "The paper does not test lists longer than 50 items."
)

_EXAMPLE_PLAN = {
    "title": "SortNet sorts by comparing",
    "one_line_claim": (
        "A tiny comparator sorts lists at inference time instead of "
        "memorizing a fixed order."
    ),
    "diagram": {
        "nodes": [
            {"id": "input-list", "label": "Input list", "icon": "\U0001f4cb"},
            {"id": "comparator", "label": "Comparator", "icon": "⚖️"},
            {"id": "sorted-output", "label": "Sorted output", "icon": "✅"},
        ],
        "edges": [
            {"source": "input-list", "target": "comparator", "label": "reads"},
            {"source": "comparator", "target": "sorted-output", "label": "writes"},
        ],
    },
    "mechanism_steps": [
        {
            "label": "Read",
            "detail": "Reads two items from the input list.",
            "narration": "SortNet reads two items from the list.",
            "kind": "transform",
            "activates": ["input-list", "input-list->comparator", "comparator"],
        },
        {
            "label": "Compare",
            "detail": "Decides which item comes first.",
            "narration": "It decides which one comes first.",
            "kind": "compare",
            "activates": ["comparator"],
        },
        {
            "label": "Write",
            "detail": "Writes the result into the sorted output.",
            "narration": "The result lands in the sorted output.",
            "kind": "store",
            "activates": ["comparator->sorted-output", "sorted-output"],
        },
    ],
    "key_numbers": [
        {"value": "12", "meaning": "average comparisons per 200-item held-out list."}
    ],
    "limits_or_caveats": "The paper does not test lists longer than 50 items.",
    "street_test_question": "Would a pairwise comparator beat your current sort step.",
    "citation_url": "https://example.com/sortnet",
}

_EXAMPLE_BLOCK = (
    "\n\nWorked example (a FICTIONAL paper, invented only to show the exact "
    "shape of a correct reply; never treat its content as real, never reuse "
    "it):\n\n"
    'Fictional source: "SortNet sorts a list purely by pairwise comparisons '
    'made at inference time. On 200 held-out lists it sorts correctly 100% '
    'of the time using 12 comparisons on average."\n\n'
    f"{_EXAMPLE_DIGEST}\n\n"
    "```json\n" + json.dumps(_EXAMPLE_PLAN) + "\n```"
)

DEEP_READ_SYSTEM = _DEEP_READ_MAIN + _EXAMPLE_BLOCK


def build_deep_read_prompt(url: str, source_text: str) -> str:
    # The source text is tagged and the hard rules restated right after it
    # closes (production long-context practice: instructions closest to
    # the generation point win) rather than only living at the top of the
    # system prompt.
    return (
        f"Source URL: {url}\n\n"
        f"<source_text>\n{source_text}\n</source_text>\n\n"
        "Reminder of the three hard rules, now that you have read the "
        "source above: every number you write comes only from the source "
        "text, never attribute to the source anything it does not itself "
        "state, and your reply is the full digest followed by exactly one "
        "fenced json scene plan.\n\n"
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


def extract_json_object(raw: str) -> str:
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


_extract_json_object = extract_json_object


_CLIP_LIMITS = {
    "title": MAX_TITLE,
    "one_line_claim": MAX_CLAIM,
    "limits_or_caveats": MAX_LIMITS,
    "street_test_question": MAX_QUESTION,
}
_STEP_CLIP_LIMITS = {"label": MAX_LABEL, "detail": MAX_DETAIL, "narration": MAX_NARRATION}
_DIAGRAM_NODE_CLIP_LIMITS = {"label": MAX_NODE_LABEL, "icon": MAX_NODE_ICON}
_DIAGRAM_EDGE_CLIP_LIMITS = {"label": MAX_EDGE_LABEL}


def _clip_lengths(data: dict) -> dict:
    """Deterministically clip over-long strings instead of failing the track.

    Length violations are salvageable (a truncated claim still makes a video;
    a dead track makes nothing - live failure 2026-08-25, core track). Only
    structural violations (step/number counts, missing keys, an `activates`
    id that names no real diagram node or edge) stay fatal.
    """
    for key, limit in _CLIP_LIMITS.items():
        if isinstance(data.get(key), str):
            data[key] = data[key][:limit]
    steps = data.get("mechanism_steps")
    if isinstance(steps, list):
        for step in steps:
            if isinstance(step, dict):
                for key, limit in _STEP_CLIP_LIMITS.items():
                    if isinstance(step.get(key), str):
                        step[key] = step[key][:limit]
    numbers = data.get("key_numbers")
    if isinstance(numbers, list):
        for num in numbers:
            if isinstance(num, dict):
                if isinstance(num.get("value"), str):
                    num["value"] = num["value"][:MAX_KEY_VALUE]
                if isinstance(num.get("meaning"), str):
                    num["meaning"] = num["meaning"][:MAX_KEY_MEANING]
    diagram = data.get("diagram")
    if isinstance(diagram, dict):
        nodes = diagram.get("nodes")
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    for key, limit in _DIAGRAM_NODE_CLIP_LIMITS.items():
                        if isinstance(node.get(key), str):
                            node[key] = node[key][:limit]
        edges = diagram.get("edges")
        if isinstance(edges, list):
            for edge in edges:
                if isinstance(edge, dict):
                    for key, limit in _DIAGRAM_EDGE_CLIP_LIMITS.items():
                        if isinstance(edge.get(key), str):
                            edge[key] = edge[key][:limit]
    return data


def _parse_scene_plan_verbose(raw: str) -> tuple[ScenePlan | None, str]:
    text = _extract_json_object(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "scene plan JSON must be an object"
    try:
        return ScenePlan(**_clip_lengths(data)), ""
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
    # The model never writes the citation: the fetch URL is ground truth
    # (a hallucinated citation burned this lab once, see proposer.py).
    plan.citation_url = url
    return digest, plan


PICK_SYSTEM = """You are picking ONE candidate from a numbered list for \
Daniel to watch a video about today. Answer with ONLY the number of your \
pick, nothing else: no words, no punctuation, just the number."""

RANK_SYSTEM = """Rank the best candidates from a numbered list for Daniel to watch.
Answer with only a JSON array of candidate numbers in best-to-worst order."""

_NOVEL_INSTRUCTION = (
    "Pick the candidate Daniel is LEAST likely to already know about, the "
    "one that sounds the coolest and most frontier-opening. Ignore "
    "Daniel's interests below except to avoid picking something that just "
    "overlaps what he already tracks; the goal here is surprise, not "
    "relevance."
)
_PAPER_PREFERENCE = (
    " Prefer candidates from arxiv or hf over hn ones; pick an hn candidate"
    " only if its title clearly refers to an actual paper."
)
_CORE_INSTRUCTION = (
    "Pick the single candidate MOST relevant to Daniel's interests below."
    + _PAPER_PREFERENCE
)


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
    instruction = (_NOVEL_INSTRUCTION + _PAPER_PREFERENCE) if mode == "novel" else _CORE_INSTRUCTION
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


def rank_papers(
    candidates: list[dict],
    interests_text: str,
    complete: Callable[[str, list[dict]], str],
    mode: str = "core",
    model: str = DEFAULT_PICK_MODEL,
    limit: int = 3,
) -> list[dict]:
    """Return a small baseline ranking before preference feedback is applied."""
    count = min(max(limit, 1), len(candidates))
    if not candidates:
        return []
    prompt = build_pick_prompt(candidates, interests_text, mode).rsplit(
        "Answer with ONLY the number, nothing else.", 1
    )[0]
    prompt += (
        f"Rank the best {count} candidates. Return exactly {count} unique numbers "
        "as a JSON array, best first."
    )
    raw = complete(
        model,
        [
            {"role": "system", "content": RANK_SYSTEM},
            {"role": "user", "content": prompt},
        ],
    )
    match = re.search(r"\[[^\]]*\]", raw or "")
    if not match:
        return []
    try:
        indices = json.loads(match.group())
    except json.JSONDecodeError:
        return []
    if (
        not isinstance(indices, list)
        or len(indices) != count
        or any(type(index) is not int for index in indices)
        or len(set(indices)) != count
        or any(index < 1 or index > len(candidates) for index in indices)
    ):
        return []
    return [candidates[index - 1] for index in indices]
