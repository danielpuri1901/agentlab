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

With chapter_lanes the same attempt loop runs once per chapter, in parallel
lanes, each with its own folder, checkpoint and attempt cap. A lane that
runs out of attempts or time ships a text card, so one chapter can no
longer fail the video. The chapter videos are joined once at the end
(docs/superpowers/specs/2026-10-23-chapter-lanes-design.md, sections 3 to 5).
"""

import json
import logging
import math
import shutil
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from agentlab import story_scene
from agentlab.scene_code import (
    SCENE_CODE_SYSTEM,
    STORY_SCENE_API,
    ChapterNote,
    check_scene_code,
    edit_scene_code,
    visual_direction,
    write_scene_code,
)
from agentlab.scene_plan import ScenePlan
from agentlab.stage_checkpoints import NoCheckpoints, fingerprint
from agentlab.stages import stage
from agentlab.story_scene import (
    SCENE_CLASS,
    TIMING_ENV,
    beat_lengths,
    card_scene_source,
)
from agentlab.storyboard import (
    STORYBOARD_SYSTEM,
    Storyboard,
    StoryboardInvalid,
    chapter_board,
    chapters,
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
LANE_ATTEMPTS = stage("scene").attempts
"""Attempts per chapter lane, from the scene stage file."""
LANE_WORKERS = 3
"""Lanes that run at once on the 4 vCPU explain task. Model calls wait on
the network and renders are subprocesses, so threads are enough."""
JOIN_TIMEOUT = 300
"""The join always gets at least this long: by then every chapter has a
render, and a clock overrun must not throw that work away."""
CARD_RENDER_TIMEOUT = 180
"""The cap on a card render. It applies even when the deadline has passed."""
CARD_RESERVE_SECONDS = 240
"""Time a lane leaves for its card: the model and render attempts stop this
long before the video deadline, so the card still renders inside it."""
CARD_FAILURE_CHARS = 300

logger = logging.getLogger(__name__)


@dataclass
class _Rendered:
    video: Path
    timing: dict
    source: str
    # The "attempt" of the record that made this render: an int on the
    # monolithic path, "c<k>-<n>" or "c<k>-card" in a chapter lane.
    attempt: int | str
    source_path: Path | None = None


@dataclass
class _Chapter:
    """One lane's input and, once _build_lane returns, its outcome. The
    monolithic path is one _Chapter over every beat, with no note."""

    index: int
    beats: list[int]
    board: Storyboard
    durations: list[float]
    note: ChapterNote | None
    rendered: _Rendered | None = None
    status: str = "pending"
    attempts: int = 0
    records: list[dict] = field(default_factory=list)


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
    selected_attempt: int | str
    attempt_records: list[dict]
    # One entry per chapter lane, in order: index, beats (0-based), status
    # (shipped, layout_fallback or card), attempts, source_path, video_path
    # and timing. Empty on the monolithic path.
    chapters: list[dict] = field(default_factory=list)


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


def _write_attempt(scene_dir: Path, attempt: int | str, source: str) -> Path:
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


def render_spec(board: Storyboard, durations: list[float]) -> dict:
    """The spec a scene file renders with: story_scene's base class reads the
    storyboard, the beat durations and the subtitles. The GEPA optimizer
    renders with the same spec, so the two cannot drift."""
    return {
        "storyboard": board.model_dump(),
        "durations": durations,
        "subtitles": [beat.narration for beat in board.beats],
    }


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
    chapter_lanes: bool = False,
) -> StoryResult:
    """checkpoints is a StageCheckpoints for the paper, or None for no reuse.
    plan_fingerprint names the deep read that produced digest and plan, so a
    saved storyboard is only reused for the same deep read. chapter_lanes
    builds each chapter of the storyboard in its own lane and joins them;
    off, one scene file covers every beat."""
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
            chapter_lanes,
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
    chapter_lanes=False,
) -> StoryResult:
    started = time.monotonic()
    bounded_complete = _deadline_bound_completion(complete, started, deadline_seconds)
    work_dir.mkdir(parents=True, exist_ok=True)
    # The prompts and the stage settings are part of each fingerprint, so a
    # prompt or settings change never reuses a storyboard or scene saved
    # under the old ones.
    board_fingerprint = fingerprint(
        "storyboard",
        story_model,
        plan_fingerprint,
        STORYBOARD_SYSTEM,
        stage("storyboard").fingerprint,
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
    build = partial(
        _build_lane,
        work_dir=work_dir,
        complete=complete,
        scene_model=scene_model,
        started=started,
        deadline_seconds=deadline_seconds,
        checkpoints=checkpoints,
        board_fingerprint=board_fingerprint,
    )
    if chapter_lanes:
        groups = chapters(storyboard)
        lanes = [
            _Chapter(
                index=k,
                beats=beats,
                board=chapter_board(storyboard, beats),
                durations=[durations[i] for i in beats],
                note=ChapterNote(
                    index=k,
                    count=len(groups),
                    first_beat=beats[0] + 1,
                    last_beat=beats[-1] + 1,
                    previous_visual=(
                        storyboard.beats[beats[0] - 1].visual if beats[0] else None
                    ),
                ),
            )
            for k, beats in enumerate(groups, start=1)
        ]
        # A lane changes only its own _Chapter, its own folder and its own
        # checkpoint key. The completion is shared; its usage sink only
        # appends to a list.
        lane = partial(
            build, attempts=min(max_attempts, LANE_ATTEMPTS), fallback_card=True
        )
        with ThreadPoolExecutor(max_workers=min(len(lanes), LANE_WORKERS)) as pool:
            lanes = list(pool.map(lane, lanes))
    else:
        whole = _Chapter(
            index=1,
            beats=list(range(len(durations))),
            board=storyboard,
            durations=durations,
            note=None,
        )
        attempts = min(max_attempts, MAX_ATTEMPTS)
        lanes = [build(whole, attempts=attempts, fallback_card=False)]
    return _ship(
        lanes,
        storyboard=storyboard,
        clips=clips,
        work_dir=work_dir,
        out_path=out_path,
        started=started,
        deadline_seconds=deadline_seconds,
        chapter_lanes=chapter_lanes,
    )


def _build_lane(
    chapter: _Chapter,
    *,
    work_dir: Path,
    complete,
    scene_model: str,
    attempts: int,
    started: float,
    deadline_seconds: int,
    checkpoints,
    board_fingerprint: str,
    fallback_card: bool,
) -> _Chapter:
    """Build one chapter's scene with the attempt loop and record the outcome
    on the chapter.

    fallback_card is True for a lane of a chaptered video. That lane works in
    its own folder under its own checkpoint, labels its attempts c<k>-<n>,
    stops its attempts CARD_RESERVE_SECONDS before the video deadline, and
    ships a text card when no model scene ships. Any other exception in the
    lane is a bug in our code, not a model failure, so it also gets the card.
    False is the monolithic path: one lane over every beat that raises
    StoryFailed as before.
    """
    k = chapter.index
    spec = render_spec(chapter.board, chapter.durations)
    if fallback_card:
        scene_dir = work_dir / f"chapter_{k}" / "scene"
        stage_name = f"scene-{k}"
        scene_fingerprint = fingerprint(
            "scene",
            scene_model,
            board_fingerprint,
            SCENE_CODE_SYSTEM,
            STORY_SCENE_API,
            str(k),
            str(len(chapter.beats)),
            stage("scene").fingerprint,
        )
        lane_deadline = deadline_seconds - CARD_RESERVE_SECONDS
    else:
        scene_dir, stage_name, lane_deadline = work_dir / "scene", "scene", deadline_seconds
        scene_fingerprint = fingerprint(
            "scene", scene_model, board_fingerprint, SCENE_CODE_SYSTEM, STORY_SCENE_API
        )
    try:
        chapter.rendered, chapter.attempts, chapter.status = _attempt_loop(
            chapter,
            spec=spec,
            scene_dir=scene_dir,
            stage_name=stage_name,
            scene_fingerprint=scene_fingerprint,
            complete=complete,
            scene_model=scene_model,
            attempt_limit=attempts,
            started=started,
            deadline_seconds=lane_deadline,
            checkpoints=checkpoints,
            labelled=fallback_card,
        )
        return chapter
    except Exception as exc:
        if not fallback_card:
            raise
        expected = isinstance(exc, StoryFailed)
        reason = str(exc) if expected else _failure_detail(exc)
        logger.warning(
            "chapter %d: no model scene, shipping its text card: %s",
            k,
            reason,
            exc_info=not expected,
        )

    # The card is never saved as the chapter's checkpoint: the lane's last
    # file and error stay saved, so a rerun asks the model again.
    try:
        source = card_scene_source(chapter.board, chapter.durations)
        scene_file = _write_attempt(scene_dir, "card", source)
        video, timing = _render(scene_file, spec, CARD_RENDER_TIMEOUT)
    except Exception as exc:
        detail = _failure_detail(exc)[-CARD_FAILURE_CHARS:]
        raise StoryFailed(f"chapter {k}: the card did not render: {detail}") from exc
    error = _timing_error(timing, chapter.durations)
    if error:
        raise StoryFailed(f"chapter {k}: the card did not render: {error}")
    chapter.attempts = len(chapter.records)
    chapter.records.append(
        {
            "attempt": f"c{k}-card",
            "chapter": k,
            "mode": "card",
            "source_path": str(scene_file),
            "status": "shipped",
            "failure": reason,
            "timing": timing,
        }
    )
    chapter.rendered = _Rendered(
        video, timing, source, f"c{k}-card", source_path=scene_file
    )
    chapter.status = "card"
    return chapter


def _attempt_loop(
    chapter: _Chapter,
    *,
    spec: dict,
    scene_dir: Path,
    stage_name: str,
    scene_fingerprint: str,
    complete,
    scene_model: str,
    attempt_limit: int,
    started: float,
    deadline_seconds: int,
    checkpoints,
    labelled: bool,
) -> tuple[_Rendered, int, str]:
    """The scene attempt loop for one chapter's beats. Returns the render
    that ships, the attempts it took, and the chapter status. Raises
    StoryFailed when no render ships. deadline_seconds is the lane's own
    deadline. Records go to chapter.records."""
    storyboard, durations, records = chapter.board, chapter.durations, chapter.records
    bounded_complete = _deadline_bound_completion(complete, started, deadline_seconds)

    def label(attempt: int) -> int | str:
        return f"c{chapter.index}-{attempt}" if labelled else attempt

    # The scene stage saves its file before each render and its error after
    # each failure. A retry resumes there: a file with no known error renders
    # again without a model call (the last run died mid-render), and a file
    # with an error goes straight to an edit.
    def remember(feedback_text: str | None) -> None:
        checkpoints.save(
            stage_name, scene_fingerprint, {"source": source, "feedback": feedback_text}
        )

    failures: list[str] = []

    # A render with only layout problems gets one edit round. It stays as
    # the fallback, so that round can never cost the day's video.
    fallback: _Rendered | None = None
    fallback_attempt = 0
    feedback: str | None = None
    source: str | None = None
    saved_scene = checkpoints.load_parsed(stage_name, scene_fingerprint, _saved_scene)
    if saved_scene is not None:
        source, feedback = saved_scene
    resume_render = saved_scene is not None and feedback is None
    for attempt in range(1, attempt_limit + 1):
        if fallback is not None and attempt > fallback_attempt + 1:
            # The one layout round did not ship; the first render does.
            return fallback, attempt - 1, "layout_fallback"
        try:
            _remaining_timeout(started, deadline_seconds, 1)
        except _DeadlineExceeded as exc:
            if fallback is not None:
                return fallback, attempt, "layout_fallback"
            raise StoryFailed(str(exc)) from exc
        mode = "generate" if source is None else "edit"
        try:
            new_source = None
            if resume_render:
                new_source, mode, resume_render = source, "resume", False
                logger.info(
                    "%s: rendering the saved file without a model call", stage_name
                )
            elif source is not None:
                new_source = edit_scene_code(
                    storyboard,
                    durations,
                    source,
                    feedback,
                    bounded_complete,
                    model=scene_model,
                    chapter=chapter.note,
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
                    chapter=chapter.note,
                )
        except Exception as exc:  # noqa: BLE001 - the next round retries
            detail = _failure_detail(exc)
            logger.warning(
                "scene coder failed on attempt %s: %s", label(attempt), detail
            )
            failures.append(f"attempt {attempt}: coder error")
            records.append(
                {
                    "attempt": label(attempt),
                    "chapter": chapter.index,
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
        record = {
            "attempt": label(attempt),
            "chapter": chapter.index,
            "mode": mode,
            "source_path": str(scene_file),
        }
        findings = check_scene_code(source, len(storyboard.beats))
        if findings:
            feedback = "The guard rejected the file:\n- " + "\n- ".join(findings)
            remember(feedback)
            failures.append(f"attempt {attempt}: guard: {findings[0]}")
            records.append({**record, "status": "guard_rejected", "failure": feedback})
            continue

        try:
            render_timeout = _remaining_timeout(
                started, deadline_seconds, RENDER_TIMEOUT
            )
            video, timing = _render(scene_file, spec, render_timeout)
        except _DeadlineExceeded as exc:
            if fallback is not None:
                return fallback, attempt, "layout_fallback"
            raise StoryFailed(str(exc)) from exc
        except subprocess.CalledProcessError as exc:
            detail = _stderr_tail(exc)
            feedback = "Manim failed with this traceback:\n" + detail
            remember(feedback)
            logger.warning("render failed on attempt %s:\n%s", label(attempt), detail)
            failures.append(f"attempt {attempt}: render error")
            records.append({**record, "status": "render_error", "failure": detail})
            continue
        except subprocess.TimeoutExpired:
            feedback = (
                f"The render did not finish within {render_timeout} s. The scene "
                "is too heavy: use fewer or coarser mobjects, lighter updaters, "
                "and shorter run_times."
            )
            remember(feedback)
            logger.warning("render timed out on attempt %s", label(attempt))
            failures.append(f"attempt {attempt}: render timeout")
            records.append({**record, "status": "render_timeout", "failure": feedback})
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
            logger.warning(
                "render timing failed on attempt %s: %s", label(attempt), error
            )
            failures.append(f"attempt {attempt}: invalid timing")
            records.append(
                {
                    **record,
                    "status": "invalid_timing",
                    "failure": feedback,
                    "timing": timing,
                }
            )
            continue

        rendered = _Rendered(
            video, timing, source, label(attempt), source_path=scene_file
        )
        warnings = timing.get("layout_warnings") or []
        if warnings and fallback is not None:
            records.append({**record, "status": "layout_still_wrong", "timing": timing})
            return fallback, attempt, "layout_fallback"
        if (
            warnings
            and attempt < attempt_limit
            and deadline_seconds - (time.monotonic() - started)
            >= LAYOUT_ROUND_MIN_SECONDS
        ):
            fallback, fallback_attempt = rendered, attempt
            feedback = (
                "The video rendered, but the layout has problems at the end of "
                "these beats:\n- "
                + "\n- ".join(warnings)
                + "\nMove or shrink the named objects so no text overlaps other "
                "text and nothing crosses the frame margin. Keep everything else "
                "the same."
            )
            remember(feedback)
            records.append(
                {**record, "status": "layout_round", "failure": feedback, "timing": timing}
            )
            continue
        records.append({**record, "status": "shipped", "timing": timing})
        return rendered, attempt, "shipped"

    if fallback is not None:
        return fallback, attempt_limit, "layout_fallback"
    raise StoryFailed(
        f"no renderable scene in {attempt_limit} attempts: " + "; ".join(failures)
    )


def _merge_timing(lanes: list[_Chapter]) -> dict:
    """One timing for the joined video, with the keys of a single scene's
    timing. Beats and skipped layout audits are renumbered across the video
    and shifted by the length of the chapters before them; each layout
    warning names its chapter."""
    beats, warnings, skipped, offset = [], [], [], 0.0
    for lane in lanes:
        timing = lane.rendered.timing
        before = len(beats)
        for beat in timing["beats"]:
            beats.append(
                {
                    **beat,
                    "beat": len(beats) + 1,
                    "start": offset + beat["start"],
                    "end": offset + beat["end"],
                }
            )
        warnings += [
            f"chapter {lane.index}: {warning}"
            for warning in timing.get("layout_warnings") or []
        ]
        skipped += [before + beat for beat in timing.get("layout_skipped") or []]
        offset += float(timing["total"])
    return {
        "beats": beats,
        "total": offset,
        "layout_warnings": warnings,
        "layout_skipped": skipped,
    }


def _ship(
    lanes: list[_Chapter],
    *,
    storyboard: Storyboard,
    clips,
    work_dir: Path,
    out_path: Path,
    started: float,
    deadline_seconds: int,
    chapter_lanes: bool,
) -> StoryResult:
    """Join the lanes' renders and the narration into the final video."""
    # Per-beat lengths in chapter order drive the audio padding and the srt.
    lengths = [length for lane in lanes for length in beat_lengths(lane.rendered.timing)]
    remaining = int(deadline_seconds - (time.monotonic() - started))
    join_timeout = max(JOIN_TIMEOUT, min(RENDER_TIMEOUT, remaining))
    audio = concat_audio(
        clips,
        work_dir / "narration.mp3",
        target_seconds=lengths,
        timeout_seconds=join_timeout,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path = out_path.with_suffix(".srt")
    srt_path.write_text(build_srt(clips, durations=lengths), encoding="utf-8")
    videos = [lane.rendered.video for lane in lanes]
    video_path = mux_final(
        videos if chapter_lanes else videos[0],
        audio,
        out_path,
        timeout_seconds=join_timeout,
        saturation=OUTPUT_SATURATION,
    )
    first = lanes[0].rendered
    if not chapter_lanes:
        scene_source, timing, chapter_rows = first.source, first.timing, []
    else:
        # The "# chapter k" headers keep one text for the attempt upload and
        # the recent visual directions reader.
        scene_source = "\n\n".join(
            f"# chapter {lane.index}\n{lane.rendered.source}" for lane in lanes
        )
        timing = _merge_timing(lanes)
        chapter_rows = [
            {
                "index": lane.index,
                "beats": lane.beats,
                "status": lane.status,
                "attempts": lane.attempts,
                "source_path": str(lane.rendered.source_path),
                "video_path": str(lane.rendered.video),
                "timing": lane.rendered.timing,
            }
            for lane in lanes
        ]
    return StoryResult(
        video_path=Path(video_path),
        srt_path=srt_path,
        storyboard=storyboard,
        scene_source=scene_source,
        visual_direction=visual_direction(first.source),
        attempts=sum(lane.attempts for lane in lanes),
        timing=timing,
        selected_attempt=first.attempt,
        attempt_records=[record for lane in lanes for record in lane.records],
        chapters=chapter_rows,
    )
