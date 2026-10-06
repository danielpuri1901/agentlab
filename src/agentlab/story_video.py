"""Compose a storyboard, generated scene, narration, and final video.

Every external step is a module-level name so tests and the worker can
replace the slow or external boundary without replacing the compose loop.
Failures become StoryFailed because the worker owns the fallback.

There is no judge. The first file that passes the code guard, renders, and
writes structurally valid timing ships. Only those deterministic failures
cost another attempt. The scene is written whole once. After a failure the
coder gets the current file and the exact error and answers with
search/replace edits, so a fix costs a few lines instead of a new file. A
whole new file is written only when there is no file yet or the edits do
not apply exactly. A beat that runs longer than its narration is not a
failure: concat_audio pads each narration clip to its beat's length.
"""

import json
import logging
import math
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agentlab import story_scene
from agentlab.scene_code import (
    SCENE_CODE_SYSTEM,
    STORY_SCENE_API,
    check_scene_code,
    edit_scene_code,
    visual_direction,
    write_scene_code,
)
from agentlab.scene_plan import ScenePlan
from agentlab.stage_checkpoints import NoCheckpoints, fingerprint
from agentlab.story_scene import SCENE_CLASS, TIMING_ENV, beat_lengths
from agentlab.storyboard import (
    STORYBOARD_SYSTEM,
    Storyboard,
    StoryboardInvalid,
    design_storyboard,
)
from agentlab.video_render import (
    build_srt,
    concat_audio,
    mux_final,
    narrate,
    render_scene_video,
)

STORY_SCENE_FILE = Path(story_scene.__file__)
RENDER_QUALITY = "m"
RENDER_TIMEOUT = 600
MODEL_CALL_TIMEOUT = 600
VIDEO_DEADLINE_SECONDS = 2400
MAX_ATTEMPTS = 6
"""The first file plus up to five fix rounds."""
OUTPUT_SATURATION = 1.5
"""3Blue1Brown's render saturation (3b1b/videos custom_config.yml)."""
LAYOUT_ROUND_MIN_SECONDS = MODEL_CALL_TIMEOUT + RENDER_TIMEOUT + 60
"""Time one layout round may need: an edit call, a render, and the mux."""
STDERR_TAIL_LINES = 40
TIMING_TOLERANCE = 0.001

logger = logging.getLogger(__name__)


@dataclass
class _Rendered:
    video: Path
    timing: dict
    source: str
    attempt: int


class StoryFailed(Exception):
    """The story path produced nothing shippable."""


class _TimingInvalid(ValueError):
    """A render did not produce timing that can safely drive its audio."""


class _DeadlineExceeded(TimeoutError):
    """A video used its total work budget."""


@dataclass
class StoryResult:
    video_path: Path
    srt_path: Path
    storyboard: Storyboard
    scene_source: str
    visual_direction: str
    attempts: int
    timing: dict
    selected_attempt: int
    attempt_records: list[dict]


def _stderr_tail(exc: subprocess.CalledProcessError) -> str:
    text = exc.stderr or exc.stdout or ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    lines = text.strip().splitlines()
    tail = lines[-STDERR_TAIL_LINES:]
    # Rich prints the scene's own frame far above the tail. Without it the fix
    # rounds saw only Manim internals and repeated one bug six times
    # (2026-10-06), so the scene's frames and their marked lines lead.
    frames = []
    for i, line in enumerate(lines):
        if "paper_story.py:" in line:
            frames.append(line)
            frames += [m for m in lines[i + 1 : i + 8] if "\u2771" in m][:1]
    frames = [f.strip(" \u2502") for f in frames if f not in tail]
    return "\n".join(frames + tail)


def _failure_detail(exc: Exception) -> str:
    if isinstance(exc, subprocess.CalledProcessError):
        return _stderr_tail(exc) or str(exc)
    return f"{type(exc).__name__}: {exc}"


def _write_attempt(scene_dir: Path, attempt: int, source: str) -> Path:
    attempt_dir = scene_dir / f"attempt_{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(STORY_SCENE_FILE, attempt_dir / "story_scene.py")
    scene_file = attempt_dir / "paper_story.py"
    scene_file.write_text(source, encoding="utf-8")
    return scene_file


def _saved_scene(data: dict) -> tuple[str, str | None]:
    source, feedback = data["source"], data["feedback"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("saved scene has no source")
    if feedback is not None and not isinstance(feedback, str):
        raise TypeError("saved scene feedback is not text")
    return source, feedback


def _remaining_timeout(started: float, deadline_seconds: int, limit: int) -> int:
    remaining = deadline_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise _DeadlineExceeded(f"video deadline of {deadline_seconds} s reached")
    return max(1, min(limit, int(remaining)))


def _deadline_bound_completion(complete, started: float, deadline_seconds: int):
    def bounded(model: str, messages: list[dict], **kwargs) -> str:
        timeout = _remaining_timeout(started, deadline_seconds, MODEL_CALL_TIMEOUT)
        return complete(model, messages, timeout=timeout, **kwargs)

    return bounded


def _render(scene_file: Path, spec: dict, timeout: int) -> tuple[Path, dict]:
    timing_path = scene_file.parent / "beat_times.json"
    video = render_scene_video(
        scene_file,
        SCENE_CLASS,
        spec,
        scene_file.parent / "media",
        quality=RENDER_QUALITY,
        timeout_seconds=timeout,
        extra_env={TIMING_ENV: str(timing_path)},
    )
    try:
        timing = json.loads(timing_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise _TimingInvalid(f"timing output could not be read: {exc}") from exc
    return video, timing


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=0.0, abs_tol=TIMING_TOLERANCE)


def _timing_error(timing: dict, durations: list[float]) -> str | None:
    if not isinstance(timing, dict):
        return "timing output must be an object"
    beats = timing.get("beats")
    if not isinstance(beats, list):
        return "timing beats must be a list"
    if len(beats) != len(durations):
        return f"timing has {len(beats)} beats, expected {len(durations)}"

    previous_end = 0.0
    for expected_beat, (beat, expected_narration) in enumerate(
        zip(beats, durations, strict=True), start=1
    ):
        if not isinstance(beat, dict):
            return f"timing beat {expected_beat} must be an object"
        if type(beat.get("beat")) is not int or beat["beat"] != expected_beat:
            return f"timing beat ids must be sequential from 1, got {beat.get('beat')}"

        values = {
            name: _number(beat.get(name))
            for name in ("start", "end", "narration", "overrun")
        }
        invalid = [name for name, value in values.items() if value is None]
        if invalid:
            return f"timing beat {expected_beat} has invalid {invalid[0]}"
        start = values["start"]
        end = values["end"]
        narration = values["narration"]
        overrun = values["overrun"]
        if min(start, end, narration, overrun) < 0:
            return f"timing beat {expected_beat} values must be nonnegative"
        if not _close(start, previous_end):
            return f"timing beat {expected_beat} does not start at the previous end"
        if end <= start:
            return f"timing beat {expected_beat} must have positive length"
        if not _close(narration, float(expected_narration)):
            return f"timing beat {expected_beat} narration does not match its clip"
        if end - start + TIMING_TOLERANCE < narration:
            return f"timing beat {expected_beat} ends before its narration"
        expected_overrun = max(0.0, round((end - start) - narration, 3))
        if not _close(overrun, expected_overrun):
            return (
                f"timing beat {expected_beat} overrun is inconsistent with its length"
            )
        previous_end = end

    total = _number(timing.get("total"))
    if total is None or total < 0:
        return "timing total must be finite and nonnegative"
    if not _close(total, previous_end):
        return "timing total does not match the end of the final beat"
    return None


def compose_story_video(
    digest: str,
    plan: ScenePlan,
    polly_client,
    voice_id: str,
    complete,
    work_dir,
    out_path,
    story_model: str,
    scene_model: str,
    max_attempts: int = MAX_ATTEMPTS,
    deadline_seconds: int = VIDEO_DEADLINE_SECONDS,
    recent_visual_directions: list[str] | None = None,
    checkpoints=None,
    plan_fingerprint: str = "",
    subject: str = "paper",
) -> StoryResult:
    """checkpoints is a StageCheckpoints for the paper, or None for no reuse.
    plan_fingerprint names the deep read that produced digest and plan, so a
    saved storyboard is only reused for the same deep read."""
    try:
        return _compose(
            digest,
            plan,
            polly_client,
            voice_id,
            complete,
            Path(work_dir),
            Path(out_path),
            story_model,
            scene_model,
            max_attempts,
            deadline_seconds,
            recent_visual_directions,
            checkpoints or NoCheckpoints(),
            plan_fingerprint,
            subject,
        )
    except StoryFailed:
        raise
    except Exception as exc:
        raise StoryFailed(f"{type(exc).__name__}: {str(exc)[:300]}") from exc


def _compose(
    digest,
    plan,
    polly_client,
    voice_id,
    complete,
    work_dir: Path,
    out_path: Path,
    story_model,
    scene_model,
    max_attempts,
    deadline_seconds,
    recent_visual_directions,
    checkpoints,
    plan_fingerprint,
    subject="paper",
) -> StoryResult:
    started = time.monotonic()
    bounded_complete = _deadline_bound_completion(complete, started, deadline_seconds)
    work_dir.mkdir(parents=True, exist_ok=True)
    # The prompts are part of each fingerprint, so a prompt change never
    # reuses a storyboard or scene saved under the old prompt.
    board_fingerprint = fingerprint(
        "storyboard", story_model, plan_fingerprint, STORYBOARD_SYSTEM
    )
    storyboard = checkpoints.load_parsed(
        "storyboard", board_fingerprint, Storyboard.model_validate
    )
    if storyboard is None:
        try:
            storyboard = design_storyboard(
                digest,
                plan,
                bounded_complete,
                model=story_model,
                recent_visual_directions=recent_visual_directions,
                subject=subject,
            )
        except StoryboardInvalid as exc:
            raise StoryFailed(f"storyboard: {exc}") from exc
        checkpoints.save(
            "storyboard", board_fingerprint, storyboard.model_dump(mode="json")
        )

    narrations = [beat.narration for beat in storyboard.beats]
    clips = narrate(polly_client, narrations, voice_id, work_dir / "narration")
    durations = [clip.seconds for clip in clips]
    spec = {
        "storyboard": storyboard.model_dump(),
        "durations": durations,
        "subtitles": narrations,
    }
    scene_dir = work_dir / "scene"

    # The scene stage saves its file before each render and its error after
    # each failure. A retry resumes there: a file with no known error renders
    # again without a model call (the last run died mid-render), and a file
    # with an error goes straight to an edit.
    scene_fingerprint = fingerprint(
        "scene", scene_model, board_fingerprint, SCENE_CODE_SYSTEM, STORY_SCENE_API
    )

    def remember(feedback_text: str | None) -> None:
        checkpoints.save(
            "scene", scene_fingerprint, {"source": source, "feedback": feedback_text}
        )

    failures: list[str] = []
    attempt_records: list[dict] = []

    def ship(chosen: _Rendered, attempts_made: int) -> StoryResult:
        lengths = beat_lengths(chosen.timing)
        audio = concat_audio(
            clips,
            work_dir / "narration.mp3",
            target_seconds=lengths,
            timeout_seconds=_remaining_timeout(
                started, deadline_seconds, RENDER_TIMEOUT
            ),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path = out_path.with_suffix(".srt")
        srt_path.write_text(build_srt(clips, durations=lengths), encoding="utf-8")
        video_path = mux_final(
            chosen.video,
            audio,
            out_path,
            timeout_seconds=_remaining_timeout(
                started, deadline_seconds, RENDER_TIMEOUT
            ),
            saturation=OUTPUT_SATURATION,
        )
        return StoryResult(
            video_path=Path(video_path),
            srt_path=srt_path,
            storyboard=storyboard,
            scene_source=chosen.source,
            visual_direction=visual_direction(chosen.source),
            attempts=attempts_made,
            timing=chosen.timing,
            selected_attempt=chosen.attempt,
            attempt_records=attempt_records,
        )

    # A render with only layout problems gets one edit round. It stays as
    # the fallback, so that round can never cost the day's video.
    fallback: _Rendered | None = None
    feedback: str | None = None
    source: str | None = None
    saved_scene = checkpoints.load_parsed("scene", scene_fingerprint, _saved_scene)
    if saved_scene is not None:
        source, feedback = saved_scene
    resume_render = saved_scene is not None and feedback is None
    attempt_limit = min(max_attempts, MAX_ATTEMPTS)
    for attempt in range(1, attempt_limit + 1):
        if fallback is not None and attempt > fallback.attempt + 1:
            # The one layout round did not ship; the first render does.
            return ship(fallback, attempt - 1)
        try:
            _remaining_timeout(started, deadline_seconds, 1)
        except _DeadlineExceeded as exc:
            if fallback is not None:
                return ship(fallback, attempt)
            raise StoryFailed(str(exc)) from exc
        mode = "generate" if source is None else "edit"
        try:
            new_source = None
            if resume_render:
                new_source, mode, resume_render = source, "resume", False
                logger.info("scene: rendering the saved file without a model call")
            elif source is not None:
                new_source = edit_scene_code(
                    storyboard,
                    durations,
                    source,
                    feedback,
                    bounded_complete,
                    model=scene_model,
                )
            if new_source is None:
                if source is not None:
                    mode = "rewrite"
                new_source = write_scene_code(
                    storyboard,
                    durations,
                    bounded_complete,
                    model=scene_model,
                    feedback=feedback,
                    previous_source=source,
                )
        except Exception as exc:  # noqa: BLE001 - the next round retries
            detail = _failure_detail(exc)
            logger.warning("scene coder failed on attempt %d: %s", attempt, detail)
            failures.append(f"attempt {attempt}: coder error")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "mode": mode,
                    "status": "coder_error",
                    "failure": detail,
                }
            )
            # With a file in hand, the next round still fixes its last failure.
            if source is None:
                feedback = f"The previous scene coder call failed: {detail}"
            continue

        source = new_source
        scene_file = _write_attempt(scene_dir, attempt, source)
        remember(None)
        record = {"attempt": attempt, "mode": mode, "source_path": str(scene_file)}
        findings = check_scene_code(source, len(storyboard.beats))
        if findings:
            feedback = "The guard rejected the file:\n- " + "\n- ".join(findings)
            remember(feedback)
            failures.append(f"attempt {attempt}: guard: {findings[0]}")
            attempt_records.append(
                {**record, "status": "guard_rejected", "failure": feedback}
            )
            continue

        try:
            render_timeout = _remaining_timeout(
                started, deadline_seconds, RENDER_TIMEOUT
            )
            video, timing = _render(scene_file, spec, render_timeout)
        except _DeadlineExceeded as exc:
            if fallback is not None:
                return ship(fallback, attempt)
            raise StoryFailed(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            detail = _stderr_tail(exc)
            feedback = "Manim failed with this traceback:\n" + detail
            remember(feedback)
            logger.warning("render failed on attempt %d:\n%s", attempt, detail)
            failures.append(f"attempt {attempt}: render error")
            attempt_records.append(
                {**record, "status": "render_error", "failure": detail}
            )
            continue
        except subprocess.TimeoutExpired:
            feedback = (
                f"The render did not finish within {render_timeout} s. The scene "
                "is too heavy: use fewer or coarser mobjects, lighter updaters, "
                "and shorter run_times."
            )
            remember(feedback)
            logger.warning("render timed out on attempt %d", attempt)
            failures.append(f"attempt {attempt}: render timeout")
            attempt_records.append(
                {**record, "status": "render_timeout", "failure": feedback}
            )
            continue
        except _TimingInvalid as exc:
            timing, error = None, str(exc)
        else:
            error = _timing_error(timing, durations)
        if error:
            feedback = (
                f"The render timing is invalid: {error}. Fix the scene timing output."
            )
            remember(feedback)
            logger.warning("render timing failed on attempt %d: %s", attempt, error)
            failures.append(f"attempt {attempt}: invalid timing")
            attempt_records.append(
                {
                    **record,
                    "status": "invalid_timing",
                    "failure": feedback,
                    "timing": timing,
                }
            )
            continue

        rendered = _Rendered(video, timing, source, attempt)
        warnings = timing.get("layout_warnings") or []
        if warnings and fallback is not None:
            attempt_records.append(
                {**record, "status": "layout_still_wrong", "timing": timing}
            )
            return ship(fallback, attempt)
        if (
            warnings
            and attempt < attempt_limit
            and deadline_seconds - (time.monotonic() - started)
            >= LAYOUT_ROUND_MIN_SECONDS
        ):
            fallback = rendered
            feedback = (
                "The video rendered, but the layout has problems at the end of "
                "these beats:\n- "
                + "\n- ".join(warnings)
                + "\nMove or shrink the named objects so no text overlaps other "
                "text and nothing crosses the frame margin. Keep everything else "
                "the same."
            )
            remember(feedback)
            attempt_records.append(
                {**record, "status": "layout_round", "failure": feedback, "timing": timing}
            )
            continue
        attempt_records.append({**record, "status": "shipped", "timing": timing})
        return ship(rendered, attempt)

    if fallback is not None:
        return ship(fallback, attempt_limit)
    raise StoryFailed(
        f"no renderable scene in {attempt_limit} attempts: " + "; ".join(failures)
    )
