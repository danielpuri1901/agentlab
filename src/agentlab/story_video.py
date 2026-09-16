"""Compose a storyboard, generated scene, narration, and final video.

Every external step is a module-level name so tests and the worker can
replace the slow or external boundary without replacing the compose loop.
Failures become StoryFailed because the worker owns the template fallback.
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
from agentlab.frame_judge import (
    FRAME_SAMPLE_TIMEOUT_SECONDS,
    contact_sheet_frames,
    judge_frames,
    judgement_feedback,
    sample_frames,
)
from agentlab.scene_code import check_scene_code, write_scene_code
from agentlab.scene_plan import ScenePlan
from agentlab.story_scene import (
    SCENE_CLASS,
    TIMING_ENV,
    beat_lengths,
    overrun_report,
)
from agentlab.storyboard import Storyboard, StoryboardInvalid, design_storyboard
from agentlab.video_render import (
    build_srt,
    concat_audio,
    mux_final,
    narrate,
    render_scene_video,
)

STORY_SCENE_FILE = Path(story_scene.__file__)
LOW_RENDER_TIMEOUT = 180
FINAL_RENDER_TIMEOUT = 300
VIDEO_DEADLINE_SECONDS = 900
MAX_ATTEMPTS = 4
STDERR_TAIL_LINES = 40
TIMING_TOLERANCE = 0.001
OVERRUN_LIMIT_TOLERANCE = 1e-9

logger = logging.getLogger(__name__)


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
    attempts: int
    judge_score: int | None
    judgement: dict | None
    timing: dict
    selected_attempt: int
    passed: bool
    attempt_records: list[dict]


@dataclass
class _Candidate:
    attempt: int
    source: str
    scene_file: Path
    video: Path
    timing: dict
    judgement: object
    frames: list[Path]


def _stderr_tail(exc: subprocess.CalledProcessError) -> str:
    text = exc.stderr or exc.stdout or ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return "\n".join(text.strip().splitlines()[-STDERR_TAIL_LINES:])


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


def beat_sample_times(timing: dict) -> list[float]:
    times = []
    for beat in timing["beats"]:
        start = float(beat["start"])
        length = float(beat["end"]) - start
        times.extend(start + length * fraction for fraction in (0.2, 0.5, 0.8))
    return times


def _remaining_timeout(started: float, deadline_seconds: int, limit: int) -> int:
    remaining = deadline_seconds - (time.monotonic() - started)
    if remaining <= 0:
        raise _DeadlineExceeded(f"video deadline of {deadline_seconds} s reached")
    return max(1, min(limit, int(remaining)))


def _render(
    scene_file: Path, spec: dict, quality: str, timeout: int
) -> tuple[Path, dict]:
    timing_path = scene_file.parent / f"beat_times_{quality}.json"
    video = render_scene_video(
        scene_file,
        SCENE_CLASS,
        spec,
        scene_file.parent / f"media_{quality}",
        quality=quality,
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


def _timing_feedback(timing: dict, durations: list[float]) -> str | None:
    error = _timing_error(timing, durations)
    if error:
        return f"The render timing is invalid: {error}. Fix the scene timing output."
    raw_timing = {
        "beats": [
            {
                **beat,
                "narration": expected_narration,
                "overrun": max(
                    0.0,
                    (beat["end"] - beat["start"]) - expected_narration,
                ),
            }
            for beat, expected_narration in zip(timing["beats"], durations, strict=True)
        ]
    }
    return overrun_report(
        raw_timing,
        per_beat_limit=(story_scene.PER_BEAT_OVERRUN_LIMIT + OVERRUN_LIMIT_TOLERANCE),
        total_limit=story_scene.TOTAL_OVERRUN_LIMIT + OVERRUN_LIMIT_TOLERANCE,
    )


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
    judge_model: str,
    max_attempts: int = MAX_ATTEMPTS,
    deadline_seconds: int = VIDEO_DEADLINE_SECONDS,
    recent_visual_directions: list[str] | None = None,
) -> StoryResult:
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
            judge_model,
            max_attempts,
            deadline_seconds,
            recent_visual_directions,
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
    judge_model,
    max_attempts,
    deadline_seconds,
    recent_visual_directions,
) -> StoryResult:
    started = time.monotonic()
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        storyboard = design_storyboard(
            digest,
            plan,
            complete,
            model=story_model,
            recent_visual_directions=recent_visual_directions,
        )
    except StoryboardInvalid as exc:
        raise StoryFailed(f"storyboard: {exc}") from exc

    captions = [beat.narration for beat in storyboard.beats]
    clips = narrate(polly_client, captions, voice_id, work_dir / "narration")
    durations = [clip.seconds for clip in clips]
    spec = {
        "storyboard": storyboard.model_dump(),
        "durations": durations,
        "captions": captions,
    }
    scene_dir = work_dir / "scene"

    candidates: list[_Candidate] = []
    failures: list[str] = []
    attempt_records: list[dict] = []
    feedback: str | None = None
    previous: str | None = None
    attempts = 0
    attempt_limit = min(max_attempts, MAX_ATTEMPTS)
    for attempt in range(1, attempt_limit + 1):
        attempts = attempt
        try:
            _remaining_timeout(started, deadline_seconds, 1)
        except _DeadlineExceeded as exc:
            failures.append(f"attempt {attempt}: deadline")
            if candidates:
                break
            raise StoryFailed(str(exc)) from exc
        try:
            source = write_scene_code(
                storyboard,
                durations,
                complete,
                model=scene_model,
                feedback=feedback,
                previous_source=previous,
            )
        except Exception as exc:  # noqa: BLE001 - an earlier candidate can still ship
            detail = _failure_detail(exc)
            logger.warning("scene coder failed on attempt %d: %s", attempt, detail)
            failures.append(f"attempt {attempt}: coder error")
            attempt_records.append(
                {"attempt": attempt, "status": "coder_error", "failure": detail}
            )
            feedback = f"The previous scene coder call failed: {detail}"
            continue

        previous = source
        scene_file = _write_attempt(scene_dir, attempt, source)
        findings = check_scene_code(source, len(storyboard.beats))
        if findings:
            feedback = "The guard rejected the file:\n- " + "\n- ".join(findings)
            failures.append(f"attempt {attempt}: guard: {findings[0]}")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "guard_rejected",
                    "failure": feedback,
                    "source_path": str(scene_file),
                }
            )
            continue

        try:
            preview_timeout = _remaining_timeout(
                started, deadline_seconds, LOW_RENDER_TIMEOUT
            )
            video, timing = _render(scene_file, spec, "l", preview_timeout)
        except _DeadlineExceeded as exc:
            failures.append(f"attempt {attempt}: deadline")
            if candidates:
                break
            raise StoryFailed(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            detail = _stderr_tail(exc)
            feedback = "Manim failed with this traceback:\n" + detail
            logger.warning("preview render failed on attempt %d:\n%s", attempt, detail)
            failures.append(f"attempt {attempt}: render error")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "render_error",
                    "failure": detail,
                    "source_path": str(scene_file),
                }
            )
            continue
        except subprocess.TimeoutExpired:
            feedback = (
                f"The render did not finish within {preview_timeout} s. "
                "The scene is too heavy: use fewer mobjects, no per-frame "
                "updaters except self.counter, shorter run_times."
            )
            logger.warning("preview render timed out on attempt %d", attempt)
            failures.append(f"attempt {attempt}: render timeout")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "render_timeout",
                    "failure": feedback,
                    "source_path": str(scene_file),
                }
            )
            continue
        except _TimingInvalid as exc:
            feedback = (
                f"The render timing is invalid: {exc}. Fix the scene timing output."
            )
            logger.warning("preview timing failed on attempt %d: %s", attempt, exc)
            failures.append(f"attempt {attempt}: invalid timing")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "invalid_timing",
                    "failure": feedback,
                    "source_path": str(scene_file),
                }
            )
            continue

        feedback = _timing_feedback(timing, durations)
        if feedback:
            logger.warning("preview timing failed on attempt %d: %s", attempt, feedback)
            failures.append(f"attempt {attempt}: timing")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "timing_rejected",
                    "failure": feedback,
                    "source_path": str(scene_file),
                    "timing": timing,
                }
            )
            continue

        try:
            frame_timeout = _remaining_timeout(
                started, deadline_seconds, FRAME_SAMPLE_TIMEOUT_SECONDS
            )
            frames = sample_frames(
                video,
                beat_sample_times(timing),
                scene_file.parent / "frames",
                timeout_seconds=frame_timeout,
            )
            contact_sheets = contact_sheet_frames(
                frames,
                scene_file.parent / "contact-sheets",
                timeout_seconds=frame_timeout,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = _failure_detail(exc)
            feedback = f"Frame extraction failed: {detail}"
            failures.append(f"attempt {attempt}: frame extraction")
            attempt_records.append(
                {
                    "attempt": attempt,
                    "status": "frame_error",
                    "failure": detail,
                    "source_path": str(scene_file),
                }
            )
            continue
        except _DeadlineExceeded as exc:
            failures.append(f"attempt {attempt}: deadline")
            if candidates:
                break
            raise StoryFailed(str(exc)) from exc
        judgement = judge_frames(
            contact_sheets, storyboard, plan, complete, model=judge_model
        )
        candidates.append(
            _Candidate(attempt, source, scene_file, video, timing, judgement, frames)
        )
        attempt_records.append(
            {
                "attempt": attempt,
                "status": "passed" if judgement.verdict == "pass" else "judge_fix",
                "source_path": str(scene_file),
                "frame_paths": [str(frame) for frame in frames],
                "contact_sheet_paths": [str(sheet) for sheet in contact_sheets],
                "timing": timing,
                "judgement": judgement.model_dump(),
            }
        )
        if judgement.verdict == "pass":
            break
        feedback = judgement_feedback(judgement)
        has_technical_problem = any(
            not beat.grounded or not beat.legible or not beat.clean
            for beat in judgement.beats
        )
        if judgement.beats and not has_technical_problem:
            previous = None

    if not candidates:
        raise StoryFailed(
            f"no renderable scene in {attempts} attempts: " + "; ".join(failures)
        )

    passed = [
        candidate for candidate in candidates if candidate.judgement.verdict == "pass"
    ]
    pool = passed or candidates
    best = max(
        pool, key=lambda candidate: (candidate.judgement.score, candidate.attempt)
    )
    try:
        final_timeout = _remaining_timeout(
            started, deadline_seconds, FINAL_RENDER_TIMEOUT
        )
        final_video, timing = _render(best.scene_file, spec, "m", final_timeout)
        final_timing_error = _timing_feedback(timing, durations)
        if final_timing_error:
            raise _TimingInvalid(final_timing_error)
    except Exception as exc:  # noqa: BLE001 - the accepted preview is the fallback
        logger.warning(
            "final render failed, using accepted preview: %s",
            _failure_detail(exc),
        )
        final_video, timing = best.video, best.timing

    lengths = beat_lengths(timing)
    audio = concat_audio(
        clips,
        work_dir / "narration.mp3",
        target_seconds=lengths,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path = out_path.with_suffix(".srt")
    srt_path.write_text(build_srt(clips, durations=lengths), encoding="utf-8")
    video_path = mux_final(final_video, audio, out_path)
    return StoryResult(
        video_path=Path(video_path),
        srt_path=srt_path,
        storyboard=storyboard,
        scene_source=best.source,
        attempts=attempts,
        judge_score=best.judgement.score,
        judgement=best.judgement.model_dump(),
        timing=timing,
        selected_attempt=best.attempt,
        passed=best.judgement.verdict == "pass",
        attempt_records=attempt_records,
    )
