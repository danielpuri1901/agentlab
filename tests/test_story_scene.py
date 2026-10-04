import json
from pathlib import Path

import pytest

from agentlab import story_scene

GOLDEN_SCENE = Path(__file__).parent / "fixtures" / "paper_story_golden.py"
GOLDEN_BOARD = Path(__file__).parent / "fixtures" / "storyboard_golden.json"


def test_module_imports_without_manim_and_exposes_pure_helpers():
    assert callable(story_scene.overrun_report)
    assert callable(story_scene.layout_warning)
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
    assert timing["layout_warnings"] == []
    assert abs(video_render.ffprobe_duration(video) - sum(durations)) < 0.5


CAMERA_SCENE = '''# Visual direction: A sphere under an orbiting camera, a square lost off stage.
from manim import DEGREES, LEFT, Create, FadeIn, Sphere, Square
from story_scene import StoryScene


class PaperStory(StoryScene):
    def beat_1(self):
        self.ball = Sphere(radius=1.0, resolution=(8, 8))
        self.play(Create(self.ball), run_time=0.5)
        self.move_camera(phi=70 * DEGREES, theta=-45 * DEGREES, zoom=0.8, run_time=1.0)
        self.begin_ambient_camera_rotation(rate=0.4)

    def beat_2(self):
        self.lost = Square(side_length=1.0).shift(LEFT * 8)
        self.play(FadeIn(self.lost), run_time=0.5)

    def beat_3(self):
        self.clear_stage()
        self.hold(2.5)
'''


@pytest.mark.render
def test_camera_moves_render_and_layout_problems_only_warn(tmp_path):
    """A 3D scene with camera moves renders; a square left off the stage and
    a beat longer than its narration are recorded, not fatal."""
    import shutil

    from agentlab import video_render

    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "paper_story.py").write_text(CAMERA_SCENE, encoding="utf-8")
    shutil.copy(Path(story_scene.__file__), scene_dir / "story_scene.py")
    durations = [2.0, 2.0, 2.0]
    timing_path = tmp_path / "beat_times.json"

    video = video_render.render_scene_video(
        scene_dir / "paper_story.py",
        "PaperStory",
        {
            "storyboard": {"beats": [{}, {}, {}]},
            "durations": durations,
            "captions": ["one", "two", "three"],
        },
        tmp_path / "media",
        quality="l",
        extra_env={"SCENE_TIMING_OUT": str(timing_path)},
    )

    assert video.exists()
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert [b["beat"] for b in timing["beats"]] == [1, 2, 3]
    assert timing["beats"][2]["overrun"] > 0.5
    assert len(timing["layout_warnings"]) == 1
    assert timing["layout_warnings"][0].startswith("beat 2 layout:")
    assert "left edge" in timing["layout_warnings"][0]


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


def test_layout_problems_passes_a_frame_inside_the_stage():
    boxes = [("title", -2.0, 2.0, 2.0, 3.0), ("diagram", -3.0, 3.0, -1.0, 1.0)]

    assert story_scene.layout_problems(boxes, caption_top=-2.0) == []


def test_layout_problems_names_the_element_that_leaves_the_stage():
    """The hexagons spilled outside the context-window panel on 2026-09-23."""
    boxes = [("hexagons", story_scene.STAGE_LEFT - 0.6, -2.0, 0.0, 1.0)]

    problems = story_scene.layout_problems(boxes)

    assert problems == ["hexagons runs off the left edge"]


def test_layout_problems_catches_every_edge():
    far = 99.0
    problems = story_scene.layout_problems([("blob", -far, far, -far, far)])

    assert len(problems) == 4


def test_layout_problems_catches_the_caption_band():
    """The judge called this "the rectangle clips the caption band"."""
    boxes = [("'Fine-tuning not evaluated'", -1.0, 1.0, -2.2, -1.6)]

    problems = story_scene.layout_problems(boxes, caption_top=-2.0)

    assert problems == ["'Fine-tuning not evaluated' sits on the caption band"]


def test_layout_problems_allows_a_box_resting_above_the_caption():
    boxes = [("bar", -1.0, 1.0, -1.8, -1.0)]

    assert story_scene.layout_problems(boxes, caption_top=-2.0) == []


def test_layout_problems_tolerates_stroke_width_at_the_edge():
    boxes = [("panel", story_scene.STAGE_LEFT - 0.01, 1.0, 0.0, 1.0)]

    assert story_scene.layout_problems(boxes) == []


def test_layout_problems_ignores_the_caption_band_before_a_caption_exists():
    boxes = [("intro", -1.0, 1.0, story_scene.STAGE_BOTTOM, -1.0)]

    assert story_scene.layout_problems(boxes, caption_top=None) == []


def test_layout_warning_names_the_beat_and_the_element_instead_of_raising():
    boxes = [("hexagons", story_scene.STAGE_LEFT - 0.6, -2.0, 0.0, 1.0)]

    warning = story_scene.layout_warning(3, boxes)

    assert warning == "beat 3 layout: hexagons runs off the left edge"


def test_layout_warning_is_none_when_everything_is_on_stage():
    boxes = [("title", -2.0, 2.0, 2.0, 3.0)]

    assert story_scene.layout_warning(1, boxes, caption_top=-2.0) is None


def test_layout_warning_lists_at_most_a_few_problems():
    far = 99.0
    warning = story_scene.layout_warning(2, [("blob", -far, far, -far, far)] * 3)

    assert warning.count("blob") == story_scene.MAX_REPORTED_PROBLEMS


def test_layout_problems_ignores_mobjects_that_draw_nothing():
    """A ValueTracker is a point whose x coordinate is the value it holds, so
    a counter running to 175 parks it far off stage while drawing nothing."""
    boxes = [("ValueTracker", 175.0, 175.0, 0.0, 0.0)]

    assert story_scene.layout_problems(boxes) == []
