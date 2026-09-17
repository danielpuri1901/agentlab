import ast
import json
from pathlib import Path

import pytest

from agentlab import scene_code, story_scene
from agentlab.storyboard import Storyboard

GOLDEN_SCENE = (Path(__file__).parent / "fixtures" / "paper_story_golden.py").read_text(
    encoding="utf-8"
)
GOLDEN_BOARD = json.loads(
    (Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(
        encoding="utf-8"
    )
)
BEATS = len(GOLDEN_BOARD["beats"])


def test_golden_scene_passes_the_guard():
    assert scene_code.check_scene_code(GOLDEN_SCENE, BEATS) == []


def test_visual_direction_reads_generated_scene_concept():
    assert scene_code.visual_direction(GOLDEN_SCENE).startswith("A crowded house")


def test_guard_requires_visual_direction_comment():
    source = GOLDEN_SCENE.replace(
        "# Visual direction: A crowded house compresses into one case while the needed item stays visible.\n",
        "",
    )

    assert (
        "visual direction"
        in " ".join(scene_code.check_scene_code(source, BEATS)).lower()
    )


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


def test_guard_blocks_indirect_dunder_access_to_builtins():
    source = GOLDEN_SCENE.replace(
        "self.hold(0.6)",
        'leak = type(self).__getattribute__(self, "__dict__")\n        self.hold(0.6)',
    )

    findings = scene_code.check_scene_code(source, BEATS)

    assert any("type" in finding for finding in findings), findings
    assert any("__getattribute__" in finding for finding in findings), findings
    assert any("__dict__" in finding for finding in findings), findings


def test_guard_rejects_wildcard_imports():
    source = GOLDEN_SCENE.replace(
        "from manim import (",
        "from manim import *\nfrom manim import (",
    )

    findings = scene_code.check_scene_code(source, BEATS)

    assert any("wildcard" in finding for finding in findings), findings


@pytest.mark.parametrize(
    ("snippet", "needle"),
    [
        ("import numpy as np\nvalues = np.fromfile('/etc/passwd')\n", "fromfile"),
        (
            "import numpy as np\nsource = np.DataSource()\nexists = source.exists('https://example.com/data')\n",
            "DataSource",
        ),
        ("from numpy import fromfile\n", "fromfile"),
        ("from numpy import DataSource\n", "DataSource"),
        ("import numpy.ctypeslib as npct\n", "numpy.ctypeslib"),
        ("from numpy.ctypeslib import load_library\n", "numpy.ctypeslib"),
        ("import numpy.lib.npyio as npio\n", "numpy.lib.npyio"),
    ],
)
def test_guard_flags_numpy_file_network_native_library_and_submodule_access(
    snippet, needle
):
    findings = scene_code.check_scene_code(snippet + GOLDEN_SCENE, BEATS)
    assert any(needle in finding for finding in findings), findings


def test_guard_allows_narrow_numeric_numpy_surface():
    source = """import numpy as np
from numpy import array, cos

""" + GOLDEN_SCENE.replace(
        "self.hold(0.6)",
        "points = array([[0.0, 1.0], [2.0, 3.0]]); "
        "weights = np.linspace(0.0, 1.0, 2); "
        "length = np.linalg.norm(points[1] - points[0]); "
        "wave = cos(weights) + np.sin(weights); "
        "self.hold(0.6)",
    )
    assert scene_code.check_scene_code(source, BEATS) == []


def test_guard_flags_include_numbers_true():
    source = GOLDEN_SCENE.replace(
        "self.hold(0.6)",
        "self.axes = Axes(x_range=[0, 1], axis_config={'include_numbers': True}); self.hold(0.6)",
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
    assert any(
        "construct" in f for f in scene_code.check_scene_code(with_construct, BEATS)
    )
    renamed = GOLDEN_SCENE.replace(
        "class PaperStory(StoryScene):", "class Other(StoryScene):"
    )
    assert any("PaperStory" in f for f in scene_code.check_scene_code(renamed, BEATS))


def test_guard_reports_syntax_errors():
    findings = scene_code.check_scene_code("def (:\n", BEATS)
    assert findings and "syntax" in findings[0]


def test_extract_python_block_takes_the_last_fenced_block():
    raw = "thinking...\n```python\nx = 1\n```\nfix:\n```python\nclass PaperStory: pass\n```\n"
    assert scene_code.extract_python_block(raw) == "class PaperStory: pass"


def test_extract_python_block_falls_back_to_raw_when_unfenced():
    raw = (
        "from story_scene import StoryScene\nclass PaperStory(StoryScene):\n    pass\n"
    )
    assert scene_code.extract_python_block(raw) == raw.strip()


def test_cheat_sheet_names_every_public_story_scene_method():
    tree = ast.parse(Path(story_scene.__file__).read_text(encoding="utf-8"))
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "StoryScene"
    )
    public = {
        n.name
        for n in cls.body
        if isinstance(n, ast.FunctionDef)
        and not n.name.startswith("_")
        and n.name != "construct"
    }
    assert public == {"fit", "label", "counter", "freeze", "clear_stage", "hold"}
    for name in public:
        assert f"self.{name}(" in scene_code.STORY_SCENE_API


def test_prompt_lists_each_beat_with_its_duration_and_budget():
    board = Storyboard(**GOLDEN_BOARD)
    prompt = scene_code.build_scene_code_prompt(board, [6.0] * BEATS, None, None)
    assert "beat_1" in prompt and "6.0 s" in prompt and "5.7 s" in prompt
    assert board.beats[0].visual in prompt


def test_prompt_includes_full_storyboard_and_visual_focus():
    board = Storyboard(**GOLDEN_BOARD)
    prompt = scene_code.build_scene_code_prompt(board, [6.0] * BEATS, None, None)
    storyboard_json = board.model_dump_json(indent=2)
    assert storyboard_json in prompt
    assert board.visual_focus in prompt


def test_prompt_requires_scene_to_fit_below_completion_limit():
    assert "under 7000 output tokens" in scene_code.SCENE_CODE_SYSTEM


def test_prompt_gives_the_scene_coder_visual_freedom():
    system = scene_code.SCENE_CODE_SYSTEM
    assert "clearest visual explanation" in system
    assert "visually compelling" in system
    assert "abstract" in system
    assert "Change the composition" in system
    assert "designed for this paper" in system
    assert "one visual change per beat" not in system
    assert "Reuse the central object" not in system


@pytest.mark.parametrize(
    ("duration", "expected_budget"),
    [(0.3, None), (0.5, "0.2"), (0.79, "0.4"), (0.8, "0.5")],
)
def test_prompt_validates_duration_margin_boundaries(duration, expected_budget):
    board = Storyboard(**GOLDEN_BOARD)
    if duration <= scene_code.BEAT_MARGIN_SECONDS:
        with pytest.raises(ValueError, match="greater than 0.3"):
            scene_code.build_scene_code_prompt(board, [duration] * BEATS, None, None)
    else:
        prompt = scene_code.build_scene_code_prompt(
            board, [duration] * BEATS, None, None
        )
        displayed = next(
            line for line in prompt.splitlines() if line.startswith("beat_1:")
        )
        assert f"at most {expected_budget} s" in displayed


@pytest.mark.parametrize("duration", [float("nan"), float("inf"), float("-inf")])
def test_prompt_rejects_non_finite_durations(duration):
    board = Storyboard(**GOLDEN_BOARD)
    with pytest.raises(ValueError, match="finite"):
        scene_code.build_scene_code_prompt(board, [duration] * BEATS, None, None)


def test_prompt_rejects_duration_count_mismatch():
    board = Storyboard(**GOLDEN_BOARD)
    with pytest.raises(ValueError, match="one duration per beat"):
        scene_code.build_scene_code_prompt(board, [6.0] * (BEATS - 1), None, None)


@pytest.mark.parametrize(
    ("declaration", "needle"),
    [
        ("class Helper:\n    pass\n", "exactly one top-level class named PaperStory"),
        (
            "class PaperStory(StoryScene):\n    pass\n\nclass Helper:\n    pass\n",
            "exactly one top-level class named PaperStory",
        ),
        (
            "class PaperStory(other.StoryScene):\n    pass\n",
            "exactly one direct base named StoryScene",
        ),
        (
            "class PaperStory(StoryScene, Helper):\n    pass\n",
            "exactly one direct base named StoryScene",
        ),
    ],
)
def test_guard_requires_exactly_one_paper_story_class_shape(declaration, needle):
    findings = scene_code.check_scene_code(declaration, BEATS)
    assert any(needle in finding for finding in findings)


def test_write_scene_code_fix_round_includes_previous_source_and_feedback():
    board = Storyboard(**GOLDEN_BOARD)
    seen = []

    def complete(model, messages):
        seen.append(messages)
        return "```python\n" + GOLDEN_SCENE + "\n```"

    source = scene_code.write_scene_code(
        board,
        [6.0] * BEATS,
        complete,
        model="m",
        feedback="beat 2 ran 1.2 s over",
        previous_source="OLD SOURCE",
    )
    assert source == GOLDEN_SCENE.strip()
    user = seen[0][-1]["content"]
    assert "OLD SOURCE" in user and "beat 2 ran 1.2 s over" in user
