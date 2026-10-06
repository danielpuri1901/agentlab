"""Turn a grounded paper plan into a 3Blue1Brown-style explanation.

The storyboard shows the paper's real mechanism with the visuals that explain
it best, in 3Blue1Brown's style (docs/superpowers/specs/
2026-10-05-3b1b-style-videos-design.md). The narration explains in plain
words: it starts with the paper title and a plain definition, and every
number comes from the source.
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
MIN_BEATS = 6
MAX_BEATS = 12
MAX_NARRATION = 240
MAX_VISUAL = 500
MAX_ON_SCREEN = 2
MAX_ON_SCREEN_LEN = 70
MAX_STORYBOARD_ATTEMPTS = 3


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
    """No valid storyboard was produced within the bounded attempt limit."""


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%|\d{3,}")


def _normalise(text: str) -> str:
    return text.replace(",", "").replace(" %", "%")


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


def _fit_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    clipped = text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:")
    return clipped.rstrip(".") + "."


def _repair_ungrounded_numbers(board: Storyboard, digest: str, plan: ScenePlan) -> None:
    mechanism_index = 0
    for beat in board.beats:
        narration_data = {"beats": [{"narration": beat.narration}]}
        if ungrounded_numbers(narration_data, digest, plan):
            if beat.role == "title":
                definition_limit = min(
                    MAX_DEFINITION, MAX_NARRATION - len(plan.title) - 2
                )
                board.simple_definition = _fit_text(
                    plan.one_line_claim, definition_limit
                )
                beat.narration = f"{plan.title}. {board.simple_definition}"
            elif beat.role == "problem":
                beat.narration = _fit_text(plan.one_line_claim, MAX_NARRATION)
            elif beat.role == "mechanism":
                step = plan.mechanism_steps[
                    min(mechanism_index, len(plan.mechanism_steps) - 1)
                ]
                beat.narration = _fit_text(step.narration, MAX_NARRATION)
            elif beat.role == "result" and plan.key_numbers:
                number = plan.key_numbers[0]
                beat.narration = _fit_text(
                    f"{number.value}: {number.meaning}", MAX_NARRATION
                )
            elif beat.role == "application":
                beat.narration = plan.application_or_implication
            elif beat.role == "limit":
                beat.narration = plan.limits_or_caveats
            elif beat.role == "question":
                beat.narration = plan.street_test_question
            else:
                beat.narration = _fit_text(plan.one_line_claim, MAX_NARRATION)
        beat.on_screen_text = [
            label
            for label in beat.on_screen_text
            if not ungrounded_numbers(
                {"beats": [{"on_screen_text": [label]}]}, digest, plan
            )
        ]
        if beat.role == "mechanism":
            mechanism_index += 1


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


def parse_storyboard(
    raw: str, digest: str, plan: ScenePlan
) -> tuple[Storyboard | None, str]:
    """Only a JSON shape error or an ungrounded number rejects a storyboard.

    Everything else is repaired in place: the title beat is rebuilt from the
    plan, the application and question beats take the plan's grounded text,
    numbers the source never states are replaced, and an em dash becomes a
    hyphen. No rule decides which content goes in which beat.
    """
    text = extract_json_object(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "storyboard JSON must be an object"
    data = json.loads(json.dumps(data, ensure_ascii=False).replace("\u2014", "-"))
    data = _clip(data)
    try:
        board = Storyboard(**data)
    except ValidationError as exc:
        return None, str(exc)
    board.title = plan.title
    board.simple_definition = _fit_text(
        board.simple_definition, MAX_NARRATION - len(plan.title) - 2
    )
    board.beats[0].narration = f"{plan.title}. {board.simple_definition}"
    board.beats[0].on_screen_text = [plan.title]
    if board.beats and board.beats[-1].role == "question":
        board.beats[-1].narration = plan.street_test_question
    for beat in board.beats:
        if beat.role == "application":
            beat.narration = plan.application_or_implication
    _repair_ungrounded_numbers(board, digest, plan)
    missing = ungrounded_numbers(board.model_dump(), digest, plan)
    if missing:
        return None, (
            "these numbers appear in the beats but not in the digest or scene plan: "
            + ", ".join(missing)
            + ". Use only numbers the source states."
        )
    return board, ""


# Every rule below has a source in
# docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md (section 1).
STORYBOARD_SYSTEM = """You are the director of a short explainer video in the style of 3Blue1Brown.
The viewer is Daniel. He must understand the paper's mechanism on the first watch.
Clear beats clever.

How 3Blue1Brown explains:
- Start from the visuals. Let the explanation form around what the viewer sees.
- Go from concrete to abstract. Show one concrete example before the general rule.
- Keep one or two examples front and centre for the whole video.
- Show the real thing: the actual objects, a graph, an equation, a geometric picture, or a diagram of the real parts. Use a metaphor only when it explains better than the real object.
- Give each key concept one colour, and keep that colour in every formula and picture for the whole video. Use two to four concept colours in a scene.
- Build a few objects early and keep them on screen. Change them from beat to beat: an equation rearranges, a curve shifts, a new symbol grows out of a copy of an old one. Do not restart the picture in every beat.
- Nothing is on screen for decoration. Every motion explains something.
- Use 3D only for spatial ideas: vector or embedding spaces, surfaces, layers stacked in depth.
- Formulas are welcome when they carry the mechanism. Show the picture first, then the formula that names it.

The narration explains the paper in plain words and may refer to what is on screen.
Start with the exact paper title and one simple definition of the main idea.
Then show a concrete problem from the paper, the real mechanism through cause and effect,
one grounded result, one practical application or implication, one limit, and the street-test question.
If the application is your inference rather than the paper's claim, say that plainly.

Write 6 to 12 beats with this exact role order:
1. title
2. problem
3. mechanism beats, as many as the mechanism needs
4. result
5. application
6. limit
7. question

Each beat has role, narration, visual, and on_screen_text.
The visual says what the viewer sees and how it changes from the previous beat.
Narration has one or two short sentences. Each sentence has at most 22 words.
Use common words. Define a necessary technical term before using it.
Never use an em dash.

The first beat's on_screen_text must contain the exact storyboard title.
The first beat's narration must contain simple_definition exactly.
Use at most two short on-screen labels per beat.
Every number must occur in the digest or scene plan.
Everything must be drawable in Manim: text, LaTeX formulas, shapes, graphs, axes, number lines, matrices, 3D surfaces, and camera moves.
Nothing comes from image files.

Output exactly one fenced json block with keys title, simple_definition, visual_focus,
mapping, and beats. visual_focus names the visual approach in one sentence. mapping is the
colour key: a list of two to eight objects with paper_term (the paper's own name for a
concept) and visual (its colour and how it appears on screen, for example "yellow, an
arrow from the origin").
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
            + "\nDo not reuse these visual ideas, and keep the same 3Blue1Brown style."
        )
    return (
        "<digest>\n"
        f"{digest}\n"
        "</digest>\n\n"
        "<scene_plan>\n"
        f"{plan.model_dump_json(indent=1)}\n"
        "</scene_plan>\n\n"
        "Explain this paper the way 3Blue1Brown would, starting from its real "
        "mechanism. Start with the title and a simple definition. End with the result, "
        "application, limit, and street-test question. Return the fenced json "
        "storyboard." + recent
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
    error = ""
    for _ in range(MAX_STORYBOARD_ATTEMPTS):
        raw = complete(model, messages)
        board, error = parse_storyboard(raw, digest, plan)
        if board is not None:
            return board
        messages += [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "The storyboard was invalid. Fix this validation error: "
                    f"{error}\nReturn the complete corrected fenced json storyboard."
                ),
            },
        ]
    raise StoryboardInvalid(
        f"storyboard invalid after {MAX_STORYBOARD_ATTEMPTS} attempts: {error}"
    )
