import copy
import importlib.util
import json
import logging
import subprocess
from pathlib import Path

import pytest

from agentlab import story_video
from agentlab.frame_judge import BeatJudgement, Judgement
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
    return {"beats": beats, "total": cursor}


def _timing_with_raw_overruns(raw_overruns):
    beats, cursor = [], 0.0
    for index, raw_overrun in enumerate(raw_overruns, start=1):
        length = 5.0 + raw_overrun
        beats.append(
            {
                "beat": index,
                "start": cursor,
                "end": cursor + length,
                "narration": 5.0,
                "overrun": round(raw_overrun, 3),
            }
        )
        cursor += length
    return {"beats": beats, "total": cursor}


def _judgement(score=8, fix_beat=None):
    beats = [
        BeatJudgement(
            beat=index, grounded=True, shows_visual=True, legible=True, clean=True
        )
        for index in range(1, N + 1)
    ]
    if fix_beat:
        beats[fix_beat - 1] = BeatJudgement(
            beat=fix_beat,
            grounded=True,
            shows_visual=True,
            legible=True,
            clean=False,
            issue="overlap",
        )
    judgement = Judgement(beats=beats, score=score)
    judgement.verdict = "fix" if fix_beat else "pass"
    return judgement


def _ungrounded(score=6, beat=1):
    """A substantive defect: the frame does not match the storyboard."""
    judgement = _judgement(score=score)
    judgement.beats[beat - 1] = BeatJudgement(
        beat=beat,
        grounded=False,
        shows_visual=True,
        legible=True,
        clean=True,
        issue="shows a different mechanism",
    )
    judgement.verdict = "fix"
    return judgement


@pytest.fixture
def seams(monkeypatch):
    """Fake external steps while leaving the compose logic real."""
    state = {
        "renders": [],
        "codes": [],
        "judgements": [],
        "render_errors": [],
        "timings": [],
        "coder": [],
        "previous_sources": [],
        "frame_times": [],
        "frame_timeouts": [],
        "contact_sheets": [],
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
        output = Path(out_dir) / "videos" / "x" / "480p15" / f"{scene_class}.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(story_video, "render_scene_video", fake_render)

    def fake_sample_frames(video, times, out_dir, timeout_seconds):
        state["frame_times"].append(times)
        state["frame_timeouts"].append(timeout_seconds)
        return [Path(out_dir) / f"{index}.png" for index in range(len(times))]

    monkeypatch.setattr(story_video, "sample_frames", fake_sample_frames)

    def fake_contact_sheets(frames, out_dir, timeout_seconds):
        sheets = [Path(out_dir) / f"beat-{index}.png" for index in range(N)]
        state["contact_sheets"].append(sheets)
        return sheets

    monkeypatch.setattr(story_video, "contact_sheet_frames", fake_contact_sheets)
    monkeypatch.setattr(
        story_video,
        "judge_frames",
        lambda frames, storyboard, plan, complete, model: (
            state["judgements"].pop(0) if state["judgements"] else _judgement()
        ),
    )
    monkeypatch.setattr(
        story_video,
        "concat_audio",
        lambda clips, out, target_seconds=None, timeout_seconds=None: (
            state.__setitem__("targets", target_seconds) or Path(out)
        ),
    )

    def fake_mux(video, audio, out, timeout_seconds=None):
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
        judge_model="j",
        max_attempts=max_attempts,
    )


def _render_error(message="NameError: x"):
    return subprocess.CalledProcessError(
        1,
        ["manim"],
        stderr=f"Traceback...\n{message}",
    )


def test_happy_path_one_attempt_then_final_render(seams, tmp_path):
    result = _compose(tmp_path)

    assert result.video_path.read_bytes() == b"final"
    assert result.attempts == 1
    assert result.judge_score == 8
    assert [quality for quality, _ in seams["renders"]] == ["l", "m"]
    assert seams["renders"][0][1] == story_video.LOW_RENDER_TIMEOUT
    assert seams["renders"][1][1] == story_video.FINAL_RENDER_TIMEOUT
    assert seams["targets"] == [5.0] * N
    assert seams["frame_times"] == [
        [offset + 5.0 * index for index in range(N) for offset in (1.0, 2.5, 4.0)]
    ]
    assert seams["frame_timeouts"] == [story_video.FRAME_SAMPLE_TIMEOUT_SECONDS]
    assert len(seams["contact_sheets"][0]) == N
    assert result.srt_path.exists()
    assert "class PaperStory" in result.scene_source
    assert result.visual_direction.startswith("A crowded house")
    assert result.selected_attempt == 1
    assert result.passed is True
    assert result.attempt_records[0]["status"] == "passed"


def test_guard_finding_goes_back_to_the_coder(seams, tmp_path):
    seams["codes"] = ["import os\n" + GOLDEN_SCENE]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "import not allowed: os" in seams["coder"][1]
    assert len([quality for quality, _ in seams["renders"] if quality == "l"]) == 1


def test_render_traceback_is_logged_and_goes_back_to_the_coder(seams, tmp_path, caplog):
    seams["render_errors"] = [_render_error()]

    with caplog.at_level(logging.WARNING, logger="agentlab.story_video"):
        result = _compose(tmp_path)

    assert result.attempts == 2
    assert "NameError" in seams["coder"][1]
    assert "NameError: x" in caplog.text


def test_overrun_goes_back_to_the_coder(seams, tmp_path):
    seams["timings"] = [_timing([0.0, 2.0] + [0.0] * (N - 2))]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "beat 2" in seams["coder"][1]


def test_raw_per_beat_overrun_above_limit_goes_back_to_the_coder(seams, tmp_path):
    seams["timings"] = [
        _timing_with_raw_overruns([0.7504] + [0.0] * (N - 1)),
        _timing(),
    ]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "beat 1" in seams["coder"][1]


def test_inflated_timing_narration_cannot_hide_clip_overrun(seams, tmp_path):
    masked = _timing_with_raw_overruns([0.7504] + [0.0] * (N - 1))
    masked["beats"][0]["narration"] = 5.0008
    masked["beats"][0]["overrun"] = 0.75
    seams["timings"] = [masked, _timing()]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "beat 1" in seams["coder"][1]


def test_raw_total_overrun_above_limit_goes_back_to_the_coder(seams, tmp_path):
    raw_overrun = 3.0002 / N
    seams["timings"] = [
        _timing_with_raw_overruns([raw_overrun] * N),
        _timing(),
    ]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "total overrun" in seams["coder"][1]


def test_cosmetic_defect_ships_on_the_first_attempt(seams, tmp_path):
    """A beat with overlap is not worth a second coder call and render."""
    seams["judgements"] = [_judgement(score=7, fix_beat=2)]

    result = _compose(tmp_path)

    assert result.attempts == 1
    assert result.passed is False
    assert result.judge_score == 7
    assert [quality for quality, _ in seams["renders"]] == ["l", "m"]


def test_substantive_defect_still_goes_back_to_the_coder(seams, tmp_path):
    seams["judgements"] = [_ungrounded(score=7), _judgement(score=8)]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "beat 1" in seams["coder"][1]
    assert result.passed is True


def test_every_candidate_ungrounded_still_fails_the_gate(seams, tmp_path):
    seams["judgements"] = [_ungrounded(score=score) for score in (7, 4, 6, 5)]

    with pytest.raises(StoryFailed, match="quality gate failed after 4 attempts"):
        _compose(tmp_path)

    assert [quality for quality, _ in seams["renders"]] == ["l"] * 4


def test_best_shippable_candidate_wins_when_nothing_passes(seams, tmp_path):
    """Exhaustion ships the best clean-enough render instead of discarding
    four of them and paying for a template video on top."""
    seams["judgements"] = [
        _ungrounded(score=8),
        _judgement(score=5, fix_beat=3),
        _ungrounded(score=9),
        _judgement(score=6, fix_beat=2),
    ]

    result = _compose(tmp_path)

    assert result.passed is False
    assert result.judge_score == 6
    assert result.selected_attempt == 4


def test_a_weak_score_never_ships(seams, tmp_path):
    """Below the ship floor is substantive, not cosmetic."""
    seams["judgements"] = [_judgement(score=4, fix_beat=2)] * 4

    with pytest.raises(StoryFailed, match="quality gate failed after 4 attempts"):
        _compose(tmp_path)


def test_low_quality_judgement_retries_with_a_fresh_visual_concept(seams, tmp_path):
    weak = _judgement(score=5)
    weak.verdict = "fix"
    weak.note = "The composition is generic and static."
    seams["judgements"] = [weak, _judgement(score=8)]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "fresh visual concept" in seams["coder"][1]
    assert seams["previous_sources"][1] is None


def test_latest_candidate_wins_a_score_tie(seams, tmp_path):
    latest_source = GOLDEN_SCENE + "\n# latest candidate\n"
    seams["codes"] = [GOLDEN_SCENE, latest_source]
    seams["judgements"] = [
        _ungrounded(score=7),
        _judgement(score=7),
    ]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert result.scene_source == latest_source


def test_later_coder_failure_does_not_ship_earlier_rejected_candidate(seams, tmp_path):
    seams["codes"] = [
        GOLDEN_SCENE,
        RuntimeError("coder unavailable"),
        RuntimeError("coder unavailable"),
        RuntimeError("coder unavailable"),
    ]
    seams["judgements"] = [_ungrounded(score=7)]

    with pytest.raises(StoryFailed, match="quality gate failed after 4 attempts"):
        _compose(tmp_path)

    assert [quality for quality, _ in seams["renders"]] == ["l"]


def test_max_attempts_is_capped_at_four(seams, tmp_path):
    seams["render_errors"] = [_render_error()] * 5

    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path, max_attempts=99)

    assert "4 attempts" in str(exc.value)
    assert len(seams["coder"]) == 4


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
def test_invalid_preview_timing_goes_back_to_the_coder(seams, tmp_path, mutate):
    invalid = copy.deepcopy(_timing())
    mutate(invalid)
    seams["timings"] = [invalid, _timing()]

    result = _compose(tmp_path)

    assert result.attempts == 2
    assert "timing" in seams["coder"][1].lower()


def test_four_failures_raise_story_failed(seams, tmp_path):
    seams["render_errors"] = [_render_error()] * 4

    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path)

    assert "4 attempts" in str(exc.value)


def test_video_deadline_stops_new_attempts(seams, monkeypatch, tmp_path):
    seams["render_errors"] = [_render_error()] * 4
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
            judge_model="j",
            deadline_seconds=1,
        )

    assert len(seams["coder"]) == 1


def test_deadline_bound_completion_caps_each_model_call(monkeypatch):
    calls = []
    monkeypatch.setattr(story_video.time, "monotonic", lambda: 40.0)

    bounded = story_video._deadline_bound_completion(
        lambda model, messages, **kwargs: calls.append(kwargs) or "ok",
        started=10.0,
        deadline_seconds=100,
    )

    assert bounded("model", []) == "ok"
    assert calls == [{"timeout": 70}]


def test_storyboard_invalid_becomes_story_failed(seams, monkeypatch, tmp_path):
    def bad_storyboard(digest, plan, complete, model):
        raise StoryboardInvalid("missing mechanism")

    monkeypatch.setattr(story_video, "design_storyboard", bad_storyboard)

    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path)

    assert "storyboard" in str(exc.value)


def test_final_render_exception_logs_and_falls_back_to_preview(seams, tmp_path, caplog):
    seams["render_errors"] = [None, OSError("render disk full")]

    with caplog.at_level(logging.WARNING, logger="agentlab.story_video"):
        result = _compose(tmp_path)

    assert result.video_path.read_bytes() == b"final"
    assert result.timing == _timing()
    assert seams["targets"] == [5.0] * N
    assert "render disk full" in caplog.text
    assert "accepted preview" in caplog.text


def test_final_render_overrun_falls_back_to_preview(seams, tmp_path, caplog):
    seams["timings"] = [
        _timing(),
        _timing([0.0, 2.0] + [0.0] * (N - 2)),
    ]

    with caplog.at_level(logging.WARNING, logger="agentlab.story_video"):
        result = _compose(tmp_path)

    assert result.timing == _timing()
    assert seams["targets"] == [5.0] * N
    assert "beat 2" in caplog.text
    assert "accepted preview" in caplog.text


def test_malformed_final_timing_falls_back_to_preview(seams, tmp_path):
    invalid = _timing()
    invalid["total"] += 1.0
    seams["timings"] = [_timing(), invalid]

    result = _compose(tmp_path)

    assert result.timing == _timing()
    assert seams["targets"] == [5.0] * N


def test_unexpected_exception_becomes_story_failed(seams, monkeypatch, tmp_path):
    monkeypatch.setattr(
        story_video,
        "sample_frames",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk")),
    )

    with pytest.raises(story_video.StoryFailed) as exc:
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
