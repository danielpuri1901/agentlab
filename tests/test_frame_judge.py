import json
import logging
import subprocess
from pathlib import Path

import pytest

from agentlab import frame_judge, video_render
from agentlab.scene_plan import ScenePlan
from agentlab.storyboard import Storyboard

BOARD = Storyboard(
    **json.loads(
        (Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(
            encoding="utf-8"
        )
    )
)
N = len(BOARD.beats)
PLAN = ScenePlan(
    **json.loads(
        (Path(__file__).parent / "fixtures" / "sample_plan.json").read_text(
            encoding="utf-8"
        )
    )
)


def _beats(**overrides):
    base = [
        {
            "beat": i,
            "grounded": True,
            "shows_visual": True,
            "legible": True,
            "clean": True,
            "issue": None,
        }
        for i in range(1, N + 1)
    ]
    for idx, fields in overrides.items():
        base[int(idx) - 1].update(fields)
    return base


def _judgement_json(beats=None, score=7):
    return json.dumps(
        {"beats": beats if beats is not None else _beats(), "score": score}
    )


def test_verdict_pass_when_all_clean_and_visible():
    assert (
        frame_judge.verdict_from_beats(
            [frame_judge.BeatJudgement(**b) for b in _beats()]
        )
        == "pass"
    )


def test_verdict_fix_on_any_unclean_or_illegible_beat():
    beats = [
        frame_judge.BeatJudgement(**b)
        for b in _beats(**{"3": {"clean": False, "issue": "text overlaps bar"}})
    ]
    assert frame_judge.verdict_from_beats(beats) == "fix"


def test_verdict_fix_on_ungrounded_beat_or_low_score():
    beats = [
        frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"grounded": False}})
    ]
    assert frame_judge.verdict_from_beats(beats, score=10) == "fix"
    clean = [frame_judge.BeatJudgement(**b) for b in _beats()]
    assert frame_judge.verdict_from_beats(clean, score=0) == "fix"
    beats = [
        frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"legible": False}})
    ]
    assert frame_judge.verdict_from_beats(beats) == "fix"


def test_verdict_rejects_any_missing_visual():
    one = [
        frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"shows_visual": False}})
    ]
    assert frame_judge.verdict_from_beats(one) == "fix"
    two = [
        frame_judge.BeatJudgement(**b)
        for b in _beats(**{"2": {"shows_visual": False}, "4": {"shows_visual": False}})
    ]
    assert frame_judge.verdict_from_beats(two) == "fix"


def test_parse_judgement_recomputes_verdict_and_clamps_score():
    raw = "```json\n" + _judgement_json(_beats(**{"1": {"clean": False}}), 14) + "\n```"
    judgement, error = frame_judge.parse_judgement(raw)
    assert error == ""
    assert judgement.score == 10
    assert judgement.verdict == "fix"


def test_parse_judgement_rejects_nonfinite_score():
    judgement, error = frame_judge.parse_judgement(
        '{"beats": ' + json.dumps(_beats()) + ', "score": Infinity}'
    )
    assert judgement is None
    assert "score" in error


def test_parse_judgement_recovers_score_appended_inside_beats():
    malformed = {"beats": [*_beats(), {"score": 8}]}
    judgement, error = frame_judge.parse_judgement(json.dumps(malformed))
    assert error == ""
    assert judgement.score == 8
    assert len(judgement.beats) == N


def test_judge_frames_retries_bad_json_then_falls_back_to_fix(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter(["garbage", "still garbage"])
    judgement = frame_judge.judge_frames(
        [frame] * N, BOARD, PLAN, lambda model, messages: next(replies), model="m"
    )
    assert judgement.verdict == "fix"
    assert judgement.score == frame_judge.FALLBACK_SCORE
    assert "judge unavailable" in judgement.note


def test_judge_frames_survives_a_model_exception(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")

    def boom(model, messages):
        raise RuntimeError("bedrock down")

    judgement = frame_judge.judge_frames([frame] * N, BOARD, PLAN, boom, model="m")
    assert judgement.verdict == "fix" and judgement.score == frame_judge.FALLBACK_SCORE


def test_judge_frames_retries_incomplete_judgement_then_accepts_complete(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter([_judgement_json(_beats()[:-1]), _judgement_json()])
    judgement = frame_judge.judge_frames(
        [frame] * N, BOARD, PLAN, lambda model, messages: next(replies), model="m"
    )
    assert len(judgement.beats) == len(BOARD.beats)
    assert judgement.note is None


@pytest.mark.parametrize(
    "beats",
    [
        _beats(**{"1": {"beat": 2}}),
        _beats(**{str(N): {"beat": N + 1}}),
        _beats(**{"2": {"beat": 1}}),
    ],
)
def test_judge_frames_falls_back_after_misnumbered_or_duplicate_judgements(
    tmp_path, beats
):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter([_judgement_json(beats), _judgement_json(beats)])
    judgement = frame_judge.judge_frames(
        [frame] * N, BOARD, PLAN, lambda model, messages: next(replies), model="m"
    )
    assert judgement.score == frame_judge.FALLBACK_SCORE
    assert "judge unavailable" in judgement.note


def test_build_judge_messages_rejects_frame_count_mismatch(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    with pytest.raises(ValueError, match="frames.*beats"):
        frame_judge.build_judge_messages([frame] * 4, BOARD, PLAN)


def test_judge_frames_logs_unavailable_judge(tmp_path, caplog):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    with caplog.at_level(logging.WARNING, logger="agentlab.frame_judge"):
        frame_judge.judge_frames(
            [frame] * N,
            BOARD,
            PLAN,
            lambda model, messages: "garbage",
            model="m",
        )
    assert "judge unavailable" in caplog.text


def test_build_judge_messages_pairs_each_contact_sheet_with_its_beat(tmp_path):
    sheets = []
    for i in range(N):
        p = tmp_path / f"sheet_{i}.png"
        p.write_bytes(b"png" + bytes([i]))
        sheets.append(p)
    messages = frame_judge.build_judge_messages(sheets, BOARD, PLAN)
    content = messages[-1]["content"]
    images = [part for part in content if part.get("type") == "image_url"]
    texts = " ".join(part["text"] for part in content if part.get("type") == "text")
    assert len(images) == N
    assert all(
        part["image_url"]["url"].startswith("data:image/png;base64,") for part in images
    )
    for beat in BOARD.beats:
        assert beat.visual in texts
        for label in beat.on_screen_text:
            assert label in texts
    assert PLAN.one_line_claim in texts
    assert "left panel is the start" in texts
    assert "middle panel is the middle" in texts
    assert "right panel is the end" in texts


def test_judge_requires_title_real_mechanism_and_simple_terms():
    assert "Beat 1 must show the title" in frame_judge.JUDGE_SYSTEM
    assert "real mechanism" in frame_judge.JUDGE_SYSTEM
    assert "unexplained technical term" in frame_judge.JUDGE_SYSTEM
    assert "abstract visual" in frame_judge.JUDGE_SYSTEM
    assert "visually compelling" in frame_judge.JUDGE_SYSTEM
    assert "paper-specific" in frame_judge.JUDGE_SYSTEM
    assert "top-level sibling" in frame_judge.JUDGE_SYSTEM


def test_sample_frames_calls_ffmpeg_once_per_time(monkeypatch, tmp_path):
    calls = []
    timeouts = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        timeouts.append(kwargs.get("timeout"))
        Path(cmd[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    frames = frame_judge.sample_frames(
        tmp_path / "v.mp4", [1.5, 4.25], tmp_path / "frames"
    )
    assert len(frames) == 2 and all(f.exists() for f in frames)
    assert calls[0][calls[0].index("-ss") + 1] == "1.500"
    assert "scale=480:-1" in " ".join(calls[1])
    assert timeouts[0] <= frame_judge.FRAME_SAMPLE_TIMEOUT_SECONDS
    assert 0 < timeouts[1] <= timeouts[0]


def test_contact_sheet_frames_combines_three_samples_per_beat(monkeypatch, tmp_path):
    frames = []
    for index in range(6):
        frame = tmp_path / f"frame-{index}.png"
        frame.write_bytes(b"png")
        frames.append(frame)
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        Path(cmd[-1]).write_bytes(b"sheet")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)

    sheets = frame_judge.contact_sheet_frames(frames, tmp_path / "sheets")

    assert len(sheets) == 2
    assert len(calls) == 2
    assert all("hstack=inputs=3" in call[0] for call in calls)
    assert 0 < calls[-1][1]["timeout"] <= calls[0][1]["timeout"]


def test_judgement_feedback_lists_only_beats_with_problems():
    judgement = frame_judge.Judgement(
        beats=[
            frame_judge.BeatJudgement(**b)
            for b in _beats(
                **{"2": {"clean": False, "issue": "label off the right edge"}}
            )
        ],
        score=6,
    )
    text = frame_judge.judgement_feedback(judgement)
    assert "beat 2" in text and "label off the right edge" in text
    assert "beat 1" not in text


def test_judgement_feedback_explains_low_visual_quality_without_failed_beats():
    judgement = frame_judge.Judgement(
        beats=[frame_judge.BeatJudgement(**b) for b in _beats()],
        score=5,
        note="The composition stays static and could fit any paper.",
    )

    text = frame_judge.judgement_feedback(judgement)

    assert "static" in text
    assert "fresh visual concept" in text


def test_judgement_feedback_preserves_scene_when_judge_is_unavailable():
    judgement = frame_judge.Judgement(
        beats=[],
        score=5,
        verdict="fix",
        note="judge unavailable: timeout",
    )

    text = frame_judge.judgement_feedback(judgement)

    assert "same scene file unchanged" in text
    assert "timeout" in text
