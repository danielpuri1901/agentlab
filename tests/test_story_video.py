import copy
import dataclasses
import importlib.util
import json
import logging
import subprocess
from pathlib import Path

import pytest

from agentlab import story_video
from agentlab.scene_plan import ScenePlan
from agentlab.story_video import StoryFailed
from agentlab.storyboard import Storyboard, StoryboardInvalid
from agentlab.video_render import NarrationClip

FIXTURES = Path(__file__).parent / "fixtures"
BOARD = Storyboard(
    **json.loads((FIXTURES / "storyboard_golden.json").read_text(encoding="utf-8"))
)
PLAN = ScenePlan(
    **json.loads((FIXTURES / "sample_plan.json").read_text(encoding="utf-8"))
)
GOLDEN_SCENE = (FIXTURES / "paper_story_golden.py").read_text(encoding="utf-8")
EDITED_SCENE = GOLDEN_SCENE + "\n# edited\n"
DIGEST = "# Digest\n0% and 44% on 50 items.\n## Limits\nNone."
N = len(BOARD.beats)


def _timing(overruns=None):
    overruns = overruns or [0.0] * N
    beats, cursor = [], 0.0
    for index, overrun in enumerate(overruns, start=1):
        length = 5.0 + overrun
        beats.append(
            {
                "beat": index,
                "start": cursor,
                "end": cursor + length,
                "narration": 5.0,
                "overrun": overrun,
            }
        )
        cursor += length
    return {"beats": beats, "total": cursor, "layout_warnings": []}


@pytest.fixture
def seams(monkeypatch):
    """Fake external steps while leaving the compose logic real."""
    state = {
        "renders": [],
        "codes": [],
        "render_errors": [],
        "timings": [],
        "coder": [],
        "previous_sources": [],
        "edits": [],
        "edited": [],
    }

    monkeypatch.setattr(
        story_video,
        "design_storyboard",
        lambda digest, plan, complete, model, **kwargs: BOARD,
    )

    def fake_narrate(polly, texts, voice, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        clips = []
        for index, text in enumerate(texts):
            path = Path(out_dir) / f"clip_{index:02d}.mp3"
            path.write_bytes(b"mp3")
            clips.append(NarrationClip(path=path, seconds=5.0, text=text))
        return clips

    monkeypatch.setattr(story_video, "narrate", fake_narrate)

    def fake_coder(
        storyboard,
        durations,
        complete,
        model,
        feedback=None,
        previous_source=None,
    ):
        state["coder"].append(feedback)
        state["previous_sources"].append(previous_source)
        result = state["codes"].pop(0) if state["codes"] else GOLDEN_SCENE
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(story_video, "write_scene_code", fake_coder)

    def fake_edit(storyboard, durations, source, feedback, complete, model):
        state["edits"].append((source, feedback))
        result = state["edited"].pop(0) if state["edited"] else EDITED_SCENE
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(story_video, "edit_scene_code", fake_edit)

    def fake_render(
        scene_file,
        scene_class,
        spec,
        out_dir,
        quality="l",
        timeout_seconds=0,
        extra_env=None,
    ):
        state["renders"].append((quality, timeout_seconds))
        if state["render_errors"]:
            error = state["render_errors"].pop(0)
            if error is not None:
                raise error
        timing = state["timings"].pop(0) if state["timings"] else _timing()
        Path(extra_env["SCENE_TIMING_OUT"]).write_text(
            json.dumps(timing), encoding="utf-8"
        )
        output = Path(out_dir) / "videos" / "x" / "720p30" / f"{scene_class}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(story_video, "render_scene_video", fake_render)
    monkeypatch.setattr(
        story_video,
        "concat_audio",
        lambda clips, out, target_seconds=None, timeout_seconds=None: (
            state.__setitem__("targets", target_seconds) or Path(out)
        ),
    )

    def fake_mux(video, audio, out, timeout_seconds=None):
        state["muxed_video"] = Path(video)
        output = Path(out)
        output.write_bytes(b"final")
        return output

    monkeypatch.setattr(story_video, "mux_final", fake_mux)
    return state


def _compose(tmp_path, max_attempts=story_video.MAX_ATTEMPTS):
    return story_video.compose_story_video(
        DIGEST,
        PLAN,
        polly_client=None,
        voice_id="Ivy",
        complete=lambda model, messages: "",
        work_dir=tmp_path / "work",
        out_path=tmp_path / "out" / "video.mp4",
        story_model="s",
        scene_model="c",
        max_attempts=max_attempts,
    )


def _render_error(message="NameError: x"):
    return subprocess.CalledProcessError(
        1,
        ["manim"],
        stderr=f"Traceback (most recent call last):\n  File paper_story.py\n{message}",
    )


def test_first_successful_render_ships_without_a_judge(seams, tmp_path):
    result = _compose(tmp_path)

    assert result.video_path.read_bytes() == b"final"
    assert result.attempts == 1
    assert result.selected_attempt == 1
    assert seams["renders"] == [("m", story_video.RENDER_TIMEOUT)]
    assert seams["coder"] == [None]
    assert seams["muxed_video"].name == "PaperStory.mp4"
    assert seams["targets"] == [5.0] * N
    assert result.srt_path.exists()
    assert "class PaperStory" in result.scene_source
    assert result.visual_direction.startswith("A crowded house")
    assert result.timing == _timing()
    assert seams["edits"] == []
    assert _modes_and_statuses(result) == [("generate", "shipped")]
    fields = {field.name for field in dataclasses.fields(result)}
    assert not fields & {"judge_score", "judgement", "passed"}


def _modes_and_statuses(result):
    return [(record["mode"], record["status"]) for record in result.attempt_records]


def test_guard_finding_is_fixed_with_an_edit(seams, tmp_path):
    first = "import os\n" + GOLDEN_SCENE
    seams["codes"] = [first]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert seams["coder"] == [None]
    assert seams["edits"][0][0] == first
    assert "import not allowed: os" in seams["edits"][0][1]
    assert result.scene_source == EDITED_SCENE
    assert len(seams["renders"]) == 1
    assert _modes_and_statuses(result) == [
        ("generate", "guard_rejected"),
        ("edit", "shipped"),
    ]


def test_render_traceback_goes_to_an_edit_of_the_current_file(
    seams, tmp_path, caplog
):
    first = GOLDEN_SCENE + "\n# first attempt\n"
    seams["codes"] = [first]
    seams["render_errors"] = [_render_error()]

    with caplog.at_level(logging.WARNING, logger="agentlab.story_video"):
        result = _compose(tmp_path)

    assert result.attempts == 2
    traceback = (
        "Manim failed with this traceback:\n"
        "Traceback (most recent call last):\n  File paper_story.py\nNameError: x"
    )
    assert seams["edits"] == [(first, traceback)]
    assert seams["coder"] == [None]
    assert "NameError: x" in caplog.text
    assert _modes_and_statuses(result) == [
        ("generate", "render_error"),
        ("edit", "shipped"),
    ]


def test_render_timeout_goes_to_an_edit(seams, tmp_path):
    seams["render_errors"] = [subprocess.TimeoutExpired(["manim"], 600)]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "did not finish within 600 s" in seams["edits"][0][1]
    assert result.attempt_records[0]["status"] == "render_timeout"


def test_edits_that_do_not_apply_fall_back_to_a_whole_new_file(seams, tmp_path):
    first = GOLDEN_SCENE + "\n# first attempt\n"
    seams["codes"] = [first, GOLDEN_SCENE]
    seams["render_errors"] = [_render_error()]
    seams["edited"] = [None]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert seams["coder"][1].startswith("Manim failed with this traceback:")
    assert seams["previous_sources"] == [None, first]
    assert result.scene_source == GOLDEN_SCENE
    assert _modes_and_statuses(result) == [
        ("generate", "render_error"),
        ("rewrite", "shipped"),
    ]


def test_coder_error_before_any_file_writes_the_file_again(seams, tmp_path):
    seams["codes"] = [RuntimeError("model output hit the token limit")]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert seams["coder"][1] == (
        "The previous scene coder call failed: "
        "RuntimeError: model output hit the token limit"
    )
    assert seams["previous_sources"] == [None, None]
    assert seams["edits"] == []
    assert _modes_and_statuses(result) == [
        ("generate", "coder_error"),
        ("generate", "shipped"),
    ]


def test_coder_error_during_an_edit_keeps_fixing_the_render_error(seams, tmp_path):
    seams["render_errors"] = [_render_error()]
    seams["edited"] = [RuntimeError("throttled"), EDITED_SCENE]

    result = _compose(tmp_path)

    assert result.attempts == 3
    assert seams["edits"][0] == seams["edits"][1]
    assert seams["edits"][1][1].startswith("Manim failed with this traceback:")
    assert _modes_and_statuses(result) == [
        ("generate", "render_error"),
        ("edit", "coder_error"),
        ("edit", "shipped"),
    ]


def test_overrun_ships_and_pads_the_audio_to_the_beat(seams, tmp_path):
    """A beat longer than its narration is not a failure: the audio clip is
    padded to the beat's length."""
    overrun = _timing([0.0, 2.5] + [0.0] * (N - 2))
    seams["timings"] = [overrun]

    result = _compose(tmp_path)

    assert result.attempts == 1
    assert seams["coder"] == [None]
    assert seams["targets"] == [5.0, 7.5] + [5.0] * (N - 2)
    assert result.timing == overrun


def test_layout_warnings_ship_with_the_timing(seams, tmp_path):
    timing = _timing()
    timing["layout_warnings"] = ["beat 2 layout: 'Agent' runs off the left edge"]
    seams["timings"] = [timing]

    result = _compose(tmp_path)

    assert result.attempts == 1
    assert result.timing["layout_warnings"] == timing["layout_warnings"]


def test_one_file_and_five_fix_rounds_at_most(seams, tmp_path):
    seams["render_errors"] = [_render_error()] * 7

    with pytest.raises(StoryFailed) as exc:
        _compose(tmp_path, max_attempts=99)

    assert "no renderable scene in 6 attempts" in str(exc.value)
    assert seams["coder"] == [None]
    assert len(seams["edits"]) == 5


def test_story_failed_names_each_failure(seams, tmp_path):
    seams["codes"] = [RuntimeError("throttled"), "import os\n" + GOLDEN_SCENE]
    seams["render_errors"] = [_render_error()]

    with pytest.raises(StoryFailed) as exc:
        _compose(tmp_path, max_attempts=3)

    message = str(exc.value)
    assert "no renderable scene in 3 attempts" in message
    assert "attempt 1: coder error" in message
    assert "attempt 2: guard: import not allowed: os" in message
    assert "attempt 3: render error" in message


def _drop_last_beat(timing):
    timing["beats"].pop()


def _misnumber_beat(timing):
    timing["beats"][1]["beat"] = 1


def _make_start_nonfinite(timing):
    timing["beats"][0]["start"] = float("nan")


def _make_start_negative(timing):
    timing["beats"][0]["start"] = -0.1


def _make_gap(timing):
    timing["beats"][1]["start"] += 0.1


def _make_zero_length(timing):
    timing["beats"][0]["end"] = timing["beats"][0]["start"]


def _make_beat_shorter_than_narration(timing):
    timing["beats"][0]["end"] -= 0.1
    previous_end = timing["beats"][0]["end"]
    for beat in timing["beats"][1:]:
        beat["start"] = previous_end
        beat["end"] = previous_end + 5.0
        previous_end = beat["end"]
    timing["total"] = previous_end


def _mismatch_narration(timing):
    timing["beats"][0]["narration"] = 4.0


def _mismatch_total(timing):
    timing["total"] += 1.0


def _mismatch_overrun(timing):
    timing["beats"][0]["overrun"] = 0.2


@pytest.mark.parametrize(
    "mutate",
    [
        _drop_last_beat,
        _misnumber_beat,
        _make_start_nonfinite,
        _make_start_negative,
        _make_gap,
        _make_zero_length,
        _make_beat_shorter_than_narration,
        _mismatch_narration,
        _mismatch_total,
        _mismatch_overrun,
    ],
    ids=[
        "incomplete",
        "beat-ids",
        "nonfinite",
        "negative",
        "noncontiguous",
        "nonpositive-length",
        "shorter-than-narration",
        "narration",
        "total",
        "overrun",
    ],
)
def test_invalid_timing_goes_to_an_edit(seams, tmp_path, mutate):
    invalid = copy.deepcopy(_timing())
    mutate(invalid)
    seams["timings"] = [invalid, _timing()]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "timing" in seams["edits"][0][1].lower()
    assert result.attempt_records[0]["status"] == "invalid_timing"


def test_unreadable_timing_goes_to_an_edit(seams, monkeypatch, tmp_path):
    fake_render = story_video.render_scene_video
    lose_timing = [True]

    def render(*args, **kwargs):
        video = fake_render(*args, **kwargs)
        if lose_timing and lose_timing.pop():
            Path(kwargs["extra_env"]["SCENE_TIMING_OUT"]).unlink()
        return video

    monkeypatch.setattr(story_video, "render_scene_video", render)

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "timing output could not be read" in seams["edits"][0][1]
    assert result.attempt_records[0]["status"] == "invalid_timing"


def test_video_deadline_stops_new_attempts(seams, monkeypatch, tmp_path):
    times = iter([0.0, 0.0, 2.0])
    monkeypatch.setattr(story_video.time, "monotonic", lambda: next(times, 2.0))

    with pytest.raises(StoryFailed, match="deadline"):
        story_video.compose_story_video(
            DIGEST,
            PLAN,
            polly_client=None,
            voice_id="Ivy",
            complete=lambda model, messages: "",
            work_dir=tmp_path / "work",
            out_path=tmp_path / "out" / "video.mp4",
            story_model="s",
            scene_model="c",
            deadline_seconds=1,
        )

    assert len(seams["coder"]) == 1
    assert seams["renders"] == []


@pytest.mark.parametrize(
    ("deadline", "timeout"),
    [(100, 70), (story_video.VIDEO_DEADLINE_SECONDS, story_video.MODEL_CALL_TIMEOUT)],
)
def test_deadline_bound_completion_caps_each_model_call(monkeypatch, deadline, timeout):
    """30 s into the video: the call gets what is left of the deadline, up to
    the per-call cap."""
    calls = []
    monkeypatch.setattr(story_video.time, "monotonic", lambda: 40.0)

    bounded = story_video._deadline_bound_completion(
        lambda model, messages, **kwargs: calls.append(kwargs) or "ok",
        started=10.0,
        deadline_seconds=deadline,
    )

    assert bounded("model", [], max_tokens=8000) == "ok"
    assert calls == [{"timeout": timeout, "max_tokens": 8000}]


def test_storyboard_invalid_becomes_story_failed(seams, monkeypatch, tmp_path):
    def bad_storyboard(digest, plan, complete, model, **kwargs):
        raise StoryboardInvalid("missing mechanism")

    monkeypatch.setattr(story_video, "design_storyboard", bad_storyboard)

    with pytest.raises(StoryFailed) as exc:
        _compose(tmp_path)

    assert "storyboard" in str(exc.value)


def test_unexpected_exception_becomes_story_failed(seams, monkeypatch, tmp_path):
    monkeypatch.setattr(
        story_video,
        "concat_audio",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )

    with pytest.raises(StoryFailed) as exc:
        _compose(tmp_path)

    assert "OSError" in str(exc.value)


def test_local_runner_main_parses_url_and_output(monkeypatch, tmp_path):
    script = Path(__file__).parent.parent / "scripts" / "story_video_for_url.py"
    spec = importlib.util.spec_from_file_location("story_video_for_url", script)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    calls = []
    monkeypatch.setattr(
        runner,
        "run_story_video_for_url",
        lambda url, output: calls.append((url, output)) or 0,
    )

    exit_code = runner.main(["https://example.com/paper", str(tmp_path)])

    assert exit_code == 0
    assert calls == [("https://example.com/paper", str(tmp_path))]
