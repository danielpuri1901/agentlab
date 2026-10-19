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
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from agentlab.source_text import prepare_source

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
_SOURCE_NUMBER_RE = re.compile(
    r"(?<![A-Za-z])\d+(?:[.,]\d+)*(?:\s*(?:%|percent))?", re.IGNORECASE
)
_VERSION_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9])[vV](\d+(?:\.\d+)+)")
_DECIMAL_NUMBER_RE = re.compile(r"(\d+\.\d+)(%?)")


class DiagramNode(BaseModel):
    id: str = Field(min_length=1, max_length=MAX_NODE_ID, pattern=_NODE_ID_PATTERN)
    label: str = Field(min_length=1, max_length=MAX_NODE_LABEL)
    icon: str | None = Field(default=None, max_length=MAX_NODE_ICON)


class DiagramEdge(BaseModel):
    source: str
    target: str
    label: str | None = Field(default=None, max_length=MAX_EDGE_LABEL)


class Diagram(BaseModel):
    nodes: list[DiagramNode] = Field(
        min_length=MIN_DIAGRAM_NODES, max_length=MAX_DIAGRAM_NODES
    )
    edges: list[DiagramEdge] = Field(
        min_length=MIN_DIAGRAM_EDGES, max_length=MAX_DIAGRAM_EDGES
    )

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
    kind: Literal["transform", "gate", "loop", "split", "store", "compare"] = (
        "transform"
    )
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
    key_numbers: list[KeyNumber] = Field(
        default_factory=list, max_length=MAX_KEY_NUMBERS
    )
    application_or_implication: str = Field(min_length=1, max_length=MAX_CLAIM)
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


# Rule sources: the grounding check (_ungrounded_source_numbers), the storyboard
# code that reads the plan (storyboard.py), the subtitle and Polly path that
# speak narration exactly as written (video_render.narrate), and Daniel's
# rulings of 2026-08-25 (the paper's own parts as diagram nodes) and
# 2026-10-19 (no labels, symbols, or shorthand in the spoken script).
_DEEP_READ_MAIN = """You are the deep-read writer for AgentLab's daily paper videos. \
Daniel has no time to read papers, so AgentLab turns one paper a day into a short \
video he watches on his phone. The full text of the source is inside the \
<source_text> tag. Your reply has two parts, in this order: a complete digest in \
markdown, which Daniel reads after the video, then one fenced ```json block with the \
scene plan, which the storyboard writer and the render code use.

Grounding: every number you write, in the digest or the plan, appears in the source \
text. A check rejects the plan when a number is missing from the source, and one \
misread claim once cost a day's video. Credit the source only with what it states \
itself; when an idea goes beyond the source, say so in the sentence.

Digest sections, in this order: headline, what the paper shows (with its numbers), \
mechanism, how it connects to AgentLab, limits. The mechanism section opens with a \
line that starts with "Components:" and lists the real parts that make the mechanism \
work, as nouns such as "frozen weights W0", never stage names such as "step one". \
The diagram in the plan uses these same names, so name the parts here first.

Write in plain language: short sentences, one idea per sentence, active voice, common \
words, and a technical term defined the first time it appears. Use a plain dash, \
never an em dash; Daniel reads the digest on his phone and the same text style goes \
into the subtitles.

After the digest, write one fenced ```json block that holds only the scene plan \
object. Its keys:
- title: the exact title of the paper. For a topic, the topic's name in two to six \
words, as a heading, not a claim. The video opens by saying this title.
- one_line_claim: the main finding in one sentence.
- diagram: the mechanism as data. nodes is a list of 2 to 8 parts, each with id (a \
lowercase slug: letters, digits, and single hyphens) and label (the name from the \
Components line). edges is a list of 1 to 10 arrows between node ids, each with \
source, target, and an optional short label for what moves along it, such as "reads" \
or "adds". Use the paper's own parts as nodes: a harness paper has nodes like user, \
llm, tools, and memory; a lunar mission has moon, lander, and sensor. A generic \
pipeline of abstract stages is wrong.
- mechanism_steps: 3 to 6 objects with label, detail, and narration, in cause and \
effect order. The narration is the spoken voiceover for that step. A text-to-speech \
voice reads it exactly as written, so write one or two short sentences the way a \
teacher says them out loud: numbers and units as words ("two billion swipes a day", \
"about ten thousand times fewer"), names in full, and no symbols, code identifiers, \
abbreviations, labels, or prefixes.
- key_numbers: 0 to 3 objects with value and meaning. Each value is a number from the \
source, written as the source writes it.
- application_or_implication: one spoken sentence on where the finding could be used \
or what it changes. When this is your inference and not the source's claim, say so \
inside the sentence, for example "One possible use is ..." or "This suggests ...". The \
video reads this sentence aloud, so it carries no label or prefix.
- limits_or_caveats: one sentence on what the paper does not claim.
- street_test_question: one question Daniel should be able to answer in his own words \
after the video.
- citation_url: the source URL."""

# The worked example is the real LoRA paper (arXiv 2106.09685), the same paper
# as the scene coder's example. Daniel ruled on 2026-10-06 that examples must
# be real papers. The numbers come from the abstract; the source below is an
# abridged summary, and the reply uses nothing that is not in it.
_EXAMPLE_SOURCE = (
    "LoRA freezes a pretrained weight matrix W0 and learns its update as the "
    "product of two small matrices, B times A, of low rank r. A maps the input "
    "x down to r numbers, and B maps them back up. The output adds both paths: "
    "h = W0 x + B A x. Only A and B are trained. After training, B A can be "
    "added into W0, so inference has no additional latency. Compared to GPT-3 "
    "175B fine-tuned with Adam, LoRA reduces the number of trainable "
    "parameters by 10,000 times and the GPU memory requirement by 3 times. It "
    "performs on par with or better than fine-tuning on RoBERTa, DeBERTa, "
    "GPT-2, and GPT-3."
)

_EXAMPLE_DIGEST = (
    "# Headline\n"
    "LoRA adapts a huge model by training two small matrices beside each "
    "frozen weight matrix.\n\n"
    "## What the paper shows\n"
    "Compared to GPT-3 175B fine-tuned with Adam, LoRA trains 10,000 times "
    "fewer parameters and needs 3 times less GPU memory. Its quality matches "
    "or beats fine-tuning on RoBERTa, DeBERTa, GPT-2, and GPT-3.\n\n"
    "## Mechanism\n"
    "Components: input x, frozen weights W0, small matrix A, small matrix B, "
    "output h.\n"
    "The input x takes two paths. One path goes through the frozen weights W0. "
    "The other goes through A, which maps x down to r numbers, and then B, "
    "which maps them back up. The two results add up: h = W0 x + B A x. Only A "
    "and B learn. After training, B A is added into W0, so the model runs as "
    "fast as before.\n\n"
    "## How it connects to AgentLab\n"
    "One possible use is to keep one base model and train one small A and B "
    "pair per task.\n\n"
    "## Limits\n"
    "The paper reports quality only for RoBERTa, DeBERTa, GPT-2, and GPT-3."
)

_EXAMPLE_PLAN = {
    "title": "LoRA: Low-Rank Adaptation of Large Language Models",
    "one_line_claim": (
        "LoRA freezes the pretrained weights and trains a small low-rank "
        "update beside them, with no added inference latency."
    ),
    "diagram": {
        "nodes": [
            {"id": "input-x", "label": "Input x"},
            {"id": "frozen-weights", "label": "Frozen weights W0"},
            {"id": "matrix-a", "label": "Small matrix A"},
            {"id": "matrix-b", "label": "Small matrix B"},
            {"id": "output-h", "label": "Output h"},
        ],
        "edges": [
            {"source": "input-x", "target": "frozen-weights", "label": "multiplies"},
            {"source": "input-x", "target": "matrix-a", "label": "maps down"},
            {"source": "matrix-a", "target": "matrix-b", "label": "maps up"},
            {"source": "frozen-weights", "target": "output-h", "label": "adds"},
            {"source": "matrix-b", "target": "output-h", "label": "adds"},
        ],
    },
    "mechanism_steps": [
        {
            "label": "Freeze",
            "detail": "The pretrained weights W0 stay fixed. They get no updates.",
            "narration": (
                "Start with a huge pretrained model. Freeze its weights. "
                "They will not change."
            ),
        },
        {
            "label": "Two paths",
            "detail": "The input x goes through W0 and, beside it, through A and B.",
            "narration": (
                "Each input now takes two paths. One goes through the frozen "
                "weights. One goes through two small new matrices."
            ),
        },
        {
            "label": "Low-rank update",
            "detail": "A maps x down to r numbers. B maps them back up. Only A and B learn.",
            "narration": (
                "The first small matrix squeezes the input down to a few "
                "numbers. The second expands them back. Only these two learn."
            ),
        },
        {
            "label": "Add",
            "detail": "The two results add up: h = W0 x + B A x.",
            "narration": "The two paths meet again. Their results simply add up.",
        },
        {
            "label": "Merge",
            "detail": "After training, B A is added into W0. Inference gets no extra latency.",
            "narration": (
                "After training, fold the small product into the frozen "
                "matrix. The model runs exactly as fast as before."
            ),
        },
    ],
    "key_numbers": [
        {
            "value": "10,000",
            "meaning": "times fewer trainable parameters than GPT-3 175B fine-tuned with Adam.",
        },
        {
            "value": "3",
            "meaning": "times less GPU memory than GPT-3 175B fine-tuned with Adam.",
        },
    ],
    "application_or_implication": (
        "One possible use is to keep one base model and swap in a small A and "
        "B pair per task."
    ),
    "limits_or_caveats": (
        "The paper does not claim the same quality for models beyond RoBERTa, "
        "DeBERTa, GPT-2, and GPT-3."
    ),
    "street_test_question": (
        "Why can two small matrices stand in for an update of a huge weight matrix?"
    ),
    "citation_url": "https://arxiv.org/abs/2106.09685",
}

_EXAMPLE_BLOCK = (
    "\n\n<example>\nA worked example on the real LoRA paper (arXiv 2106.09685) with "
    "its source abridged. It shows the shape of a correct reply. Copy the shape, "
    "never the content.\n\n"
    f'Source (abridged): "{_EXAMPLE_SOURCE}"\n\n'
    f"{_EXAMPLE_DIGEST}\n\n"
    "```json\n" + json.dumps(_EXAMPLE_PLAN) + "\n```\n</example>"
)

DEEP_READ_SYSTEM = _DEEP_READ_MAIN + _EXAMPLE_BLOCK

# The scene-plan half of the paper prompt, shared with the lesson and study prompts.
_PLAN_RULES = _DEEP_READ_MAIN[_DEEP_READ_MAIN.index("Write in plain language") :]

# Lesson videos for the built track
# (docs/superpowers/specs/2026-10-05-built-lane-design.md, section 4).
_LESSON_READ_INTRO = """You are the deep-read writer for AgentLab's lesson videos. \
Daniel built the project described inside the <source_text> tag with AI help, and his \
understanding lags behind what he built. Each video teaches him one idea from the \
project, so his understanding catches up. The source text holds one topic from his \
study map (the topic, what to study, and where it appears), files from his project, \
and the whole study map. Your reply has two parts, in this order: a complete digest in \
markdown, which Daniel reads after the video, then one fenced ```json block with the \
scene plan, which the storyboard writer and the render code use. In the rules below, \
"paper" means this topic and this project.

Choose one anchor idea: the single idea from this topic that the project depends on \
most. The video teaches that idea; the digest also covers the rest of the topic in \
brief. In the scene plan, application_or_implication says where the idea lives in \
Daniel's project, and street_test_question is a question he can answer about his own \
project.

Grounding: every number you write, in the digest or the plan, appears in the source \
text; a check rejects the plan otherwise. General knowledge about the concept is \
welcome, but a fact about Daniel's project comes only from the source text, and you \
name a file or a function only when the source text shows it.

Digest sections, in this order: headline (the anchor idea in one sentence), the idea \
in plain words, why the project needed it, mechanism, where it lives in the project \
(files and functions from the source text), a worked example with the project's real \
numbers, the rest of the topic in brief, common confusion, limits, exercise (from the \
study map, when it has one). The mechanism section opens with a line that starts with \
"Components:" and lists the real parts that make the mechanism work. The diagram in \
the plan uses these same names, so name the parts here first.

"""

LESSON_READ_SYSTEM = _LESSON_READ_INTRO + _PLAN_RULES + _EXAMPLE_BLOCK

# Study videos for the built track: material Daniel is learning, not code he
# built, like system design prep for an interview.
_STUDY_READ_INTRO = """You are the deep-read writer for AgentLab's study videos. \
Daniel is preparing for a system design interview, and the study plan in the source \
names the role and the company. The source text inside the <source_text> tag holds one \
topic from his study plan (the topic, what to study, and the study material for it) and \
the whole study plan. Your reply has two parts, in this order: a complete digest in \
markdown, which Daniel reads after the video, then one fenced ```json block with the \
scene plan, which the storyboard writer and the render code use. In the rules below, \
"paper" means this topic.

Choose one anchor idea: the single idea from this topic that an interviewer probes \
most. The video teaches that idea through one concrete example. For a design problem \
("Design X"), the anchor is the deep dive that decides the design, shown on top of a \
short sketch of the high-level design. In the scene plan, application_or_implication \
says where the idea shows up in a design interview, and street_test_question is a \
question an interviewer would ask about it.

Grounding: every number you write, in the digest or the plan, appears in the source \
text; a check rejects the plan otherwise. General knowledge about the concept is \
welcome. Say nothing about the company's own systems; the only link to the company is \
the analogy section below, and that line starts with "Analogy:".

Digest sections, in this order: headline (the anchor idea in one sentence), the idea \
in plain words, the problem it solves, mechanism, the decision ladder (the simplest \
choice first, then the signal that calls for each stronger one), a worked example with \
numbers from the source, how it builds on earlier topics in the study plan, common \
interview mistakes, limits, an analogy to the company's product (one line), a practice \
question. The mechanism section opens with a line that starts with "Components:" and \
lists the real parts that make the mechanism work. The diagram in the plan uses these \
same names, so name the parts here first.

"""

STUDY_READ_SYSTEM = _STUDY_READ_INTRO + _PLAN_RULES + _EXAMPLE_BLOCK


def build_deep_read_prompt(url: str, source_text: str) -> str:
    # The source comes first and the task last: with long inputs, the query
    # at the end measurably improves the answer (Anthropic prompting best
    # practices, long context section), and one short recap of the two rules
    # the checks enforce is cheaper than a failed plan.
    return (
        f"Source URL: {url}\n\n"
        f"<source_text>\n{source_text}\n</source_text>\n\n"
        "That is the whole source. Write the digest, then the fenced json scene "
        "plan. Every number comes from the source text above, and the source is "
        "credited only with what it states."
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
    "application_or_implication": MAX_CLAIM,
    "limits_or_caveats": MAX_LIMITS,
    "street_test_question": MAX_QUESTION,
}
_STEP_CLIP_LIMITS = {
    "label": MAX_LABEL,
    "detail": MAX_DETAIL,
    "narration": MAX_NARRATION,
}
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


def _normalise_source_number(value: str) -> str:
    # All whitespace, not just spaces: the number regex spans a newline, so a
    # digest that wraps between "74.2" and "percent" must still read as
    # "74.2%" (it did not, and killed a track on 2026-09-21).
    collapsed = re.sub(r"\s+", "", value.lower())
    return collapsed.replace(",", "").replace("percent", "%")


def _rounded_forms(value: str) -> set[str]:
    """A grounded decimal rounded to a whole number and to one decimal.

    The unit stays as written: "26.4%" gives "26%" and "26.4%", and "26.4"
    gives "26" and "26.4". Each source number is already grounded with and
    without "%", so both forms round.
    """
    match = _DECIMAL_NUMBER_RE.fullmatch(value)
    if not match:
        return set()
    number, unit = Decimal(match.group(1)), match.group(2)
    forms = set()
    for step in (Decimal(1), Decimal("0.1")):
        try:
            forms.add(f"{number.quantize(step, rounding=ROUND_HALF_UP)}{unit}")
        except InvalidOperation:  # more digits than the decimal context holds
            continue
    return forms


def _grounded_source_numbers(source_text: str) -> set[str]:
    grounded: set[str] = set()
    for value in _SOURCE_NUMBER_RE.findall(source_text):
        normalised = _normalise_source_number(value)
        grounded.add(normalised)
        # The guard catches invented numbers, not units. A source "22.9%"
        # grounds a plan "22.9" (four killed tracks, 2026-09-21 to 09-23), and
        # a bare table cell "64.60" grounds "64.60%": papers put the % in the
        # column header (Cache-to-Cache deep read, 2026-10-04).
        if normalised.endswith("%"):
            grounded.add(normalised[:-1])
        else:
            grounded.add(normalised + "%")
    # "v0.1.1" grounds "0.1.1". _SOURCE_NUMBER_RE refuses a number preceded by
    # a letter (so "gpt4" never grounds "4"), which also hides every version
    # string behind its own "v".
    grounded.update(_VERSION_NUMBER_RE.findall(source_text))
    # Rounding is not inventing: the source said "26.4 percentage points" and
    # the plan said "26 percent" in seven of 24 failed runs up to 2026-10-04.
    grounded.update(form for value in list(grounded) for form in _rounded_forms(value))
    return grounded


def _ungrounded_in(text: str, grounded: set[str]) -> list[str]:
    missing: list[str] = []
    for value in _SOURCE_NUMBER_RE.findall(text):
        if _normalise_source_number(value) not in grounded and value not in missing:
            missing.append(value)
    return missing


def _plan_prose(plan: ScenePlan) -> str:
    """Every part of the plan a viewer reads or hears, except key_numbers.

    key_numbers is the one field the template can drop: video_scenes.py
    renders the numbers card only when the list is non-empty.
    """
    return "\n".join(
        [
            plan.title,
            plan.one_line_claim,
            plan.application_or_implication,
            plan.limits_or_caveats,
            plan.street_test_question,
            *(node.label for node in plan.diagram.nodes),
            *(edge.label or "" for edge in plan.diagram.edges),
            *(
                text
                for step in plan.mechanism_steps
                for text in (step.label, step.detail, step.narration)
            ),
        ]
    )


def _ungrounded_source_numbers(
    digest: str, plan: ScenePlan, source_text: str
) -> list[str]:
    grounded = _grounded_source_numbers(source_text)
    key_number_text = "\n".join(
        text for number in plan.key_numbers for text in (number.value, number.meaning)
    )
    return _ungrounded_in(
        "\n".join([digest, _plan_prose(plan), key_number_text]), grounded
    )


def drop_ungrounded_key_numbers(
    digest: str, plan: ScenePlan, source_text: str
) -> list[str]:
    """Strip key_numbers that carry an ungrounded number. Returns what is left
    ungrounded in the digest and the spoken plan, which is never droppable.

    A stray figure in the numbers card is not worth losing the whole video:
    the card is optional in the template, so it goes and the video ships. A
    number inside the digest or the narration is a claim Daniel reads or
    hears, so that still fails the run.
    """
    grounded = _grounded_source_numbers(source_text)
    plan.key_numbers = [
        number
        for number in plan.key_numbers
        if not _ungrounded_in(f"{number.value}\n{number.meaning}", grounded)
    ]
    return _ungrounded_in("\n".join([digest, _plan_prose(plan)]), grounded)


def deep_read(
    url: str,
    fetch_text: Callable[[str], str],
    complete: Callable[[str, list[dict]], str],
    model: str = DEFAULT_DEEP_READ_MODEL,
    system: str = DEEP_READ_SYSTEM,
) -> tuple[str, ScenePlan]:
    """Fetch a source and turn it into (digest markdown, validated ScenePlan).
    `system` is DEEP_READ_SYSTEM for a paper, LESSON_READ_SYSTEM for a topic.

    One completion is asked to produce both artifacts at once (the digest is
    the depth layer, the plan drives the video); if the trailing JSON fails
    schema validation, retry once with the pydantic error appended so the
    model can fix only the JSON.
    """
    source_text = fetch_text(url)
    # The model reads the visible prose, capped to fit the context window.
    # The grounding guard below keeps checking the FULL fetched source: the
    # model can only cite what it was shown, so a smaller prompt can only
    # make the guard easier to satisfy, never harder.
    messages = [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": build_deep_read_prompt(url, prepare_source(source_text)),
        },
    ]
    raw = complete(model, messages)
    digest, plan_raw = split_digest_and_plan(raw)
    plan, error = _parse_scene_plan_verbose(plan_raw)
    if plan is not None:
        missing = _ungrounded_source_numbers(digest, plan, source_text)
        if missing:
            plan = None
            error = "numbers missing from fetched source: " + ", ".join(missing)
    if plan is None:
        retry_messages = messages + [
            {
                "role": "user",
                "content": (
                    "Your last reply was invalid. "
                    f"Validation error: {error}\n"
                    "Send the full digest again, then a corrected "
                    "```json fenced scene plan at the very end. Fix the stated "
                    "error and keep every claim grounded in the source text."
                ),
            }
        ]
        raw = complete(model, retry_messages)
        digest, plan_raw = split_digest_and_plan(raw)
        plan, error = _parse_scene_plan_verbose(plan_raw)
        if plan is not None:
            missing = _ungrounded_source_numbers(digest, plan, source_text)
            if missing:
                # The model had its retry and still wrote a number the source
                # does not contain. Drop what is droppable rather than losing
                # the day's video to the numbers card.
                still_missing = drop_ungrounded_key_numbers(digest, plan, source_text)
                if still_missing:
                    plan = None
                    error = "numbers missing from fetched source: " + ", ".join(
                        still_missing
                    )
    if plan is None:
        raise ValueError(f"scene plan invalid after retry for {url}: {error}")
    # The model never writes the citation: the fetch URL is ground truth
    # (a hallucinated citation burned this lab once, see proposer.py).
    plan.citation_url = url
    return digest, plan


PICK_SYSTEM = """You pick one candidate from a numbered list for Daniel's daily paper \
video. Code reads your reply as a number, so reply with the number of your pick and \
nothing else."""

RANK_SYSTEM = """You rank candidates from a numbered list for Daniel's daily paper video. \
Code reads your reply as a JSON array of candidate numbers, best first, so reply with \
that array and nothing else."""

_ANSWER_LINE = "Reply with the number only."

_NOVEL_INSTRUCTION = (
    "Pick the candidate Daniel is least likely to already know about, the one "
    "that sounds the coolest and most frontier-opening. Ignore Daniel's interests "
    "below except to avoid picking something that overlaps what he already "
    "tracks; the goal here is surprise, not relevance."
)
_PAPER_PREFERENCE = (
    " Prefer candidates from arxiv or hf over hn ones; pick an hn candidate"
    " only if its title clearly refers to an actual paper."
)
_CORE_INSTRUCTION = (
    "Pick the single candidate most relevant to Daniel's interests below."
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
    instruction = (
        (_NOVEL_INSTRUCTION + _PAPER_PREFERENCE)
        if mode == "novel"
        else _CORE_INSTRUCTION
    )
    return (
        f"Candidates:\n{lines}\n\n"
        f"Daniel's interests:\n{interests_text}\n\n"
        f"{instruction}\n\n{_ANSWER_LINE}"
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
            {
                "role": "user",
                "content": build_pick_prompt(candidates, interests_text, mode),
            },
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
        _ANSWER_LINE, 1
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
