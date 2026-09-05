import ast
import json
from pathlib import Path

import pytest

from agentlab import scene_code, story_scene
from agentlab.storyboard import Storyboard

GOLDEN_SCENE = (Path(__file__).parent / "fixtures" / "paper_story_golden.py").read_text(encoding="utf-8")
GOLDEN_BOARD = json.loads((Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(encoding="utf-8"))
BEATS = len(GOLDEN_BOARD["beats"])


def test_golden_scene_passes_the_guard():
    assert scene_code.check_scene_code(GOLDEN_SCENE, BEATS) == []


@pytest.mark.parametrize(
    "snippet, needle",
    [
        ("import os\n", "os"),
        ("from pathlib import Path\n", "pathlib"),
        ("import requests\n", "requests"),
        ("x = open('/etc/passwd')\n", "open"),
        ("y = __import__('os')\n", "__import__"),
        ("z = ().__class__.__mro__\n", "__class__"),
        ("from manim import MathTex\n", "MathTex"),
        ("from manim import DecimalNumber\n", "DecimalNumber"),
        ("from manim import BarChart\n", "BarChart"),
        ("w = self.camera.frame\n", "camera"),
    ],
)
def test_guard_flags_forbidden_things(snippet, needle):
    source = snippet + GOLDEN_SCENE
    findings = scene_code.check_scene_code(source, BEATS)
    assert any(needle in f for f in findings), findings


def test_guard_flags_include_numbers_true():
    source = GOLDEN_SCENE.replace(
        "self.hold(0.6)", "self.axes = Axes(x_range=[0, 1], axis_config={'include_numbers': True}); self.hold(0.6)"
    )
    source = source.replace("from manim import (", "from manim import (\n    Axes,")
    findings = scene_code.check_scene_code(source, BEATS)
    assert any("include_numbers" in f for f in findings)


def test_guard_requires_every_beat_method_and_no_extras():
    missing = GOLDEN_SCENE.replace("def beat_5(self):", "def beat_9(self):")
    findings = scene_code.check_scene_code(missing, BEATS)
    assert any("beat_5" in f for f in findings)
    assert any("beat_9" in f for f in findings)


def test_guard_rejects_construct_override_and_wrong_class():
    with_construct = GOLDEN_SCENE + "\n    def construct(self):\n        pass\n"
    assert any("construct" in f for f in scene_code.check_scene_code(with_construct, BEATS))
    renamed = GOLDEN_SCENE.replace("class PaperStory(StoryScene):", "class Other(StoryScene):")
    assert any("PaperStory" in f for f in scene_code.check_scene_code(renamed, BEATS))


def test_guard_reports_syntax_errors():
    findings = scene_code.check_scene_code("def (:\n", BEATS)
    assert findings and "syntax" in findings[0]


def test_extract_python_block_takes_the_last_fenced_block():
    raw = "thinking...\n```python\nx = 1\n```\nfix:\n```python\nclass PaperStory: pass\n```\n"
    assert scene_code.extract_python_block(raw) == "class PaperStory: pass"


def test_extract_python_block_falls_back_to_raw_when_unfenced():
    raw = "from story_scene import StoryScene\nclass PaperStory(StoryScene):\n    pass\n"
    assert scene_code.extract_python_block(raw) == raw.strip()


def test_cheat_sheet_names_every_public_story_scene_method():
    tree = ast.parse(Path(story_scene.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "StoryScene")
    public = {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and n.name != "construct"}
    assert public == {"fit", "label", "counter", "freeze", "clear_stage", "hold"}
    for name in public:
        assert f"self.{name}(" in scene_code.STORY_SCENE_API


def test_prompt_lists_each_beat_with_its_duration_and_budget():
    board = Storyboard(**GOLDEN_BOARD)
    prompt = scene_code.build_scene_code_prompt(board, [6.0, 7.0, 7.0, 6.0, 8.0], None, None)
    assert "beat_1" in prompt and "6.0 s" in prompt and "5.7 s" in prompt
    assert board.beats[0].visual in prompt


def test_write_scene_code_fix_round_includes_previous_source_and_feedback():
    board = Storyboard(**GOLDEN_BOARD)
    seen = []

    def complete(model, messages):
        seen.append(messages)
        return "```python\n" + GOLDEN_SCENE + "\n```"

    source = scene_code.write_scene_code(
        board, [6.0] * BEATS, complete, model="m", feedback="beat 2 ran 1.2 s over", previous_source="OLD SOURCE"
    )
    assert source == GOLDEN_SCENE.strip()
    user = seen[0][-1]["content"]
    assert "OLD SOURCE" in user and "beat 2 ran 1.2 s over" in user
