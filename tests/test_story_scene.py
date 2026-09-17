import json
from pathlib import Path

import pytest

from agentlab import story_scene

GOLDEN_SCENE = Path(__file__).parent / "fixtures" / "paper_story_golden.py"
GOLDEN_BOARD = Path(__file__).parent / "fixtures" / "storyboard_golden.json"


def test_module_imports_without_manim_and_exposes_pure_helpers():
    assert callable(story_scene.overrun_report)
    assert story_scene.SCENE_CLASS == "PaperStory"
    assert story_scene.STAGE_BOTTOM < 0 < story_scene.STAGE_TOP


def test_beat_record_measures_overrun_never_negative():
    rec = story_scene.beat_record(2, start=10.0, end=14.5, narration_seconds=4.0)
    assert rec == {
        "beat": 2,
        "start": 10.0,
        "end": 14.5,
        "narration": 4.0,
        "overrun": 0.5,
    }
    assert story_scene.beat_record(1, 0.0, 3.0, 4.0)["overrun"] == 0.0


def test_overrun_report_is_none_within_limits():
    timing = {
        "beats": [
            story_scene.beat_record(1, 0, 4.4, 4.0),
            story_scene.beat_record(2, 4.4, 8.0, 3.6),
        ]
    }
    assert story_scene.overrun_report(timing) is None


def test_overrun_report_names_the_offending_beats():
    timing = {
        "beats": [
            story_scene.beat_record(1, 0, 5.0, 4.0),
            story_scene.beat_record(2, 5.0, 8.0, 3.0),
        ]
    }
    report = story_scene.overrun_report(timing)
    assert "beat 1" in report and "1.0 s" in report
    assert "beat 2" not in report


def test_overrun_report_flags_total_even_when_each_beat_is_small():
    beats = [
        story_scene.beat_record(i, i * 4.0, i * 4.0 + 4.7, 4.0) for i in range(1, 6)
    ]
    report = story_scene.overrun_report({"beats": beats})
    assert "total overrun" in report


def test_beat_midpoints_and_lengths():
    timing = {
        "beats": [
            story_scene.beat_record(1, 0.0, 4.0, 4.0),
            story_scene.beat_record(2, 4.0, 10.0, 6.0),
        ]
    }
    assert story_scene.beat_midpoints(timing) == [2.0, 7.0]
    assert story_scene.beat_lengths(timing) == [4.0, 6.0]


def test_load_spec_requires_env(monkeypatch):
    monkeypatch.delenv("SCENE_SPEC_JSON", raising=False)
    with pytest.raises(RuntimeError):
        story_scene.load_spec()


def test_golden_scene_defines_one_beat_method_per_golden_beat():
    import ast

    tree = ast.parse(GOLDEN_SCENE.read_text(encoding="utf-8"))
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PaperStory"
    )
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    beats = json.loads(GOLDEN_BOARD.read_text(encoding="utf-8"))["beats"]
    assert {f"beat_{i}" for i in range(1, len(beats) + 1)} <= methods
    assert "construct" not in methods


@pytest.mark.render
def test_golden_scene_renders_and_writes_timing(tmp_path):
    import shutil

    from agentlab import video_render

    board = json.loads(GOLDEN_BOARD.read_text(encoding="utf-8"))
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    shutil.copy(GOLDEN_SCENE, scene_dir / "paper_story.py")
    shutil.copy(Path(story_scene.__file__), scene_dir / "story_scene.py")
    durations = [6.0] * len(board["beats"])
    captions = [b["narration"] for b in board["beats"]]
    timing_path = tmp_path / "beat_times.json"

    video = video_render.render_scene_video(
        scene_dir / "paper_story.py",
        "PaperStory",
        {"storyboard": board, "durations": durations, "captions": captions},
        tmp_path / "media",
        quality="l",
        extra_env={"SCENE_TIMING_OUT": str(timing_path)},
    )

    assert video.exists()
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert [b["beat"] for b in timing["beats"]] == list(
        range(1, len(board["beats"]) + 1)
    )
    assert all(b["overrun"] < 0.05 for b in timing["beats"])
    assert abs(timing["total"] - sum(durations)) < 0.2
    assert abs(video_render.ffprobe_duration(video) - sum(durations)) < 0.5


@pytest.mark.render
def test_fractional_narration_durations_are_padded_to_whole_frames(tmp_path):
    import shutil

    from agentlab import video_render

    board = json.loads(GOLDEN_BOARD.read_text(encoding="utf-8"))
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    shutil.copy(GOLDEN_SCENE, scene_dir / "paper_story.py")
    shutil.copy(Path(story_scene.__file__), scene_dir / "story_scene.py")
    fractional = [6.013, 7.021, 7.039, 6.077, 8.111]
    durations = [
        fractional[index % len(fractional)] for index in range(len(board["beats"]))
    ]
    captions = [beat["narration"] for beat in board["beats"]]
    timing_path = tmp_path / "beat_times.json"

    video_render.render_scene_video(
        scene_dir / "paper_story.py",
        "PaperStory",
        {"storyboard": board, "durations": durations, "captions": captions},
        tmp_path / "media",
        quality="l",
        extra_env={"SCENE_TIMING_OUT": str(timing_path)},
    )

    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    frame_seconds = 1 / 15
    for beat, narration_seconds in zip(timing["beats"], durations, strict=True):
        actual_seconds = beat["end"] - beat["start"]
        assert actual_seconds >= narration_seconds - 1e-6
        assert actual_seconds < narration_seconds + frame_seconds + 1e-6
