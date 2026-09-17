"""Turn a grounded paper plan into a simple visual explanation.

The storyboard shows the paper's real parts and mechanism. It starts with
the paper title and a plain definition. It does not invent an analogy.
"""

import json
import re
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator

from agentlab.scene_plan import ScenePlan, extract_json_object

MAX_TITLE = 70
MAX_DEFINITION = 180
MAX_VISUAL_FOCUS = 240
MIN_MAPPING = 2
MAX_MAPPING = 8
MAX_TERM = 40
MAX_MAPPING_VISUAL = 80
MIN_BEATS = 8
MAX_BEATS = 10
MAX_NARRATION = 240
MAX_VISUAL = 500
MAX_ON_SCREEN = 2
MAX_ON_SCREEN_LEN = 70
MAX_SENTENCE_WORDS = 22


class Mapping(BaseModel):
    paper_term: str = Field(min_length=1, max_length=MAX_TERM)
    visual: str = Field(min_length=1, max_length=MAX_MAPPING_VISUAL)


class Beat(BaseModel):
    role: Literal[
        "title", "problem", "mechanism", "result", "application", "limit", "question"
    ]
    narration: str = Field(min_length=1, max_length=MAX_NARRATION)
    visual: str = Field(min_length=1, max_length=MAX_VISUAL)
    on_screen_text: list[str] = Field(default_factory=list, max_length=MAX_ON_SCREEN)

    @field_validator("on_screen_text")
    @classmethod
    def _labels_short(cls, labels: list[str]) -> list[str]:
        for label in labels:
            if not label or len(label) > MAX_ON_SCREEN_LEN:
                raise ValueError(
                    f"on_screen_text entries must be 1 to {MAX_ON_SCREEN_LEN} characters"
                )
        return labels


class Storyboard(BaseModel):
    title: str = Field(min_length=1, max_length=MAX_TITLE)
    simple_definition: str = Field(min_length=1, max_length=MAX_DEFINITION)
    visual_focus: str = Field(min_length=1, max_length=MAX_VISUAL_FOCUS)
    mapping: list[Mapping] = Field(min_length=MIN_MAPPING, max_length=MAX_MAPPING)
    beats: list[Beat] = Field(min_length=MIN_BEATS, max_length=MAX_BEATS)


class StoryboardInvalid(ValueError):
    """No valid storyboard was produced after one correction attempt."""


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%|\d{3,}")
_ANALOGY_OPEN_RE = re.compile(
    r"\b(imagine|picture this|think of|it is like|it's like|as if)\b", re.IGNORECASE
)


def _normalise(text: str) -> str:
    return text.replace(",", "").replace(" %", "%")


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def ungrounded_numbers(data: dict, digest: str, plan: ScenePlan) -> list[str]:
    """Find numbers in beats that do not occur in the source material."""
    grounded = set(
        _NUMBER_RE.findall(_normalise(digest + "\n" + plan.model_dump_json()))
    )
    found: list[str] = []
    for beat in data.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        texts = [beat.get("narration", "")] + list(beat.get("on_screen_text") or [])
        for text in texts:
            if not isinstance(text, str):
                continue
            for token in _NUMBER_RE.findall(_normalise(text)):
                if token not in grounded and token not in found:
                    found.append(token)
    return found


def _clip(data: dict) -> dict:
    for key, limit in (
        ("title", MAX_TITLE),
        ("simple_definition", MAX_DEFINITION),
        ("visual_focus", MAX_VISUAL_FOCUS),
    ):
        if isinstance(data.get(key), str):
            data[key] = data[key][:limit]
    if isinstance(data.get("mapping"), list):
        for item in data["mapping"]:
            if isinstance(item, dict):
                for key, limit in (
                    ("paper_term", MAX_TERM),
                    ("visual", MAX_MAPPING_VISUAL),
                ):
                    if isinstance(item.get(key), str):
                        item[key] = item[key][:limit]
    if isinstance(data.get("beats"), list):
        for beat in data["beats"]:
            if isinstance(beat, dict):
                for key, limit in (
                    ("narration", MAX_NARRATION),
                    ("visual", MAX_VISUAL),
                ):
                    if isinstance(beat.get(key), str):
                        beat[key] = beat[key][:limit]
                if isinstance(beat.get("on_screen_text"), list):
                    beat["on_screen_text"] = [
                        value[:MAX_ON_SCREEN_LEN]
                        for value in beat["on_screen_text"]
                        if isinstance(value, str) and value
                    ][:MAX_ON_SCREEN]
    return data


def _structure_error(board: Storyboard, plan: ScenePlan) -> str:
    if board.title != plan.title:
        return "storyboard title must match the scene plan title exactly"
    roles = [beat.role for beat in board.beats]
    if roles[:2] != ["title", "problem"]:
        return "beats 1 and 2 must have roles title and problem"
    if roles[-4:] != ["result", "application", "limit", "question"]:
        return "the final four beats must have roles result, application, limit, and question"
    if any(role != "mechanism" for role in roles[2:-4]):
        return "all beats between problem and result must have role mechanism"
    first = board.beats[0]
    if board.title not in first.on_screen_text:
        return "beat 1 on_screen_text must contain the storyboard title exactly"
    if _ANALOGY_OPEN_RE.search(first.narration):
        return "beat 1 must start directly; do not open with an analogy"
    required_start = f"{board.title}. {board.simple_definition}"
    if not first.narration.startswith(required_start):
        return "beat 1 narration must start with title, then simple_definition"
    if _plain(board.beats[-1].narration) != _plain(plan.street_test_question):
        return "the question beat must use the scene plan street-test question exactly"
    if plan.key_numbers:
        result_beat = next(beat for beat in board.beats if beat.role == "result")
        result_text = result_beat.narration + " " + " ".join(result_beat.on_screen_text)
        if not any(number.value in result_text for number in plan.key_numbers):
            return "the result beat must use at least one grounded key number"
    if "—" in board.model_dump_json():
        return "use a plain hyphen or period instead of an em dash"
    for index, beat in enumerate(board.beats, start=1):
        sentences = [
            part.strip() for part in re.split(r"[.!?]+", beat.narration) if part.strip()
        ]
        for sentence in sentences:
            if len(sentence.split()) > MAX_SENTENCE_WORDS:
                return (
                    f"beat {index} has a sentence longer than "
                    f"{MAX_SENTENCE_WORDS} words"
                )

    allowed_terms = {
        _plain(value) for node in plan.diagram.nodes for value in (node.id, node.label)
    } | {_plain(step.label) for step in plan.mechanism_steps}
    unknown = [
        item.paper_term
        for item in board.mapping
        if _plain(item.paper_term) not in allowed_terms
    ]
    if unknown:
        return "mapping uses terms outside the scene plan: " + ", ".join(unknown)
    return ""


def parse_storyboard(
    raw: str, digest: str, plan: ScenePlan
) -> tuple[Storyboard | None, str]:
    text = extract_json_object(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "storyboard JSON must be an object"
    data = _clip(data)
    try:
        board = Storyboard(**data)
    except ValidationError as exc:
        return None, str(exc)
    board.title = plan.title
    title_narration = f"{plan.title}. {board.simple_definition}"
    if len(title_narration) > MAX_NARRATION:
        return None, "title and simple_definition exceed the narration limit"
    board.beats[0].narration = title_narration
    board.beats[0].on_screen_text = [plan.title]
    if board.beats and board.beats[-1].role == "question":
        board.beats[-1].narration = plan.street_test_question
    for beat in board.beats:
        if beat.role == "application":
            beat.narration = plan.application_or_implication
    error = _structure_error(board, plan)
    if error:
        return None, error
    missing = ungrounded_numbers(board.model_dump(), digest, plan)
    if missing:
        return None, (
            "these numbers appear in the beats but not in the digest or scene plan: "
            + ", ".join(missing)
            + ". Use only numbers the source states."
        )
    return board, ""


STORYBOARD_SYSTEM = """You are the visual director for a 90-second research-paper video.
Make it clear, visually compelling, and memorable.
Use the paper's real mechanism and evidence.
Treat the scene plan as a factual brief, not as a required diagram or layout.

Choose one strong visual concept designed for this paper.
Abstract visual systems are welcome when their meaning is easy to follow.
Use shape, space, scale, rhythm, contrast, and transformation to make ideas visible.
Change the composition when a new view makes the idea clearer.
Avoid a generic row of boxes unless that is truly the clearest explanation.
Do not use an unrelated metaphor, mascot, or story.

Start with the exact paper title and one simple definition of the main idea.
Then establish a concrete input or problem from the paper.
Show the real mechanism through cause and effect.
Then show one grounded result, one practical application or implication, one limit, and the street-test question.
If the application is your inference rather than the paper's claim, say that plainly.

For every beat, first decide what the viewer must understand.
Then choose the clearest visual explanation for someone seeing the idea for the first time.
Describe meaningful motion and transitions, not a static inventory of objects.

Write 8 to 10 beats with this exact role order:
1. title
2. problem
3. two to four mechanism beats
4. result
5. application
6. limit
7. question

Each beat has role, narration, visual, and on_screen_text.
Narration has one or two short sentences. Each sentence has at most 22 words.
Use common words. Define a necessary technical term before using it.
Never open with "imagine", "picture this", "think of", "it is like", or "as if".
Never use an em dash.

The first beat's on_screen_text must contain the exact storyboard title.
The first beat's narration must contain simple_definition exactly.
Use at most two short on-screen labels per beat.
Only use paper terms that occur as diagram node labels or mechanism step labels.
Every number must occur in the digest or scene plan.
Everything must be drawable with Manim text, shapes, paths, particles, and transformations.

Output exactly one fenced json block with keys title, simple_definition, visual_focus,
mapping (a list of paper_term and visual objects), and beats.
Nothing can follow the json block."""


def build_storyboard_prompt(
    digest: str,
    plan: ScenePlan,
    recent_visual_directions: list[str] | None = None,
) -> str:
    recent = ""
    if recent_visual_directions:
        recent = (
            "\n\nRecent visual directions to avoid repeating:\n- "
            + "\n- ".join(recent_visual_directions)
            + "\nChoose a substantially different visual concept, composition, and motion system."
        )
    return (
        "<digest>\n"
        f"{digest}\n"
        "</digest>\n\n"
        "<scene_plan>\n"
        f"{plan.model_dump_json(indent=1)}\n"
        "</scene_plan>\n\n"
        "Explain this paper directly. Show its real components and relationships. "
        "Start with the title and a simple definition. End with the result, application, "
        "limit, and street-test question. Return the fenced json storyboard." + recent
    )


def design_storyboard(
    digest: str,
    plan: ScenePlan,
    complete: Callable[[str, list[dict]], str],
    model: str,
    recent_visual_directions: list[str] | None = None,
) -> Storyboard:
    messages = [
        {"role": "system", "content": STORYBOARD_SYSTEM},
        {
            "role": "user",
            "content": build_storyboard_prompt(
                digest, plan, recent_visual_directions=recent_visual_directions
            ),
        },
    ]
    raw = complete(model, messages)
    board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        retry = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "The storyboard was invalid. Fix this validation error: "
                    f"{error}\nReturn the complete corrected fenced json storyboard."
                ),
            },
        ]
        raw = complete(model, retry)
        board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        raise StoryboardInvalid(f"storyboard invalid after retry: {error}")
    return board
