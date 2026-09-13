"""Judge sampled story frames against their storyboard."""

import base64
import json
import logging
import math
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agentlab import video_render
from agentlab.scene_plan import ScenePlan, extract_json_object
from agentlab.storyboard import Storyboard

FALLBACK_SCORE = 5
FRAME_WIDTH = 960
MAX_MISSING_VISUALS = 0
MIN_PASS_SCORE = 7

logger = logging.getLogger(__name__)


class BeatJudgement(BaseModel):
    beat: int
    grounded: bool
    shows_visual: bool
    legible: bool
    clean: bool
    issue: str | None = None


class Judgement(BaseModel):
    beats: list[BeatJudgement]
    score: int = Field(ge=0, le=10)
    verdict: Literal["pass", "fix"] = "pass"
    note: str | None = None


def verdict_from_beats(beats: list[BeatJudgement], score: int = 10) -> str:
    if score < MIN_PASS_SCORE:
        return "fix"
    if any(not beat.grounded or not beat.clean or not beat.legible for beat in beats):
        return "fix"
    if sum(1 for beat in beats if not beat.shows_visual) > MAX_MISSING_VISUALS:
        return "fix"
    return "pass"


def parse_judgement(raw: str) -> tuple[Judgement | None, str]:
    if not isinstance(raw, str):
        return None, "judgement response must be text"
    try:
        data = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    except (AttributeError, TypeError, ValueError) as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "judgement must be a JSON object"

    beats = data.get("beats")
    if isinstance(beats, list) and beats:
        trailing = beats[-1]
        metadata_keys = {"score", "verdict", "note"}
        if (
            isinstance(trailing, dict)
            and "beat" not in trailing
            and "score" in trailing
            and set(trailing) <= metadata_keys
        ):
            data["beats"] = beats[:-1]
            for key in metadata_keys:
                if key in trailing and key not in data:
                    data[key] = trailing[key]

    score = data.get("score", FALLBACK_SCORE)
    try:
        numeric_score = float(score)
    except (TypeError, ValueError, OverflowError) as exc:
        return None, f"invalid score: {exc}"
    if not math.isfinite(numeric_score):
        return None, "invalid score: score must be finite"
    data["score"] = min(10, max(0, round(numeric_score)))
    data.pop("verdict", None)
    try:
        judgement = Judgement(**data)
    except ValidationError as exc:
        return None, str(exc)
    judgement.verdict = verdict_from_beats(judgement.beats, judgement.score)
    return judgement, ""


def sample_frames(video_path, times: list[float], out_dir) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for index, time in enumerate(times, start=1):
        output = out_dir / f"frame_{index:02d}.png"
        video_render.run_subprocess(
            [
                "ffmpeg",
                "-y",
                "-v",
                "error",
                "-ss",
                f"{time:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale={FRAME_WIDTH}:-1",
                str(output),
            ]
        )
        frames.append(output)
    return frames


JUDGE_SYSTEM = """You check rendered frames of a short explainer video against its \
storyboard. The video must explain the paper directly with its real components. Each \
frame is the middle of one beat. Compare every narration claim with the grounded scene \
plan. For each frame decide: grounded (does the narration stay within the scene plan), shows_visual \
(does the frame show what the beat's visual description says should be on screen at \
that point, roughly), legible (is every piece of text readable at this size, nothing \
tiny or garbled), clean (nothing overlaps another element, nothing is cut off at the \
frame edge, nothing is drawn over the caption text in the bottom band). Put a short \
concrete issue when something is wrong, else null. Then give an overall score from 0 \
to 10 for how well the frames explain the real mechanism. Beat 1 must show the title. \
The mechanism beats must show the named paper components and their cause-and-effect \
changes. Reject an unrelated analogy, decorative metaphor, unexplained technical term, \
or generic row of boxes. Mark the affected beat shows_visual=false and score the video \
at most 5. Be strict about overlap, cut-off elements, and missing mechanism details. \
Answer with ONLY a fenced json \
object: {"beats": [{"beat": 1, "grounded": true, "shows_visual": true, "legible": true, "clean": true, \
"issue": null}, ...], "score": 7}. The score is a top-level sibling after the closed \
beats array, never an item inside beats."""


def _image_part(path: Path) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


def build_judge_messages(
    frames: list[Path], storyboard: Storyboard, plan: ScenePlan
) -> list[dict]:
    expected = len(storyboard.beats)
    if len(frames) != expected:
        raise ValueError(
            f"frames count {len(frames)} must equal storyboard beats count {expected}"
        )
    mapping = ", ".join(
        f"{item.paper_term} = {item.visual}" for item in storyboard.mapping
    )
    content: list[dict] = [
        {
            "type": "text",
            "text": (
                f"Title: {storyboard.title}\n"
                f"Simple definition: {storyboard.simple_definition}\n"
                f"Visual focus: {storyboard.visual_focus}\n"
                f"Required paper components: {mapping}\n"
                f"Grounded scene plan: {plan.model_dump_json()}"
            ),
        }
    ]
    for index, (frame, beat) in enumerate(zip(frames, storyboard.beats, strict=True), start=1):
        content.append(
            {
                "type": "text",
                "text": (
                    f"Beat {index}. Narration: {beat.narration}\n"
                    f"Visual: {beat.visual}\n"
                    f"Allowed on-screen text: {json.dumps(beat.on_screen_text)}"
                ),
            }
        )
        content.append(_image_part(Path(frame)))
    content.append({"type": "text", "text": "Judge every beat above. Fenced json only."})
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": content}]


def _completeness_error(judgement: Judgement, expected: int) -> str:
    numbers = [beat.beat for beat in judgement.beats]
    if len(numbers) != expected:
        return f"expected {expected} beat judgements, got {len(numbers)}"
    duplicates = sorted({number for number in numbers if numbers.count(number) > 1})
    if duplicates:
        return f"duplicate beat judgements: {duplicates}"
    expected_numbers = set(range(1, expected + 1))
    missing = sorted(expected_numbers - set(numbers))
    extra = sorted(set(numbers) - expected_numbers)
    if missing or extra:
        return f"misnumbered beat judgements, missing {missing}, extra {extra}"
    return ""


def judge_frames(
    frames: list[Path],
    storyboard: Storyboard,
    plan: ScenePlan,
    complete: Callable[[str, list[dict]], str],
    model: str,
) -> Judgement:
    messages = build_judge_messages(frames, storyboard, plan)
    error = ""
    expected = len(storyboard.beats)
    for _attempt in range(2):
        try:
            raw = complete(model, messages)
        except Exception as exc:  # noqa: BLE001 - a judge outage must never cost a video
            error = f"{type(exc).__name__}: {exc}"
            continue
        try:
            judgement, error = parse_judgement(raw)
        except Exception as exc:  # noqa: BLE001 - malformed judge output must be recoverable
            judgement = None
            error = f"{type(exc).__name__}: {exc}"
        if judgement is not None:
            completeness_error = _completeness_error(judgement, expected)
            if not completeness_error:
                return judgement
            error = completeness_error
    logger.warning("judge unavailable: %s", error)
    return Judgement(
        beats=[], score=FALLBACK_SCORE, verdict="fix", note=f"judge unavailable: {error}"
    )


def judgement_feedback(judgement: Judgement) -> str:
    lines = []
    for beat in judgement.beats:
        problems = []
        if not beat.clean:
            problems.append("overlap or cut off")
        if not beat.legible:
            problems.append("text not legible")
        if not beat.shows_visual:
            problems.append("does not show the described visual")
        if not beat.grounded:
            problems.append("claim is not grounded in the scene plan")
        if problems:
            detail = f" ({beat.issue})" if beat.issue else ""
            lines.append(f"beat {beat.beat}: {', '.join(problems)}{detail}")
    if not lines:
        return "The frame judge found no specific problems."
    return "The frame judge flagged these beats; fix only these:\n- " + "\n- ".join(lines)
