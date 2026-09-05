"""Storyboard: the visual-choice step of the metaphor videos
(docs/specs/2026-09-05-metaphor-videos.md, section 1).

One creative model call turns the deep read (digest + grounded ScenePlan)
into a Storyboard: one metaphor, a paper-term-to-visual mapping, and 5 to
9 beats of narration plus a concrete visual description. The coder
(scene_code.py) draws from this; the judge (frame_judge.py) checks against
it. Same parse discipline as scene_plan.py: fences and prose tolerated,
over-long strings clipped, structure and grounding fatal (one retry, then
StoryboardInvalid so the worker falls back to the template).

Temperature: litellm sends no temperature unless asked, and the Anthropic
API's default is 1.0, which is the high-ambiguity setting this step wants.
"""

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from agentlab.scene_plan import ScenePlan, extract_json_object

MAX_METAPHOR = 200
MAX_WHY = 300
MAX_REJECTED = 3
MAX_REJECTED_LEN = 200
MIN_MAPPING = 2
MAX_MAPPING = 6
MAX_TERM = 40
MAX_MAPPING_VISUAL = 60
MIN_BEATS = 5
MAX_BEATS = 9
MAX_NARRATION = 280
MAX_VISUAL = 500
MAX_ON_SCREEN = 3
MAX_ON_SCREEN_LEN = 40


class Mapping(BaseModel):
    paper_term: str = Field(min_length=1, max_length=MAX_TERM)
    visual: str = Field(min_length=1, max_length=MAX_MAPPING_VISUAL)


class Beat(BaseModel):
    narration: str = Field(min_length=1, max_length=MAX_NARRATION)
    visual: str = Field(min_length=1, max_length=MAX_VISUAL)
    on_screen_text: list[str] = Field(default_factory=list, max_length=MAX_ON_SCREEN)

    @field_validator("on_screen_text")
    @classmethod
    def _labels_short(cls, labels: list[str]) -> list[str]:
        for label in labels:
            if not label or len(label) > MAX_ON_SCREEN_LEN:
                raise ValueError(f"on_screen_text entries must be 1 to {MAX_ON_SCREEN_LEN} characters")
        return labels


class Storyboard(BaseModel):
    metaphor: str = Field(min_length=1, max_length=MAX_METAPHOR)
    why_this_metaphor: str = Field(min_length=1, max_length=MAX_WHY)
    rejected: list[str] = Field(default_factory=list, max_length=MAX_REJECTED)
    mapping: list[Mapping] = Field(min_length=MIN_MAPPING, max_length=MAX_MAPPING)
    beats: list[Beat] = Field(min_length=MIN_BEATS, max_length=MAX_BEATS)


class StoryboardInvalid(ValueError):
    """No valid storyboard after the retry; the caller falls back to the template."""


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%|\d{3,}")


def _normalise(text: str) -> str:
    return text.replace(",", "").replace(" %", "%")


def _collect_ungrounded(text: object, haystack: str, found: list[str]) -> None:
    if not isinstance(text, str):
        return
    for token in _NUMBER_RE.findall(_normalise(text)):
        if token not in haystack and token not in found:
            found.append(token)


def ungrounded_numbers(data: dict, digest: str, plan: ScenePlan) -> list[str]:
    """Numbers in beat narration or on-screen text that appear neither in
    the digest nor in the scene plan (which the deep read already grounded).
    Percentages and any 3+ digit run count; commas are ignored so 1,250
    matches 1250. Narration is scanned across all beats first, then
    on-screen text across all beats, so the narration's numbers (the
    voiceover's claims) surface before label text in the returned order."""
    haystack = _normalise(digest + "\n" + plan.model_dump_json())
    beats = [beat for beat in (data.get("beats") or []) if isinstance(beat, dict)]
    found: list[str] = []
    for beat in beats:
        _collect_ungrounded(beat.get("narration", ""), haystack, found)
    for beat in beats:
        for text in beat.get("on_screen_text") or []:
            _collect_ungrounded(text, haystack, found)
    return found


def _clip(data: dict) -> dict:
    for key, limit in (("metaphor", MAX_METAPHOR), ("why_this_metaphor", MAX_WHY)):
        if isinstance(data.get(key), str):
            data[key] = data[key][:limit]
    if isinstance(data.get("rejected"), list):
        data["rejected"] = [r[:MAX_REJECTED_LEN] for r in data["rejected"] if isinstance(r, str)][:MAX_REJECTED]
    if isinstance(data.get("mapping"), list):
        for item in data["mapping"]:
            if isinstance(item, dict):
                for key, limit in (("paper_term", MAX_TERM), ("visual", MAX_MAPPING_VISUAL)):
                    if isinstance(item.get(key), str):
                        item[key] = item[key][:limit]
    if isinstance(data.get("beats"), list):
        for beat in data["beats"]:
            if isinstance(beat, dict):
                for key, limit in (("narration", MAX_NARRATION), ("visual", MAX_VISUAL)):
                    if isinstance(beat.get(key), str):
                        beat[key] = beat[key][:limit]
                if isinstance(beat.get("on_screen_text"), list):
                    beat["on_screen_text"] = [
                        t[:MAX_ON_SCREEN_LEN] for t in beat["on_screen_text"] if isinstance(t, str) and t
                    ][:MAX_ON_SCREEN]
    return data


def parse_storyboard(raw: str, digest: str, plan: ScenePlan) -> tuple[Storyboard | None, str]:
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
    missing = ungrounded_numbers(data, digest, plan)
    if missing:
        return None, (
            "these numbers appear in the beats but not in the digest or scene plan: "
            + ", ".join(missing)
            + ". Use only numbers the source states."
        )
    return board, ""


STORYBOARD_SYSTEM = """You design the visual story for a 90-second explainer video \
about one research paper, in the style of 3Blue1Brown: one metaphor, one object the \
viewer watches change, and the change IS the explanation. You are given the paper's \
digest and a grounded scene plan (claim, mechanism, numbers, limits, street-test \
question).

Work in two parts. First, list three candidate metaphors, one line each. A candidate \
names a concrete physical object or scene the viewer can watch (a suitcase being packed, \
a tree being pruned, a wall of dials, a queue at a gate, a map with routes, a bucket \
filling, a scale tipping), what it stands for, and what visible change on it shows the \
paper's mechanism working. Then pick the one where cause and effect is most visible on \
screen, and say why in one sentence. The two you did not pick go in "rejected".

Then write the beats, 5 to 9 (five to nine). Each beat has:
- narration: what the voice says, 1 to 3 short spoken sentences, at most 280 characters. \
This text is also the caption.
- visual: what is on screen and what changes during this beat, concrete enough that a \
programmer can draw it with circles, rectangles, lines, dots, arrows, plain text and \
simple bars. Say what appears, where (left, right, centre, above), what moves, what \
changes colour or size, and what that change proves. At most 500 characters.
- on_screen_text: up to three short labels (at most 40 characters each) that should be \
drawn as text. Optional.

Rules:
- Beat 1 is the hook: the object appears and the viewer learns what it stands for. Do \
not open with the paper title or a definition.
- The middle beats show the mechanism as changes on the object. Before and after on the \
same object beats a list of steps.
- The paper's real numbers appear ON the metaphor: a bar grows to 41%, a counter climbs \
to 1250, three of ten items turn red. Never a separate statistics slide.
- The second-to-last beat states the limits: what the paper does NOT claim, shown as a \
boundary on the object.
- The last beat asks the scene plan's street-test question, with the object still on \
screen.
- One metaphor for the whole video. Never a flowchart, never a row of labelled boxes \
with arrows, never boxes lighting up in order, never a pipeline diagram.
- No emoji, no images, no photos: everything must be drawable with simple shapes and \
text.
- Narration is plain spoken English: short sentences, one idea per sentence, never an \
em dash (use a comma or a period).
- Every number in narration or on-screen text must appear in the digest or the scene \
plan, written the same way (44%, 1250). Never attribute to the paper anything it does \
not say.

Three exemplars, given only to show the level of concreteness. They are for other \
papers; never reuse them.

1. Compaction (a house move): "This house is an agent's whole chat. You must move today \
with one small suitcase." Visual: a grey house full of furniture blocks on the left, a \
gold-outlined suitcase on the right, a gold passport among the furniture. Next beat: the \
packing list says "summarize concisely", a sofa slides into the suitcase, the passport \
stays on the shelf and dims, a red cross appears. Next beat: the list says "list every \
code first", the passport slides in first and glows, a green check replaces the cross.

2. Tree of Thoughts (a growing tree): a single trunk labelled "prompt" grows three \
branches; each branch grows three twigs; a grey gardener's blade cuts the twigs whose \
small score circle is below 0.5, they fall away dim; the one surviving path glows and \
its leaf becomes the answer. Numbers: the survivor count shows "4 of 27 kept".

3. RLHF (a wall of dials): a grid of 40 small dials all pointing in random directions; \
two candidate answers appear as cards; a hand-shaped cursor taps the better card; a wave \
sweeps across the wall and every dial nudges a few degrees toward the same direction; \
after three taps the wall is almost aligned, and the alignment bar reads the paper's \
win rate.

Output: after your candidate list and choice, output exactly one fenced ```json block \
with keys metaphor, why_this_metaphor, rejected, mapping (list of {paper_term, \
visual}), beats (list of {narration, visual, on_screen_text}). Nothing after the block."""


def build_storyboard_prompt(digest: str, plan: ScenePlan) -> str:
    return (
        "<digest>\n"
        f"{digest}\n"
        "</digest>\n\n"
        "<scene_plan>\n"
        f"{plan.model_dump_json(indent=1)}\n"
        "</scene_plan>\n\n"
        "Reminder of the hard rules now that you have read the paper: one metaphor, the "
        "mechanism as visible change on one object, real numbers on the object, the limits "
        "in the second-to-last beat, the street-test question in the last beat, never a "
        "flowchart, only numbers the digest or scene plan states.\n\n"
        "List three candidate metaphors, choose one, then write the fenced json storyboard."
    )


def design_storyboard(
    digest: str, plan: ScenePlan, complete: Callable[[str, list[dict]], str], model: str
) -> Storyboard:
    messages = [
        {"role": "system", "content": STORYBOARD_SYSTEM},
        {"role": "user", "content": build_storyboard_prompt(digest, plan)},
    ]
    raw = complete(model, messages)
    board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        retry = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your storyboard JSON was invalid. Validation error: "
                    f"{error}\nSend the corrected fenced json storyboard again, "
                    "fixing only what the error names, keeping the same metaphor."
                ),
            },
        ]
        raw = complete(model, retry)
        board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        raise StoryboardInvalid(f"storyboard invalid after retry: {error}")
    return board
