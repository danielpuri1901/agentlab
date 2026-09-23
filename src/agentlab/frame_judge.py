"""Judge sampled story frames against their storyboard."""

import base64
import json
import logging
import math
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agentlab import video_render
from agentlab.scene_plan import ScenePlan, extract_json_object
from agentlab.storyboard import Storyboard

FALLBACK_SCORE = 5
FRAME_WIDTH = 480
FRAMES_PER_BEAT = 3
FRAME_SAMPLE_TIMEOUT_SECONDS = 15
MAX_MISSING_VISUALS = 0
MIN_PASS_SCORE = 7
MIN_SHIP_SCORE = 5
"""The floor for shipping a video the judge did not pass outright.

A defect is either substantive (the frame is not grounded in the storyboard,
or the whole video is weak) or cosmetic (overlap, a cut-off label, a beat
that does not show its visual). Only substantive defects are worth paying
another scene-coder call and another render for: between 2026-08-25 and
2026-09-23 the retry loop tripled the median video cost, from 0.49 to 1.56
USD, and still ended in a discarded run whenever nothing reached a clean
pass. Cosmetic defects ship."""

logger = logging.getLogger(__name__)


def _remaining_subprocess_timeout(deadline: float, command: list[str]) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    return remaining


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


def blocking_defect(judgement: "Judgement") -> str | None:
    """Why this candidate must not ship, or None if it is shippable as it is.

    Grounding is the one visual property that cannot be waved through: a beat
    that does not match the storyboard shows Daniel something the source never
    said, the same failure the scene-plan number guard exists to stop.
    """
    if any(not beat.grounded for beat in judgement.beats):
        return "a beat is not grounded in the storyboard"
    if judgement.score < MIN_SHIP_SCORE:
        return f"score {judgement.score}/10 is below the ship floor"
    return None


def cosmetic_only(judgement: "Judgement") -> bool:
    """True when the judge asked for a fix but every defect is cosmetic.

    The video is already at pass-level quality overall, so another attempt
    buys a tidier frame at the price of a full coder call plus a render.
    """
    return (
        judgement.verdict != "pass"
        and blocking_defect(judgement) is None
        and judgement.score >= MIN_PASS_SCORE
    )


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


def sample_frames(
    video_path,
    times: list[float],
    out_dir,
    timeout_seconds: int = FRAME_SAMPLE_TIMEOUT_SECONDS,
) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    deadline = time.monotonic() + timeout_seconds
    for index, sample_time in enumerate(times, start=1):
        output = out_dir / f"frame_{index:02d}.png"
        command = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{sample_time:.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale={FRAME_WIDTH}:-1",
            str(output),
        ]
        video_render.run_subprocess(
            command,
            timeout=_remaining_subprocess_timeout(deadline, command),
        )
        frames.append(output)
    return frames


def contact_sheet_frames(
    frames: list[Path],
    out_dir,
    timeout_seconds: int = FRAME_SAMPLE_TIMEOUT_SECONDS,
) -> list[Path]:
    if len(frames) % FRAMES_PER_BEAT:
        raise ValueError(
            f"frames count {len(frames)} must be divisible by {FRAMES_PER_BEAT}"
        )
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    sheets = []
    deadline = time.monotonic() + timeout_seconds
    for start in range(0, len(frames), FRAMES_PER_BEAT):
        group = frames[start : start + FRAMES_PER_BEAT]
        output = out_dir / f"beat-{start // FRAMES_PER_BEAT + 1:02d}.png"
        command = ["ffmpeg", "-y", "-v", "error"]
        for frame in group:
            command.extend(["-i", str(frame)])
        command.extend(
            [
                "-filter_complex",
                f"hstack=inputs={FRAMES_PER_BEAT}",
                str(output),
            ]
        )
        video_render.run_subprocess(
            command,
            timeout=_remaining_subprocess_timeout(deadline, command),
        )
        sheets.append(output)
    return sheets


JUDGE_SYSTEM = """You judge sampled frames from a short research-paper video. Each beat \
has one contact sheet: start on the left, middle in the center, and end on the right. \
Each panel is sampled at phone width. The video must explain the real mechanism \
and stay within the grounded scene plan.

For each beat decide:
- grounded: the narration and visual meaning stay within the scene plan.
- shows_visual: the frames make the narrated idea easier to understand through meaningful \
visual change. An abstract visual is valid when its mapping is clear. It does not need to \
copy the storyboard description literally.
- legible: every necessary text item is readable at phone width.
- clean: nothing collides, gets cut off, or covers the caption band.

Put a short, concrete issue when something is wrong, else null. Judge the progression \
across all three frames, not only each still image. Then give one overall score from 0 to 10:
- 9 to 10: clear, visually compelling, paper-specific, and memorable.
- 7 to 8: clear and coherent, with meaningful motion and an intentional composition.
- 0 to 6: confusing, static, generic, repetitive, or visually weak.

Beat 1 must show the title. The mechanism beats must reveal cause and effect. Reject an \
unrelated metaphor, an unexplained technical term, or a generic layout that could fit any \
paper without changing its behavior. Do not reject useful abstraction. When the score is \
below 7, include a note that states how to improve the visual concept. Be strict about \
grounding, readability, clipping, and overlap. Answer with ONLY a fenced json \
object: {"beats": [{"beat": 1, "grounded": true, "shows_visual": true, "legible": true, "clean": true, \
"issue": null}, ...], "score": 7, "note": null}. The score is a top-level sibling after the closed \
beats array, never an item inside beats."""


def _image_part(path: Path) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{encoded}"},
    }


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
    for index, (frame, beat) in enumerate(
        zip(frames, storyboard.beats, strict=True), start=1
    ):
        content.append(
            {
                "type": "text",
                "text": (
                    f"Beat {index}. Narration: {beat.narration}\n"
                    f"Visual: {beat.visual}\n"
                    f"Allowed on-screen text: {json.dumps(beat.on_screen_text)}\n"
                    "The left panel is the start, the middle panel is the middle, "
                    "and the right panel is the end."
                ),
            }
        )
        content.append(_image_part(Path(frame)))
    content.append(
        {"type": "text", "text": "Judge every beat above. Fenced json only."}
    )
    return [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": content},
    ]


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
        beats=[],
        score=FALLBACK_SCORE,
        verdict="fix",
        note=f"judge unavailable: {error}",
    )


def judgement_feedback(judgement: Judgement) -> str:
    if not judgement.beats:
        return (
            "The frame judge was unavailable. Return the same scene file unchanged.\n- "
            + (judgement.note or "no judge detail")
        )
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
        detail = judgement.note or f"overall visual score was {judgement.score}/10"
        return (
            "Create a fresh visual concept instead of patching the prior layout.\n- "
            + detail
        )
    if judgement.note:
        lines.append(f"overall: {judgement.note}")
    return "The frame judge flagged these problems:\n- " + "\n- ".join(lines)
