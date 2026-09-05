import json
import logging
import subprocess
from pathlib import Path

import pytest

from agentlab import frame_judge, video_render
from agentlab.storyboard import Storyboard

BOARD = Storyboard(
    **json.loads(
        (Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(
            encoding="utf-8"
        )
    )
)


def _beats(**overrides):
    base = [
        {
            "beat": i,
            "shows_visual": True,
            "legible": True,
            "clean": True,
            "issue": None,
        }
        for i in range(1, 6)
    ]
    for idx, fields in overrides.items():
        base[int(idx) - 1].update(fields)
    return base


def _judgement_json(beats=None, score=7):
    return json.dumps({"beats": beats if beats is not None else _beats(), "score": score})


def test_verdict_pass_when_all_clean_and_visible():
    assert frame_judge.verdict_from_beats(
        [frame_judge.BeatJudgement(**b) for b in _beats()]
    ) == "pass"


def test_verdict_fix_on_any_unclean_or_illegible_beat():
    beats = [
        frame_judge.BeatJudgement(**b)
        for b in _beats(**{"3": {"clean": False, "issue": "text overlaps bar"}})
    ]
    assert frame_judge.verdict_from_beats(beats) == "fix"
    beats = [
        frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"legible": False}})
    ]
    assert frame_judge.verdict_from_beats(beats) == "fix"


def test_verdict_tolerates_one_missing_visual_but_not_two():
    one = [
        frame_judge.BeatJudgement(**b)
        for b in _beats(**{"2": {"shows_visual": False}})
    ]
    assert frame_judge.verdict_from_beats(one) == "pass"
    two = [
        frame_judge.BeatJudgement(**b)
        for b in _beats(
            **{"2": {"shows_visual": False}, "4": {"shows_visual": False}}
        )
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
        "{\"beats\": " + json.dumps(_beats()) + ', "score": Infinity}'
    )
    assert judgement is None
    assert "score" in error


def test_judge_frames_retries_bad_json_then_falls_back_to_pass(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter(["garbage", "still garbage"])
    judgement = frame_judge.judge_frames(
        [frame] * 5, BOARD, lambda model, messages: next(replies), model="m"
    )
    assert judgement.verdict == "pass"
    assert judgement.score == frame_judge.FALLBACK_SCORE
    assert "judge unavailable" in judgement.note


def test_judge_frames_survives_a_model_exception(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")

    def boom(model, messages):
        raise RuntimeError("bedrock down")

    judgement = frame_judge.judge_frames([frame] * 5, BOARD, boom, model="m")
    assert judgement.verdict == "pass" and judgement.score == frame_judge.FALLBACK_SCORE


def test_judge_frames_retries_incomplete_judgement_then_accepts_complete(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter([_judgement_json(_beats()[:-1]), _judgement_json()])
    judgement = frame_judge.judge_frames(
        [frame] * 5, BOARD, lambda model, messages: next(replies), model="m"
    )
    assert len(judgement.beats) == len(BOARD.beats)
    assert judgement.note is None


@pytest.mark.parametrize(
    "beats",
    [
        _beats(**{"1": {"beat": 2}}),
        _beats(**{"5": {"beat": 6}}),
        _beats(**{"2": {"beat": 1}}),
    ],
)
def test_judge_frames_falls_back_after_misnumbered_or_duplicate_judgements(tmp_path, beats):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter([_judgement_json(beats), _judgement_json(beats)])
    judgement = frame_judge.judge_frames(
        [frame] * 5, BOARD, lambda model, messages: next(replies), model="m"
    )
    assert judgement.score == frame_judge.FALLBACK_SCORE
    assert "judge unavailable" in judgement.note


def test_build_judge_messages_rejects_frame_count_mismatch(tmp_path):
    frame = tmp_path / "frame.png"
    frame.write_bytes(b"png")
    with pytest.raises(ValueError, match="frames.*beats"):
        frame_judge.build_judge_messages([frame] * 4, BOARD)


def test_judge_frames_logs_unavailable_judge(tmp_path, caplog):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    with caplog.at_level(logging.WARNING, logger="agentlab.frame_judge"):
        frame_judge.judge_frames(
            [frame] * 5,
            BOARD,
            lambda model, messages: "garbage",
            model="m",
        )
    assert "judge unavailable" in caplog.text


def test_build_judge_messages_pairs_each_frame_with_its_beat(tmp_path):
    frames = []
    for i in range(5):
        p = tmp_path / f"frame_{i}.png"
        p.write_bytes(b"png" + bytes([i]))
        frames.append(p)
    messages = frame_judge.build_judge_messages(frames, BOARD)
    content = messages[-1]["content"]
    images = [part for part in content if part.get("type") == "image_url"]
    texts = " ".join(part["text"] for part in content if part.get("type") == "text")
    assert len(images) == 5
    assert all(
        part["image_url"]["url"].startswith("data:image/png;base64,") for part in images
    )
    for beat in BOARD.beats:
        assert beat.visual in texts


def test_sample_frames_calls_ffmpeg_once_per_time(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    frames = frame_judge.sample_frames(
        tmp_path / "v.mp4", [1.5, 4.25], tmp_path / "frames"
    )
    assert len(frames) == 2 and all(f.exists() for f in frames)
    assert calls[0][calls[0].index("-ss") + 1] == "1.500"
    assert "scale=960:-1" in " ".join(calls[1])


def test_judgement_feedback_lists_only_beats_with_problems():
    judgement = frame_judge.Judgement(
        beats=[
            frame_judge.BeatJudgement(
                **b
            )
            for b in _beats(
                **{"2": {"clean": False, "issue": "label off the right edge"}}
            )
        ],
        score=6,
    )
    text = frame_judge.judgement_feedback(judgement)
    assert "beat 2" in text and "label off the right edge" in text
    assert "beat 1" not in text
