# 3Blue1Brown-Style Video Generator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every daily video looks and teaches like a 3Blue1Brown video: LaTeX on, no forced metaphor, no caption band, prompts grounded in 3b1b's own code.

**Architecture:** The pipeline shape stays the same (deep read, storyboard, narration, scene code, render, Telegram).
The plan changes what flows through it: the storyboard and scene prompts, the guard that checks generated code, the base scene the code subclasses, one extra layout round in the compose loop, and a render image with TeX Live and the CMU Serif font.
One original example scene, written in 3b1b's style, is shown to the scene coder and doubles as the golden scene for tests and the CI render.

**Tech Stack:** Python 3.12, Manim Community 0.21.0, TeX Live (minimal scheme), ffmpeg, pytest, Docker, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md`

## Global Constraints

- Every rule in the storyboard and scene prompts has a source: 3b1b's code or statements, a ruling by Daniel, or a pipeline limit. A rule without a source is not added.
- No em dash anywhere: code, comments, prompts, docs, commit messages.
- Manim Community 0.21.0 APIs only (the version in `uv.lock`).
- The security core of the guard stays: files, processes, network, introspection, `camera`, `renderer`, `file_writer`, `window`, `config`, `ImageMobject`, `SVGMobject`, `add_sound`, `interactive_embed`.
- Container images build only in GitHub Actions, never on the laptop.
- No copying from `3b1b/videos` (CC BY-NC-SA 4.0). The example scene is original.
- Commits go to the branch `3b1b-style-videos`. Merging to main, deploying, local installs, and S3 downloads each need Daniel's OK.
- Commit messages follow the repository style (`feat:`, `fix:`, `docs:`, `ci:`), plain English, no AI co-author line.

## Review Focus

1. A prompt change must not reuse stale S3 checkpoints: a replay of a paper rendered before the change must regenerate the storyboard and scene. Pinned by the fingerprint test in Task 3.
2. Camera pans leave the first board off screen on purpose: the layout check must not report objects the camera is not showing. Pinned by the off-board test in Task 2.
3. Manim adds a temporary group for `Swap` and similar animations, so the same text object can sit in two top-level families: the overlap check must not report a text overlapping itself. Pinned by the golden render test in Task 2: the example scene's `Swap` in beat 4 creates exactly this case, and the test asserts no layout warnings.
4. A layout round must never cost the day's video: if the edit fails, renders badly, or hits the deadline, the first render ships. Pinned by the fallback tests in Task 3.
5. A `Code` mobject with a file path could print a secret into a frame that goes to Telegram: only `code_string=` passes. Pinned by the guard tests in Task 1.

---

## File Map

| File | Change | Responsibility |
|---|---|---|
| `src/agentlab/scene_code.py` | Modify | Guard rules; scene prompt; scene API text |
| `src/agentlab/scene_coder_example.py` | Create | The example scene in 3b1b style: shown in the prompt, used as the golden scene |
| `src/agentlab/story_scene.py` | Modify | Base scene: 3b1b look, full-frame stage, no captions, layout audit |
| `src/agentlab/story_video.py` | Modify | Compose loop: layout round with fallback; prompt-aware fingerprints; saturation |
| `src/agentlab/video_render.py` | Modify | `mux_final` saturation option |
| `src/agentlab/storyboard.py` | Modify | Storyboard prompt |
| `tests/test_scene_code.py`, `tests/test_story_scene.py`, `tests/test_story_video.py`, `tests/test_storyboard.py`, `tests/test_video_render.py` | Modify | Tests |
| `tests/fixtures/paper_story_golden.py` | Delete | Replaced by the example scene |
| `Dockerfile.video`, `docker/texlive-profile.txt` | Modify, Create | TeX Live, CMU fonts, render tests in the test stage |
| `.github/workflows/check-images.yml` | Create | Build both images to their test stage on pull requests, no AWS, no push |
| `docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md` | Modify | Record the decisions this plan adds |

---

### Task 1: Guard allows LaTeX, helper classes, `Code(code_string=...)`, and more numpy

**Files:**
- Modify: `src/agentlab/scene_code.py` (`ALLOWED_NUMPY_MEMBERS`, `ALLOWED_NUMPY_NAMESPACES`, `FORBIDDEN_NAMES`, `check_scene_code`)
- Test: `tests/test_scene_code.py`

**Interfaces:**
- Produces: `check_scene_code(source: str, beat_count: int) -> list[str]` (same signature). New finding texts: `"Code takes code_string=... only, never a file path"`, `"helper class {name} must not be a scene; only PaperStory subclasses StoryScene"`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_scene_code.py`, remove the three LaTeX rows (`MathTex`, `DecimalNumber`, `BarChart`) from `test_guard_flags_forbidden_things`, delete `test_guard_flags_include_numbers_true`, and add:

```python
@pytest.mark.parametrize(
    "snippet",
    [
        "from manim import MathTex, DecimalNumber, Matrix, BarChart, TransformMatchingTex, Title\n",
        "from manim import Axes\nAXES = Axes(x_range=[0, 1], axis_config={'include_numbers': True})\n",
        "from manim import Axes\nAXES = Axes(x_range=[0, 1]).add_coordinates()\n",
        "from manim import Code\nSNIPPET = Code(code_string='x = 1', language='python')\n",
        "import numpy as np\nNOISE = np.random.uniform(0, 1, 5) + np.tanh(np.eye(5)).sum()\n",
        "from manim import VGroup\n\n\nclass Row(VGroup):\n    def __init__(self):\n        super().__init__()\n",
    ],
    ids=["latex", "include-numbers", "coordinates", "code-string", "numpy-random", "helper-class"],
)
def test_guard_allows_latex_code_strings_numpy_random_and_helper_classes(snippet):
    assert scene_code.check_scene_code(snippet + GOLDEN_SCENE, BEATS) == []


@pytest.mark.parametrize(
    "snippet, needle",
    [
        ("w = getattr(1, 'real')\n", "getattr"),
        ("from manim import ImageMobject\n", "ImageMobject"),
        ("from manim import SVGMobject\n", "SVGMobject"),
        ("from manim import Code\nC = Code('secrets.txt')\n", "Code takes code_string"),
        ("from manim import Code\nC = Code(code_file='secrets.txt')\n", "Code takes code_string"),
        ("import numpy as np\nD = np.load('x.npy')\n", "np.load"),
        ("import numpy as np\nB = np.random.bit_generator\n", "np.random.bit_generator"),
    ],
)
def test_guard_still_blocks_files_secrets_and_media(snippet, needle):
    findings = scene_code.check_scene_code(snippet + GOLDEN_SCENE, BEATS)
    assert any(needle in f for f in findings), findings


def test_guard_flags_a_second_scene_class():
    source = GOLDEN_SCENE + "\n\nclass Other(StoryScene):\n    pass\n"
    findings = scene_code.check_scene_code(source, BEATS)
    assert any("helper class Other must not be a scene" in f for f in findings), findings
```

- [ ] **Step 2: Run them to see them fail.** Run: `uv run pytest tests/test_scene_code.py -q -k "allows_latex or still_blocks or second_scene"`. Expected: FAIL (LaTeX names and `super` are still forbidden).

- [ ] **Step 3: Implement.** In `src/agentlab/scene_code.py`:

```python
# After the existing ALLOWED_NUMPY_MEMBERS literal, extend it:
ALLOWED_NUMPY_MEMBERS = ALLOWED_NUMPY_MEMBERS | frozenset(
    {
        # Added 2026-10-05: maths that 3Blue1Brown-style scenes use
        # (docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md).
        "convolve",
        "cosh",
        "diag",
        "exp2",
        "eye",
        "flip",
        "histogram",
        "identity",
        "log2",
        "meshgrid",
        "mod",
        "polyfit",
        "polyval",
        "repeat",
        "roll",
        "sinh",
        "tanh",
        "tile",
        "transpose",
    }
)

ALLOWED_NUMPY_NAMESPACES = {
    "linalg": frozenset({"det", "eig", "eigh", "inv", "norm", "solve"}),
    "random": frozenset(
        {"choice", "default_rng", "normal", "randint", "random", "seed", "uniform"}
    ),
}
```

In `FORBIDDEN_NAMES`, delete `"super"`, delete the whole `# anything that needs LaTeX` group (`Tex` through `get_tex`), and delete `"Code"` from the media group, so the media group reads:

```python
        # media and files
        "ImageMobject",
        "SVGMobject",
        "add_sound",
        "interactive_embed",
```

Add next to `_BEAT_RE`:

```python
SCENE_BASES = frozenset({BASE_CLASS, SCENE_CLASS, "Scene", "ThreeDScene", "MovingCameraScene"})


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None
```

In `check_scene_code`, delete the two `include_numbers` branches (the `ast.keyword` branch and the `ast.Dict` branch), and add this branch to the `for node in ast.walk(tree)` chain:

```python
        elif isinstance(node, ast.Call) and _call_name(node) == "Code":
            if (
                node.args
                or not any(k.arg == "code_string" for k in node.keywords)
                or any(k.arg in (None, "code_file") for k in node.keywords)
            ):
                findings.append("Code takes code_string=... only, never a file path")
```

Replace the class check (from `classes = [...]` to `cls = classes[0]`) with:

```python
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    scenes = [c for c in classes if c.name == SCENE_CLASS]
    if len(scenes) != 1:
        findings.append(f"exactly one top-level class named {SCENE_CLASS} is required")
        return _dedup(findings)
    cls = scenes[0]
    for helper in classes:
        if helper is not cls and any(
            isinstance(base, ast.Name) and base.id in SCENE_BASES for base in helper.bases
        ):
            findings.append(
                f"helper class {helper.name} must not be a scene; "
                f"only {SCENE_CLASS} subclasses {BASE_CLASS}"
            )
```

- [ ] **Step 4: Run the guard tests.** Run: `uv run pytest tests/test_scene_code.py -q`. Expected: the new tests PASS. Prompt tests that still name the old prompt fail until Task 5; note them and move on.

- [ ] **Step 5: Commit.** `git add src/agentlab/scene_code.py tests/test_scene_code.py && git commit -m "feat: the scene guard allows LaTeX, helper classes, Code strings, and more numpy"`

---

### Task 2: Base scene with the 3b1b look, a full-frame stage, and a screen-space layout audit

**Files:**
- Modify: `src/agentlab/story_scene.py` (constants, layout helpers, `StoryScene`)
- Modify: `src/agentlab/story_video.py` (the spec no longer carries captions)
- Test: `tests/test_story_scene.py`

**Interfaces:**
- Produces (pure, importable without manim): `camera_is_flat(phi, theta, gamma) -> bool`; `to_screen(box, center_x=0.0, center_y=0.0, zoom=1.0) -> box`; `layout_problems(boxes, texts=()) -> list[str]`; `layout_warning(beat, boxes, texts=()) -> str | None`. A box is `(name, left, right, bottom, top)`.
- Produces: palette constants `BACKGROUND, FONT, BLUE, BLUE_E, TEAL, GREEN, YELLOW, GOLD, RED, MAROON, MAROON_B, PURPLE, PINK, ORANGE, GREY_A, GREY_B, GREY_C, GREY_D, GREY_E, GREY_BROWN, WHITE, BLACK`; stage constants `STAGE_TOP = 3.5`, `STAGE_BOTTOM = -3.5`, `STAGE_RIGHT = 6.61`, `STAGE_LEFT = -6.61`.
- Produces: the timing JSON gains `"layout_skipped": [beat numbers]`. The scene spec is `{"storyboard": ..., "durations": [...]}`, with no `"captions"`.
- Removes: `counter`, `freeze`, `_swap_caption`, `ACCENT`, every `CAPTION_*` constant.

- [ ] **Step 1: Write the failing tests.** In `tests/test_story_scene.py`, delete the three caption tests (`test_layout_problems_catches_the_caption_band`, `test_layout_problems_allows_a_box_resting_above_the_caption`, `test_layout_problems_ignores_the_caption_band_before_a_caption_exists`), drop the `caption_top=-2.0` argument from the remaining calls, and add:

```python
def test_stage_is_the_full_frame_minus_3b1b_edge_buffer():
    assert story_scene.STAGE_TOP == 3.5 and story_scene.STAGE_BOTTOM == -3.5
    assert story_scene.STAGE_RIGHT == 6.61 and story_scene.STAGE_LEFT == -6.61
    assert story_scene.BACKGROUND == "#000000"
    assert story_scene.YELLOW == "#FFFF00"


def test_layout_problems_reports_text_that_overlaps_text():
    texts = [("'Query'", -1.0, 1.0, 0.0, 0.5), ("'Key'", 0.0, 2.0, 0.1, 0.6)]
    assert story_scene.layout_problems([], texts) == ["'Query' overlaps 'Key'"]


def test_layout_problems_ignores_a_sliver_of_overlap():
    texts = [("'a'", 0.0, 1.0, 0.0, 1.0), ("'b'", 0.95, 2.0, 0.0, 1.0)]
    assert story_scene.layout_problems([], texts) == []


def test_layout_problems_skips_the_board_the_camera_is_not_showing():
    """After a pan, the first board sits wholly off screen on purpose."""
    boxes = [("title", -18.0, -10.0, 3.0, 3.5)]
    texts = [("'a'", -18.0, -17.0, 0.0, 1.0), ("'b'", -18.0, -17.0, 0.0, 1.0)]
    assert story_scene.layout_problems(boxes, texts) == []


def test_to_screen_applies_a_pan_and_a_zoom():
    box = ("grid", 12.0, 16.0, -1.0, 1.0)
    assert story_scene.to_screen(box, 14.0, 0.0, 1.0) == ("grid", -2.0, 2.0, -1.0, 1.0)
    assert story_scene.to_screen(box, 14.0, 0.0, 0.5) == ("grid", -1.0, 1.0, -0.5, 0.5)


def test_camera_is_flat_until_it_tilts():
    import math

    assert story_scene.camera_is_flat(0.0, -math.pi / 2, 0.0)
    assert not story_scene.camera_is_flat(70 * math.pi / 180, -math.pi / 2, 0.0)
```

In the three render tests, drop `"captions"` from every spec dict and every `captions = [...]` line. Replace `CAMERA_SCENE` and the assertions of `test_camera_moves_render_and_layout_problems_only_warn` with:

```python
CAMERA_SCENE = '''# Visual direction: A sphere under a tilted camera, then a flat board with a square half off the left edge.
from manim import DEGREES, LEFT, Create, FadeIn, Sphere, Square
from story_scene import StoryScene


class PaperStory(StoryScene):
    def beat_1(self):
        self.ball = Sphere(radius=1.0, resolution=(8, 8))
        self.play(Create(self.ball), run_time=0.5)
        self.move_camera(phi=70 * DEGREES, theta=-45 * DEGREES, zoom=0.8, run_time=1.0)

    def beat_2(self):
        self.set_camera_orientation(phi=0, theta=-90 * DEGREES, zoom=1)
        self.lost = Square(side_length=1.0).shift(LEFT * 6.8)
        self.play(FadeIn(self.lost), run_time=0.5)

    def beat_3(self):
        self.clear_stage()
        self.hold(2.5)
'''
```

```python
    assert timing["beats"][2]["overrun"] > 0.5
    assert timing["layout_skipped"] == [1]
    assert len(timing["layout_warnings"]) == 1
    assert timing["layout_warnings"][0].startswith("beat 2 layout:")
    assert "left edge" in timing["layout_warnings"][0]
```

- [ ] **Step 2: Run them to see them fail.** Run: `uv run pytest tests/test_story_scene.py -q`. Expected: FAIL (`to_screen`, `camera_is_flat` missing; old stage constants).

- [ ] **Step 3: Implement the pure part.** In `src/agentlab/story_scene.py`, replace the colour and stage constants and the caption constants with:

```python
import math

# 3Blue1Brown's look: background and font from 3b1b/videos custom_config.yml,
# colours from 3b1b/manim manimlib/default_config.yml (both read 2026-10-05).
# Manim Community has the same values except YELLOW (#F7D96F there) and
# BLUE_E (#236B8E there), so generated scenes import colours from here.
BACKGROUND = "#000000"
FONT = "CMU Serif"
BLUE = "#58C4DD"
BLUE_E = "#1C758A"
TEAL = "#5CD0B3"
GREEN = "#83C167"
YELLOW = "#FFFF00"
GOLD = "#F0AC5F"
RED = "#FC6255"
MAROON = "#C55F73"
MAROON_B = "#EC92AB"
PURPLE = "#9A72AC"
PINK = "#D147BD"
ORANGE = "#FF862F"
GREY_A = "#DDDDDD"
GREY_B = "#BBBBBB"
GREY_C = "#888888"
GREY_D = "#444444"
GREY_E = "#222222"
GREY_BROWN = "#736357"
WHITE = "#FFFFFF"
BLACK = "#000000"

FRAME_HALF_WIDTH = 64 / 9
"""Half of Manim's default frame width: 16:9 at a frame height of 8."""
FRAME_HALF_HEIGHT = 4.0
EDGE_MARGIN = 0.5
"""3b1b's DEFAULT_MOBJECT_TO_EDGE_BUFF: content stays this far inside."""

STAGE_TOP = FRAME_HALF_HEIGHT - EDGE_MARGIN
STAGE_BOTTOM = -STAGE_TOP
STAGE_RIGHT = round(FRAME_HALF_WIDTH - EDGE_MARGIN, 2)
STAGE_LEFT = -STAGE_RIGHT
STAGE_WIDTH = STAGE_RIGHT - STAGE_LEFT
STAGE_HEIGHT = STAGE_TOP - STAGE_BOTTOM
```

Replace `CAPTION_CLEARANCE`, `layout_problems`, and `layout_warning` with:

```python
OVERLAP_SHARE = 0.10
"""Two text boxes overlap when the shared area passes this share of the
smaller box, so text that only touches stays quiet."""

FLAT_TOLERANCE = 1e-3


def camera_is_flat(phi: float, theta: float, gamma: float) -> bool:
    """True while the camera looks straight at the stage, as it starts.
    Pans and zooms keep it flat; a tilt or a roll does not."""
    return (
        abs(phi) < FLAT_TOLERANCE
        and abs(theta + math.pi / 2) < FLAT_TOLERANCE
        and abs(gamma) < FLAT_TOLERANCE
    )


def to_screen(box, center_x: float = 0.0, center_y: float = 0.0, zoom: float = 1.0):
    """Map a world box to screen units for a flat camera: a pan moves the
    frame center and a zoom scales distances from it."""
    name, left, right, bottom, top = box
    return (
        name,
        (left - center_x) * zoom,
        (right - center_x) * zoom,
        (bottom - center_y) * zoom,
        (top - center_y) * zoom,
    )


def _on_screen(box) -> bool:
    _, left, right, bottom, top = box
    return (
        right > -FRAME_HALF_WIDTH
        and left < FRAME_HALF_WIDTH
        and top > -FRAME_HALF_HEIGHT
        and bottom < FRAME_HALF_HEIGHT
    )


def _area(box) -> float:
    return max(box[2] - box[1], 0.0) * max(box[4] - box[3], 0.0)


def _overlap_area(first, second) -> float:
    width = min(first[2], second[2]) - max(first[1], second[1])
    height = min(first[4], second[4]) - max(first[3], second[3])
    return width * height if width > 0 and height > 0 else 0.0


def layout_problems(boxes, texts=()) -> list[str]:
    """Name every box that crosses the frame margin and every pair of texts
    that overlap. Pure screen-space geometry; advisory, see layout_warning.

    `boxes` and `texts` are [(name, left, right, bottom, top)] in screen
    units. A box wholly outside the frame belongs to a part of the board
    the camera is not showing, so it is skipped.
    """
    problems: list[str] = []
    for box in boxes:
        name, left, right, bottom, top = box
        # A box with no extent draws nothing. ValueTracker is the one that
        # matters: it is a single point whose x coordinate IS the number it
        # stores, so a tracker running to 175 parks it far off the stage.
        if right - left < LAYOUT_TOLERANCE and top - bottom < LAYOUT_TOLERANCE:
            continue
        if not _on_screen(box):
            continue
        if left < STAGE_LEFT - LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the left edge")
        if right > STAGE_RIGHT + LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the right edge")
        if top > STAGE_TOP + LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the top edge")
        if bottom < STAGE_BOTTOM - LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the bottom edge")
    shown = [t for t in texts if _on_screen(t) and _area(t) > 0]
    for index, first in enumerate(shown):
        for second in shown[index + 1 :]:
            if _overlap_area(first, second) > OVERLAP_SHARE * min(_area(first), _area(second)):
                problems.append(f"{first[0]} overlaps {second[0]}")
    return problems


def layout_warning(beat: int, boxes, texts=()) -> str | None:
    """One line naming what crosses the frame margin or overlaps at the end
    of a beat, or None. The base class records it in the timing output and
    never fails a render on it; story_video.py spends at most one edit
    round on it."""
    problems = layout_problems(boxes, texts)
    if not problems:
        return None
    return f"beat {beat} layout: " + "; ".join(problems[:MAX_REPORTED_PROBLEMS])
```

- [ ] **Step 4: Implement the manim part.** Replace the manim import block and the class with:

```python
try:
    from manim import (
        BOLD,
        DecimalNumber,
        FadeOut,
        MarkupText,
        Paragraph,
        SingleStringMathTex,
        Text,
        ThreeDScene,
        ValueTracker,
    )

    _MANIM_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit without manim installed
    _MANIM_AVAILABLE = False


if _MANIM_AVAILABLE:
    TEXT_TYPES = (Text, MarkupText, Paragraph, SingleStringMathTex, DecimalNumber)
    """Tex and MathTex subclass SingleStringMathTex; Integer subclasses
    DecimalNumber."""

    def _label_of(mobject):
        text = getattr(mobject, "text", None) or getattr(mobject, "tex_string", None)
        return text if isinstance(text, str) and text.strip() else None

    def _describe_mobject(mobject) -> str:
        """A name the scene coder can act on: its own text, else the text of
        the first label inside it, else its shape."""
        own = _label_of(mobject)
        if own:
            return repr(" ".join(own.split())[:40])
        for child in mobject.get_family():
            text = _label_of(child)
            if text:
                return f"the group holding {repr(' '.join(text.split())[:40])}"
        shapes = [type(c).__name__ for c in mobject.submobjects]
        if shapes:
            kinds = sorted(set(shapes))
            return f"the {type(mobject).__name__} of {len(shapes)} {'/'.join(kinds[:2])}"
        return type(mobject).__name__

    def _texts_in(mobject) -> list:
        """The outermost text objects in a family: a MathTex inside a group
        counts once, its glyphs never separately."""
        if isinstance(mobject, TEXT_TYPES):
            return [mobject]
        found = []
        for sub in mobject.submobjects:
            found.extend(_texts_in(sub))
        return found

    def _visible(mobject) -> bool:
        return max(mobject.get_fill_opacity(), mobject.get_stroke_opacity()) > 0.05

    class StoryScene(ThreeDScene):
        """Subclass as PaperStory, define beat_1 .. beat_n, never override
        construct. See the API cheat-sheet in agentlab.scene_code."""

        def construct(self):
            self.camera.background_color = BACKGROUND
            Text.set_default(font=FONT)
            MarkupText.set_default(font=FONT)
            spec = load_spec()
            self.storyboard = spec["storyboard"]
            durations = spec["durations"]
            n = len(self.storyboard["beats"])
            if len(durations) != n:
                raise ValueError(f"spec has {len(durations)} durations for {n} beats")
            self._timing: list[dict] = []
            self._layout_warnings: list[str] = []
            self._layout_skipped: list[int] = []
            for i in range(n):
                method = getattr(self, f"beat_{i + 1}", None)
                if method is None:
                    raise AttributeError(f"{SCENE_CLASS} is missing beat_{i + 1}")
                start = self.time
                method()
                remaining = durations[i] - (self.time - start)
                if remaining > 0:
                    self.wait(remaining)
                shortfall = durations[i] - (self.time - start)
                if shortfall > FRAME_TIME_TOLERANCE:
                    frame_seconds = 1 / self.renderer.camera.frame_rate
                    self.wait(
                        frame_seconds + FRAME_TIME_TOLERANCE,
                        frozen_frame=True,
                    )
                self._audit_layout(i + 1)
                self._timing.append(beat_record(i + 1, start, self.time, durations[i]))
            self._write_timing()

        # -- what the generated code may call ---------------------------------

        def fit(self, mobject, max_w: float | None = None, max_h: float | None = None):
            """Shrink a mobject to the stage (or the given bounds); returns it."""
            max_w = STAGE_WIDTH if max_w is None else max_w
            max_h = STAGE_HEIGHT if max_h is None else max_h
            if mobject.width > max_w:
                mobject.scale_to_fit_width(max_w)
            if mobject.height > max_h:
                mobject.scale_to_fit_height(max_h)
            return mobject

        def label(self, text: str, size: int = 28, color=WHITE, width: int = 44, bold: bool = False):
            """A wrapped, fitted Text. Place it yourself (next_to, move_to)."""
            kwargs = {"weight": BOLD} if bold else {}
            return self.fit(Text(wrap_text(text, width), font_size=size, color=color, line_spacing=1.2, **kwargs))

        def clear_stage(self, run_time: float = 0.4):
            """Fade out everything. Value trackers draw nothing and include
            the camera's own angles, so they stay and an ambient camera
            rotation keeps turning."""
            targets = [m for m in list(self.mobjects) if not isinstance(m, ValueTracker)]
            for m in targets:
                m.clear_updaters()
            if targets:
                self.play(*[FadeOut(m) for m in targets], run_time=run_time)

        def hold(self, seconds: float):
            """Wait; use it to let a change sink in."""
            self.wait(max(float(seconds), 0.01))

        # -- owned by the base class -------------------------------------------

        def _audit_layout(self, beat: int):
            """Record text that overlaps text, or anything that crosses the
            frame margin, at the end of this beat, in screen units. A tilted
            camera turns boxes into perspective shapes, so that beat is
            recorded as skipped. Never fails the render."""
            camera = self.camera
            if not camera_is_flat(camera.get_phi(), camera.get_theta(), camera.get_gamma()):
                self._layout_skipped.append(beat)
                return
            center = camera.frame_center
            zoom = float(camera.get_zoom())
            fixed = camera.fixed_in_frame_mobjects

            def screen_box(mobject):
                box = (
                    _describe_mobject(mobject),
                    float(mobject.get_left()[0]),
                    float(mobject.get_right()[0]),
                    float(mobject.get_bottom()[1]),
                    float(mobject.get_top()[1]),
                )
                if mobject in fixed:
                    return box
                return to_screen(box, float(center[0]), float(center[1]), zoom)

            boxes, texts, seen = [], [], set()
            for mobject in self.mobjects:
                if not mobject.get_all_points().size:
                    continue
                boxes.append(screen_box(mobject))
                for text in _texts_in(mobject):
                    # Swap and similar animations leave a group holding
                    # mobjects that already sit in another family.
                    if id(text) in seen or not _visible(text):
                        continue
                    seen.add(id(text))
                    texts.append(screen_box(text))
            warning = layout_warning(beat, boxes, texts)
            if warning:
                self._layout_warnings.append(warning)

        def _write_timing(self):
            path = os.environ.get(TIMING_ENV)
            if not path:
                return
            timing = {
                "beats": self._timing,
                "total": float(self.time),
                "layout_warnings": self._layout_warnings,
                "layout_skipped": self._layout_skipped,
            }
            Path(path).write_text(json.dumps(timing, indent=1), encoding="utf-8")
```

Update the module docstring: the spec holds `storyboard` and `durations`; the timing output adds `layout_skipped`; the base class owns the 3b1b look and has no captions (Daniel, 2026-10-05).

In `src/agentlab/story_video.py`, replace the caption lines before the scene loop with:

```python
    narrations = [beat.narration for beat in storyboard.beats]
    clips = narrate(polly_client, narrations, voice_id, work_dir / "narration")
    durations = [clip.seconds for clip in clips]
    spec = {"storyboard": storyboard.model_dump(), "durations": durations}
```

- [ ] **Step 5: Run the tests.** Run: `uv run pytest tests/test_story_scene.py tests/test_story_video.py -q`. Expected: PASS for the pure layout tests. The render tests run in Task 6 and Task 8.

- [ ] **Step 6: Commit.** `git add src/agentlab/story_scene.py src/agentlab/story_video.py tests/test_story_scene.py && git commit -m "feat: the base scene takes 3Blue1Brown's look, drops captions, and audits overlapping text"`

---

### Task 3: Compose loop with one layout round, prompt-aware checkpoints, and 3b1b saturation

**Files:**
- Modify: `src/agentlab/story_video.py` (`_compose`)
- Modify: `src/agentlab/video_render.py` (`mux_final`)
- Test: `tests/test_story_video.py`, `tests/test_video_render.py`

**Interfaces:**
- Consumes: `timing["layout_warnings"]` (list of strings) from Task 2.
- Produces: `mux_final(video_path, audio_path, out_path, timeout_seconds=..., saturation: float | None = None) -> Path`; `OUTPUT_SATURATION = 1.5`; `LAYOUT_ROUND_MIN_SECONDS = MODEL_CALL_TIMEOUT + RENDER_TIMEOUT + 60`; attempt record status `"layout_round"`.

- [ ] **Step 1: Write the failing tests.** In `tests/test_story_video.py`, give `fake_mux` a `saturation=None` parameter that it stores in `state["saturation"]`, and add:

```python
def _timing_with_warning(text="beat 2 layout: 'Query' overlaps 'Key'"):
    timing = _timing()
    timing["layout_warnings"] = [text]
    return timing


def test_layout_warning_gets_one_edit_round_and_the_clean_render_ships(seams, tmp_path):
    seams["timings"] = [_timing_with_warning(), _timing()]

    result = _compose(tmp_path)

    assert result.selected_attempt == 2
    assert len(seams["edits"]) == 1
    assert "'Query' overlaps 'Key'" in seams["edits"][0][1]
    assert result.attempt_records[0]["status"] == "layout_round"


def test_layout_round_that_still_warns_ships_the_first_render(seams, tmp_path):
    seams["timings"] = [_timing_with_warning(), _timing_with_warning("beat 3 layout: x")]

    result = _compose(tmp_path)

    assert result.selected_attempt == 1
    assert len(seams["edits"]) == 1
    assert "attempt_1" in str(seams["muxed_video"])


def test_layout_round_that_fails_to_render_ships_the_first_render(seams, tmp_path):
    seams["timings"] = [_timing_with_warning()]
    seams["render_errors"] = [None, _render_error()]

    result = _compose(tmp_path)

    assert result.selected_attempt == 1
    assert result.attempts == 2


def test_no_layout_round_without_time_for_one_more_render(seams, tmp_path):
    seams["timings"] = [_timing_with_warning()]

    result = story_video.compose_story_video(
        DIGEST, PLAN, polly_client=None, voice_id="Ivy",
        complete=lambda model, messages: "",
        work_dir=tmp_path / "work", out_path=tmp_path / "out" / "video.mp4",
        story_model="s", scene_model="c",
        deadline_seconds=story_video.LAYOUT_ROUND_MIN_SECONDS - 1,
    )

    assert result.selected_attempt == 1
    assert seams["edits"] == []


def test_final_video_gets_3b1b_saturation(seams, tmp_path):
    _compose(tmp_path)
    assert seams["saturation"] == story_video.OUTPUT_SATURATION == 1.5


def test_checkpoint_fingerprints_cover_the_prompts(seams, tmp_path, monkeypatch):
    seen = []
    real = story_video.fingerprint
    monkeypatch.setattr(
        story_video, "fingerprint", lambda *parts: seen.append(parts) or real(*parts)
    )

    _compose(tmp_path)

    assert any(story_video.STORYBOARD_SYSTEM in parts for parts in seen)
    assert any(
        story_video.SCENE_CODE_SYSTEM in parts and story_video.STORY_SCENE_API in parts
        for parts in seen
    )
```

In `tests/test_video_render.py`, add:

```python
def test_mux_final_reencodes_with_saturation_only_when_asked(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        video_render,
        "run_subprocess",
        lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    video_render.mux_final(tmp_path / "v.mp4", tmp_path / "a.mp3", tmp_path / "o.mp4")
    video_render.mux_final(
        tmp_path / "v.mp4", tmp_path / "a.mp3", tmp_path / "o.mp4", saturation=1.5
    )

    assert "copy" in calls[0] and "-vf" not in calls[0]
    assert "eq=saturation=1.5" in calls[1] and "copy" not in calls[1][calls[1].index("-c:v") + 1]
```

- [ ] **Step 2: Run them to see them fail.** Run: `uv run pytest tests/test_story_video.py tests/test_video_render.py -q -k "layout or saturation or fingerprints"`. Expected: FAIL.

- [ ] **Step 3: Implement `mux_final`.** In `src/agentlab/video_render.py`:

```python
def mux_final(
    video_path: Path,
    audio_path: Path,
    out_path: Path,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    saturation: float | None = None,
) -> Path:
    """Video+audio mux. With no saturation, `-c:v copy` repackages the
    video stream untouched. With a saturation, ffmpeg's eq filter
    re-encodes it: 3Blue1Brown renders at saturation 1.5
    (3b1b/videos custom_config.yml, file_writer)."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    video_args = ["-c:v", "copy"]
    if saturation is not None:
        video_args = [
            "-vf",
            f"eq=saturation={saturation}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
        ]
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        *video_args,
        "-c:a",
        "aac",
        "-shortest",
        str(out_path),
    ]
    run_subprocess(cmd, timeout=timeout_seconds)
```

(Keep the function's existing return statement after `run_subprocess`.)

- [ ] **Step 4: Implement the loop.** In `src/agentlab/story_video.py`:

```python
from agentlab.scene_code import SCENE_CODE_SYSTEM, STORY_SCENE_API  # add to the existing import
from agentlab.storyboard import STORYBOARD_SYSTEM  # add to the existing import

OUTPUT_SATURATION = 1.5
"""3Blue1Brown's render saturation (3b1b/videos custom_config.yml)."""
LAYOUT_ROUND_MIN_SECONDS = MODEL_CALL_TIMEOUT + RENDER_TIMEOUT + 60
"""Time one layout round may need: an edit call, a render, and the mux."""


@dataclass
class _Rendered:
    video: Path
    timing: dict
    source: str
    attempt: int
```

Change the two fingerprints so a prompt change never reuses a stale checkpoint:

```python
    board_fingerprint = fingerprint(
        "storyboard", story_model, plan_fingerprint, STORYBOARD_SYSTEM
    )
    ...
    scene_fingerprint = fingerprint(
        "scene", scene_model, board_fingerprint, SCENE_CODE_SYSTEM, STORY_SCENE_API
    )
```

Move the shipping code (from `lengths = beat_lengths(timing)` to the `return StoryResult(...)`) into a nested function defined after `clips`, and pass the saturation:

```python
    def ship(chosen: _Rendered, attempts_made: int) -> StoryResult:
        lengths = beat_lengths(chosen.timing)
        audio = concat_audio(
            clips,
            work_dir / "narration.mp3",
            target_seconds=lengths,
            timeout_seconds=_remaining_timeout(started, deadline_seconds, RENDER_TIMEOUT),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        srt_path = out_path.with_suffix(".srt")
        srt_path.write_text(build_srt(clips, durations=lengths), encoding="utf-8")
        video_path = mux_final(
            chosen.video,
            audio,
            out_path,
            timeout_seconds=_remaining_timeout(started, deadline_seconds, RENDER_TIMEOUT),
            saturation=OUTPUT_SATURATION,
        )
        return StoryResult(
            video_path=Path(video_path),
            srt_path=srt_path,
            storyboard=storyboard,
            scene_source=chosen.source,
            visual_direction=visual_direction(chosen.source),
            attempts=attempts_made,
            timing=chosen.timing,
            selected_attempt=chosen.attempt,
            attempt_records=attempt_records,
        )
```

Before the loop, add `fallback: _Rendered | None = None`. At the top of the loop body, before the deadline check, add:

```python
        if fallback is not None and attempt > fallback.attempt + 1:
            # The one layout round did not ship; the first render does.
            return ship(fallback, attempt - 1)
```

In both `except _DeadlineExceeded as exc:` handlers inside the loop, ship the fallback when there is one:

```python
        except _DeadlineExceeded as exc:
            if fallback is not None:
                return ship(fallback, attempt)
            raise StoryFailed(str(exc)) from exc
```

Replace the success path (from `attempt_records.append({**record, "status": "shipped", "timing": timing})` to the end of the old return) with:

```python
        rendered = _Rendered(video, timing, source, attempt)
        warnings = timing.get("layout_warnings") or []
        if warnings and fallback is not None:
            attempt_records.append({**record, "status": "layout_still_wrong", "timing": timing})
            return ship(fallback, attempt)
        if (
            warnings
            and attempt < attempt_limit
            and deadline_seconds - (time.monotonic() - started) >= LAYOUT_ROUND_MIN_SECONDS
        ):
            fallback = rendered
            feedback = (
                "The video rendered, but the layout has problems at the end of these beats:\n- "
                + "\n- ".join(warnings)
                + "\nMove or shrink the named objects so no text overlaps other text "
                "and nothing crosses the frame margin. Keep everything else the same."
            )
            remember(feedback)
            attempt_records.append(
                {**record, "status": "layout_round", "failure": feedback, "timing": timing}
            )
            continue
        attempt_records.append({**record, "status": "shipped", "timing": timing})
        return ship(rendered, attempt)
```

After the loop, before `raise StoryFailed(...)`, add:

```python
    if fallback is not None:
        return ship(fallback, attempt_limit)
```

- [ ] **Step 5: Run the tests.** Run: `uv run pytest tests/test_story_video.py tests/test_video_render.py -q`. Expected: PASS.

- [ ] **Step 6: Commit.** `git add src/agentlab/story_video.py src/agentlab/video_render.py tests/test_story_video.py tests/test_video_render.py && git commit -m "feat: one layout round with a safe fallback, prompt-aware checkpoints, and 3b1b saturation"`

---

### Task 4: Storyboard prompt directs a 3Blue1Brown explainer

**Files:**
- Modify: `src/agentlab/storyboard.py` (module docstring, `STORYBOARD_SYSTEM`, `build_storyboard_prompt`)
- Test: `tests/test_storyboard.py`

**Interfaces:**
- Produces: the same `Storyboard` schema. `visual_focus` now names the visual approach; `mapping` is the colour key.

- [ ] **Step 1: Write the failing tests.** In `tests/test_storyboard.py`, change the rule tuple in `test_prompt_carries_digest_plan_and_the_hard_rules` to `("real mechanism", "3Blue1Brown", "title", "street-test")`, change `"substantially different"` to `"Do not reuse these visual ideas"` in `test_prompt_names_recent_visual_directions_to_avoid`, and replace `test_prompt_asks_for_one_bold_visual_metaphor` with:

```python
def test_prompt_directs_a_3b1b_explainer_without_a_forced_metaphor():
    system = sb.STORYBOARD_SYSTEM
    assert "3Blue1Brown" in system
    for gone in ("one bold visual metaphor", "Prefer creative over safe", "visually striking"):
        assert gone not in system
    for rule in (
        "concrete to abstract",
        "one colour",
        "decoration",
        "Use 3D only for spatial ideas",
        "simple definition",
        "6 to 12 beats",
        "application",
        "at most 22 words",
    ):
        assert rule in system
```

- [ ] **Step 2: Run to see it fail.** Run: `uv run pytest tests/test_storyboard.py -q`. Expected: FAIL.

- [ ] **Step 3: Implement.** Replace `STORYBOARD_SYSTEM` with:

```python
# Every rule below has a source in
# docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md (section 1).
STORYBOARD_SYSTEM = """You are the director of a short explainer video in the style of 3Blue1Brown.
The viewer is Daniel. He must understand the paper's mechanism on the first watch.
Clear beats clever.

How 3Blue1Brown explains:
- Start from the visuals. Let the explanation form around what the viewer sees.
- Go from concrete to abstract. Show one concrete example before the general rule.
- Keep one or two examples front and centre for the whole video.
- Show the real thing: the actual objects, a graph, an equation, a geometric picture, or a diagram of the real parts. Use a metaphor only when it explains better than the real object.
- Give each key concept one colour, and keep that colour in every formula and picture for the whole video. Use two to four concept colours in a scene.
- Build a few objects early and keep them on screen. Change them from beat to beat: an equation rearranges, a curve shifts, a new symbol grows out of a copy of an old one. Do not restart the picture in every beat.
- Nothing is on screen for decoration. Every motion explains something.
- Use 3D only for spatial ideas: vector or embedding spaces, surfaces, layers stacked in depth.
- Formulas are welcome when they carry the mechanism. Show the picture first, then the formula that names it.

The narration explains the paper in plain words and may refer to what is on screen.
Start with the exact paper title and one simple definition of the main idea.
Then show a concrete problem from the paper, the real mechanism through cause and effect,
one grounded result, one practical application or implication, one limit, and the street-test question.
If the application is your inference rather than the paper's claim, say that plainly.

Write 6 to 12 beats with this exact role order:
1. title
2. problem
3. mechanism beats, as many as the mechanism needs
4. result
5. application
6. limit
7. question

Each beat has role, narration, visual, and on_screen_text.
The visual says what the viewer sees and how it changes from the previous beat.
Narration has one or two short sentences. Each sentence has at most 22 words.
Use common words. Define a necessary technical term before using it.
Never use an em dash.

The first beat's on_screen_text must contain the exact storyboard title.
The first beat's narration must contain simple_definition exactly.
Use at most two short on-screen labels per beat.
Every number must occur in the digest or scene plan.
Everything must be drawable in Manim: text, LaTeX formulas, shapes, graphs, axes, number lines, matrices, 3D surfaces, and camera moves.
Nothing comes from image files.

Output exactly one fenced json block with keys title, simple_definition, visual_focus,
mapping, and beats. visual_focus names the visual approach in one sentence. mapping is the
colour key: a list of two to eight objects with paper_term (the paper's own name for a
concept) and visual (its colour and how it appears on screen, for example "yellow, an
arrow from the origin").
Nothing can follow the json block."""
```

In `build_storyboard_prompt`, replace the recent-directions sentence and the closing instruction:

```python
    if recent_visual_directions:
        recent = (
            "\n\nRecent visual directions:\n- "
            + "\n- ".join(recent_visual_directions)
            + "\nDo not reuse these visual ideas, and keep the same 3Blue1Brown style."
        )
    ...
        "Explain this paper the way 3Blue1Brown would, starting from its real "
        "mechanism. Start with the title and a simple definition. End with the result, "
        "application, limit, and street-test question. Return the fenced json "
        "storyboard." + recent
```

Update the module docstring to "Turn a grounded paper plan into a 3Blue1Brown-style explanation" and drop the metaphor sentence.

- [ ] **Step 4: Run.** Run: `uv run pytest tests/test_storyboard.py -q`. Expected: PASS.

- [ ] **Step 5: Commit.** `git add src/agentlab/storyboard.py tests/test_storyboard.py && git commit -m "feat: the storyboard prompt directs a 3Blue1Brown explainer instead of one bold metaphor"`

---

### Task 5: Scene prompt, API text, translation table, and the example scene

**Files:**
- Create: `src/agentlab/scene_coder_example.py`
- Modify: `src/agentlab/scene_code.py` (`STORY_SCENE_API`, `SCENE_CODE_SYSTEM`, new `SCENE_CODER_EXAMPLE`)
- Modify: `tests/test_scene_code.py`, `tests/test_story_scene.py`, `tests/test_story_video.py` (golden scene path)
- Delete: `tests/fixtures/paper_story_golden.py`

**Interfaces:**
- Consumes: Task 1 guard (LaTeX, `Code`), Task 2 palette names (`from story_scene import BLUE, GREEN, GREY_B, RED, YELLOW, StoryScene`).
- Produces: `SCENE_CODER_EXAMPLE: str` (the example file's text, embedded at the end of `SCENE_CODE_SYSTEM`). The example has 8 beat methods, the same count as `tests/fixtures/storyboard_golden.json`.

- [ ] **Step 1: Create the example scene** `src/agentlab/scene_coder_example.py` with exactly this content:

```python
# Visual direction: One row of number tiles stays on the board while a yellow comparator weighs pairs, the tiles swap into a green sorted row, and the camera pans to the results and pulls back to the limit.

from manim import (
    DOWN,
    LEFT,
    ORIGIN,
    RIGHT,
    UP,
    Brace,
    Circumscribe,
    Create,
    DecimalNumber,
    FadeIn,
    FadeOut,
    Line,
    MathTex,
    NumberLine,
    ReplacementTransform,
    RoundedRectangle,
    Square,
    SurroundingRectangle,
    Swap,
    Text,
    TransformMatchingTex,
    ValueTracker,
    VGroup,
    Write,
    always_redraw,
)
from story_scene import BLUE, GREEN, GREY_B, RED, YELLOW, StoryScene

# The colour key: one colour per concept for the whole video.
ITEM = BLUE
COMPARATOR = YELLOW
SORTED = GREEN
UNTESTED = RED

# Illustrative tile values: the paper reports results, not its lists.
VALUES = [7, 3, 9, 1, 5]


def make_tile(value):
    box = RoundedRectangle(
        width=0.9,
        height=0.9,
        corner_radius=0.12,
        stroke_color=ITEM,
        stroke_width=3,
        fill_color=ITEM,
        fill_opacity=0.15,
    )
    return VGroup(box, MathTex(str(value), font_size=44).move_to(box))


class PaperStory(StoryScene):
    def beat_1(self):
        # The title, then the one concrete example the video keeps on screen.
        self.title = Text("SortNet sorts by comparing", font_size=60).to_edge(UP)
        self.values = list(VALUES)
        self.tiles = [make_tile(value) for value in self.values]
        self.row = VGroup(*self.tiles).arrange(RIGHT, buff=0.3).shift(0.8 * UP)
        self.play(Write(self.title))
        self.play(FadeIn(self.row, shift=0.3 * UP, lag_ratio=0.15))
        self.hold(0.6)

    def beat_2(self):
        # The problem: an order stored for one list does not fit a new list.
        stored = MathTex("2", "8", "4", "6", "0", font_size=44, color=GREY_B)
        stored.arrange(RIGHT, buff=0.62)
        label = Text("stored list", font_size=30, color=GREY_B)
        memory = VGroup(label, stored).arrange(RIGHT, buff=0.5).shift(1.7 * DOWN)
        mismatch = SurroundingRectangle(stored, color=UNTESTED, buff=0.15)
        self.play(FadeIn(memory, shift=0.2 * UP))
        self.play(Create(mismatch))
        self.play(FadeOut(memory), FadeOut(mismatch))

    def beat_3(self):
        # The real part: a comparator that reads two items at a time.
        box = RoundedRectangle(
            width=3.2,
            height=1.4,
            corner_radius=0.2,
            stroke_color=COMPARATOR,
            stroke_width=3,
        ).shift(1.7 * DOWN)
        name = Text("comparator", font_size=30, color=COMPARATOR)
        name.next_to(box, DOWN, buff=0.2)
        self.comparator = VGroup(box, name)
        self.rule = MathTex("x_i", "<", "x_j", font_size=48).move_to(box)
        self.rule[0].set_color(ITEM)
        self.rule[2].set_color(ITEM)
        self.play(FadeIn(self.comparator, shift=0.2 * UP))
        # The two symbols grow out of copies of the first two numbers, so the
        # viewer sees where they come from.
        self.play(
            ReplacementTransform(self.tiles[0][1].copy(), self.rule[0]),
            ReplacementTransform(self.tiles[1][1].copy(), self.rule[2]),
            FadeIn(self.rule[1]),
            run_time=1.5,
        )
        # Keep the formula as one object for the transforms that follow.
        self.remove(*self.rule)
        self.add(self.rule)

    def beat_4(self):
        # Compare: 7 is not less than 3, so the rule flips and the tiles swap.
        flipped = MathTex("x_j", "<", "x_i", font_size=48).move_to(self.rule)
        flipped[0].set_color(ITEM)
        flipped[2].set_color(ITEM)
        self.play(TransformMatchingTex(self.rule, flipped))
        self.rule = flipped
        self.play(Swap(self.tiles[0], self.tiles[1]))
        self.tiles[0], self.tiles[1] = self.tiles[1], self.tiles[0]
        self.values[0], self.values[1] = self.values[1], self.values[0]

    def beat_5(self):
        # The same comparison repeats until no pair is out of order.
        slots = [tile.get_center() for tile in self.tiles]
        ranked = [
            tile
            for _, tile in sorted(
                zip(self.values, self.tiles), key=lambda pair: pair[0]
            )
        ]
        self.play(
            *(tile.animate.move_to(slot) for tile, slot in zip(ranked, slots)),
            run_time=2,
        )
        self.play(
            *(
                tile[0].animate.set_stroke(SORTED).set_fill(SORTED, opacity=0.15)
                for tile in ranked
            ),
            Circumscribe(self.comparator[0], color=COMPARATOR),
        )
        self.tiles = ranked

    def beat_6(self):
        # The result gets its own part of the board: 200 lists, all sorted.
        board = 14 * RIGHT
        self.lists = VGroup(
            *(
                Square(side_length=0.22, stroke_width=1, stroke_color=GREY_B)
                for _ in range(200)
            )
        )
        self.lists.arrange_in_grid(rows=10, cols=20, buff=0.06)
        self.lists.move_to(board + 0.3 * UP)
        self.move_camera(
            frame_center=board,
            added_anims=[FadeIn(self.lists, lag_ratio=0.01)],
            run_time=1.5,
        )
        percent = MathTex(r"100\%", font_size=60, color=SORTED)
        percent.next_to(self.lists, UP, buff=0.3)
        brace = Brace(self.lists, DOWN, color=GREY_B)
        held_out = brace.get_text("200 held-out lists")
        self.play(
            self.lists.animate.set_fill(SORTED, opacity=0.8),
            FadeIn(percent, shift=0.2 * UP),
            run_time=1.2,
        )
        self.play(FadeIn(brace), Write(held_out))
        # 12 comparisons on average, as a number that counts up.
        caption = Text("comparisons on average", font_size=26, color=COMPARATOR)
        caption.next_to(held_out, DOWN, buff=0.35).shift(0.4 * RIGHT)
        self.count = ValueTracker(0)
        number = always_redraw(
            lambda: DecimalNumber(
                self.count.get_value(),
                num_decimal_places=0,
                font_size=40,
                color=COMPARATOR,
            ).next_to(caption, LEFT, buff=0.2)
        )
        self.add(number)
        self.play(FadeIn(caption), self.count.animate.set_value(12), run_time=1.3)

    def beat_7(self):
        # The limit: list lengths past 50 were never tested.
        self.move_camera(frame_center=7 * RIGHT + 1.2 * DOWN, zoom=0.5, run_time=1.5)
        line = NumberLine(
            x_range=[0, 80, 10], length=24, include_numbers=True, font_size=60
        ).move_to(7 * RIGHT + 5.2 * DOWN)
        tested = Line(line.n2p(0), line.n2p(50), color=SORTED, stroke_width=10)
        untested = Line(line.n2p(50), line.n2p(80), color=UNTESTED, stroke_width=10)
        name = Text("list length", font_size=56, color=GREY_B)
        name.next_to(line, DOWN, buff=0.4)
        note = Text("not tested", font_size=56, color=UNTESTED)
        note.next_to(untested, UP, buff=0.4)
        self.play(Create(line), FadeIn(name), run_time=1)
        self.play(Create(tested), run_time=0.8)
        self.play(Create(untested), FadeIn(note, shift=0.2 * UP))

    def beat_8(self):
        # The street-test question, back on the first board.
        self.move_camera(frame_center=ORIGIN, zoom=1, run_time=1.5)
        question = Text(
            "Would a pairwise comparator beat your sort step?", font_size=32
        ).to_edge(DOWN)
        self.play(self.row.animate.set_opacity(0.3), FadeIn(question, shift=0.2 * UP))
        self.play(Circumscribe(self.comparator, color=COMPARATOR), run_time=1.2)
```

- [ ] **Step 2: Point the tests at it.** In the three test files, replace the golden fixture path with the example:

```python
GOLDEN_SCENE = (Path(scene_code.__file__).with_name("scene_coder_example.py")).read_text(
    encoding="utf-8"
)
```

(`tests/test_story_scene.py` keeps a `Path`: `GOLDEN_SCENE = Path(story_scene.__file__).with_name("scene_coder_example.py")`.) Delete `tests/fixtures/paper_story_golden.py`. In `tests/test_scene_code.py`:
- `test_visual_direction_reads_generated_scene_concept`: `startswith("One row of number tiles")`.
- `test_guard_requires_visual_direction_comment`: `source = GOLDEN_SCENE.split("\n", 1)[1]`.
- The public API set becomes `{"fit", "label", "clear_stage", "hold"}`.
- `test_prompt_opens_with_daniels_brief_verbatim`: `startswith("Animate it the way 3Blue1Brown does: clean, smooth, and every motion explains something.\n")`.
- In `test_prompt_keeps_only_hard_technical_facts`: `"ThreeDAxes without labels"` becomes `"ThreeDAxes"`, `"The palette is free"` becomes `"the 3Blue1Brown palette"`, and add `"Invent the visuals"` and `"No LaTeX"` to the old-rule tuple.

Add:

```python
def test_prompt_embeds_the_example_scene_as_style_only():
    system = scene_code.SCENE_CODE_SYSTEM
    assert scene_code.SCENE_CODER_EXAMPLE in system
    assert "Copy its style, never its content." in system


def test_prompt_translates_3b1b_manim_idioms():
    system = scene_code.SCENE_CODE_SYSTEM
    for theirs, ours in (
        ("ShowCreation", "Create"),
        ("TexText", "Tex"),
        ("t2c=", "tex_to_color_map="),
        ("frame.reorient(0, 0, 0, center, height)", "self.move_camera(frame_center=center, zoom=8 / height)"),
        ("set_backstroke(BLACK, 5)", "set_stroke(BLACK, 5, background=True)"),
    ):
        assert theirs in system and ours in system


def test_example_scene_shows_the_3b1b_idioms():
    example = scene_code.SCENE_CODER_EXAMPLE
    for idiom in (
        "MathTex",
        "TransformMatchingTex",
        "ReplacementTransform(self.tiles[0][1].copy()",
        "lag_ratio",
        "self.move_camera(",
        "ValueTracker",
        "Brace(",
        "from story_scene import",
    ):
        assert idiom in example
```

- [ ] **Step 3: Run to see them fail.** Run: `uv run pytest tests/test_scene_code.py -q`. Expected: FAIL (old prompt).

- [ ] **Step 4: Implement the prompts.** In `src/agentlab/scene_code.py` add `from pathlib import Path` and:

```python
SCENE_CODER_EXAMPLE = (
    Path(__file__).with_name("scene_coder_example.py").read_text(encoding="utf-8")
)
"""Original scene in 3Blue1Brown's style for the fictional SortNet paper
(the deep-read prompt's example). Shown to the scene coder as style only,
and used by the tests and the CI render as the golden scene."""
```

Replace `STORY_SCENE_API` with:

```python
STORY_SCENE_API = """The base class is a ThreeDScene (already written, do not redefine \
it). It gives you:

Constants (import them from story_scene): BACKGROUND (#000000), the 3Blue1Brown palette \
BLUE, BLUE_E, TEAL, GREEN, YELLOW, GOLD, RED, MAROON, MAROON_B, PURPLE, PINK, ORANGE, \
GREY_A, GREY_B, GREY_C, GREY_D, GREY_E, GREY_BROWN, WHITE, BLACK, and the stage bounds \
STAGE_TOP (3.5), STAGE_BOTTOM (-3.5), STAGE_LEFT (-6.61), STAGE_RIGHT (6.61).

Methods:
- self.fit(mobject, max_w=None, max_h=None): shrink to the stage or the given bounds; returns the mobject.
- self.label(text, size=28, color=WHITE, width=44, bold=False): a wrapped, fitted Text.
- self.clear_stage(run_time=0.4): fade out everything. The camera stays where it is.
- self.hold(seconds): wait, to let a change sink in.

Camera and 3D (angles in radians, for example 70 * DEGREES):
- self.move_camera(phi=..., theta=..., zoom=..., frame_center=..., run_time=...): animate the camera. added_anims=[...] plays other animations at the same time. With phi and theta left alone, it pans and zooms a flat board.
- self.set_camera_orientation(phi=..., theta=..., zoom=...): set the camera at once.
- self.begin_ambient_camera_rotation(rate=0.02) and self.stop_ambient_camera_rotation(): a slow orbit that runs until you stop it. rate is radians per second.
- self.add_fixed_in_frame_mobjects(mobject): pin text or a formula to the screen, so camera moves never tilt or move it. It adds the mobject to the scene at once, so call it just before you animate the mobject in.
- 3D mobjects: Surface, Sphere, Cube, Prism, Cylinder, Line3D, Arrow3D, Dot3D, and ThreeDAxes.
- Render cost: the renderer draws every face of every 3D mobject on every frame, and the whole video must render within 10 minutes on 4 CPUs. Use 3D mobjects for a few large objects only. Draw grids, particles, and other repeated small shapes with flat Dot, Line, and Circle.
The camera starts flat, looking straight at the stage (phi=0, theta=-90 * DEGREES), and stays wherever you leave it.

The base class already sets the black background and the CMU Serif font, and pads each \
beat so it lasts at least its narration. There are no captions: the narration is audio \
only. You only write beat_1 .. beat_n."""
```

Replace `SCENE_CODE_SYSTEM` with:

```python
# Every rule below has a source in
# docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md (section 2).
SCENE_CODE_SYSTEM = (
    """Animate it the way 3Blue1Brown does: clean, smooth, and every motion explains something.

You write one Manim Community v0.21 scene file for a short research-paper video. The \
storyboard gives the facts, their order, the visual approach, and the colour key.

Look:
- The base class sets the black background and the CMU Serif font.
- Import colours from story_scene: they are 3Blue1Brown's values. Manim's own YELLOW \
and BLUE_E differ.
- Follow the storyboard's colour key: one colour per concept, the same in every formula \
and picture.
- On-screen text is short: one to eight words. Titles use font_size 60 to 72, labels 24 \
to 36.
- Put titles and equations at the top edge. Put a label next to the thing it names.
- Text over lines or grids gets a black background stroke: set_stroke(BLACK, 5, \
background=True).

Motion:
- Most animations use the default run_time of 1 second. Bigger moves take 2 to 5 \
seconds. Only a process that shows time passing runs longer. Keep the default rate_func.
- Stagger groups: FadeIn(group, lag_ratio=...), LaggedStart, or LaggedStartMap. Use a \
lag_ratio of about 0.5 for a few objects and 0.01 to 0.25 for many.
- Build objects early and transform them: ReplacementTransform, FadeTransform, \
TransformMatchingTex, MoveToTarget. Grow a new object out of a copy of an old one \
(TransformFromCopy, or ReplacementTransform of a .copy()), so the viewer sees where it \
comes from.
- Bring things in with Write, Create, GrowArrow, or FadeIn with a small shift.
- To point at something, dim the rest (set_opacity 0.25 to 0.35), then use \
SurroundingRectangle, Circumscribe, or ShowPassingFlash.
- Show continuous change with a ValueTracker and add_updater or always_redraw. Use \
DecimalNumber for a number that changes.
- Use the camera as a layout tool: build one large board and pan or zoom across it with \
self.move_camera(frame_center=..., zoom=...).
- Use 3D only for spatial ideas. Orbit slowly: 4 to 12 seconds per camera move, or \
begin_ambient_camera_rotation with a rate near 0.02. Pin 2D formulas over a 3D view with \
self.add_fixed_in_frame_mobjects.
- Nothing moves for decoration.

Math and numbers:
- Write formulas with MathTex and raw strings. Pass the parts as separate strings and \
colour them from the colour key, or use tex_to_color_map with whole symbols.
- Axes, NumberLine, NumberPlane, axis labels, include_numbers, Matrix, DecimalNumber, and \
Brace with get_text are all available.
- Draw a vector as a bracketed column of numbers and a matrix with brackets and ellipses. \
Label a large size with a Brace.
- Words and result numbers on screen come from the storyboard. You may shorten a label. \
Entries inside vectors and matrices may be illustrative values that stand for learned \
numbers; never present them as results.

If you know 3Blue1Brown's own manim (manimgl), translate it to Manim Community:
- ShowCreation becomes Create.
- Tex for math becomes MathTex. TexText becomes Tex.
- t2c={...} becomes tex_to_color_map={...}.
- TransformMatchingStrings becomes TransformMatchingTex or TransformMatchingShapes.
- FlashAround becomes Circumscribe, or ShowPassingFlash on a SurroundingRectangle.
- VFadeIn becomes FadeIn. GlowDot becomes Dot.
- frame.reorient(0, 0, 0, center, height) becomes self.move_camera(frame_center=center, \
zoom=8 / height).
- frame.reorient(theta, phi) becomes self.move_camera(phi=..., theta=...), in radians.
- frame.add_ambient_rotation() becomes self.begin_ambient_camera_rotation(rate=...).
- fix_in_frame() becomes self.add_fixed_in_frame_mobjects(...).
- .animate.f().set_anim_args(run_time=2) becomes .animate(run_time=2).f().
- set_backstroke(BLACK, 5) becomes set_stroke(BLACK, 5, background=True).

Hard technical facts:
- The file starts with one comment line, `# Visual direction: ...`, that names the \
visual approach this file implements.
- Exactly one scene class, `class PaperStory(StoryScene):`, with one method per beat, \
beat_1 to beat_n, n equal to the storyboard's beat count. Do not override construct. \
Import StoryScene from story_scene. Keep objects that live across beats on self \
(self.name, never self._name). Helper functions and helper classes (for example a VGroup \
subclass) are allowed outside PaperStory, but they must not be scenes.
- No files, network, images, SVG, or sound. Code() takes code_string= only.
- Only these imports: manim, story_scene, math, random, itertools, functools, numpy, \
dataclasses, typing, colorsys. Name every imported name: no wildcard imports. From numpy \
use numeric functions and np.random. The guard rejects open, exec, eval, getattr, \
setattr, type, object, and any direct use of self.camera, self.renderer, or config.
- The whole frame is the stage. Keep content 0.5 units inside the frame edges. At the \
end of each beat, the base class reports text that overlaps other text and anything that \
crosses the frame margin.
- Aim to finish each beat's animations before its narration ends. The base class pads \
the rest with a still frame.
- The render must finish within 10 minutes at 1280x720 and 30 fps, so keep 3D meshes \
coarse (for example resolution=(16, 16)) and updaters light.
- Keep the file under 12000 tokens.

Example. The file below explains SortNet, a fictional paper, in this style. Copy its \
style, never its content.

<example_scene>
"""
    + SCENE_CODER_EXAMPLE
    + "\n</example_scene>"
)
```

- [ ] **Step 5: Run the whole suite.** Run: `uv run pytest -q`. Expected: PASS. Run `uv run ruff check src tests`. Expected: no findings.

- [ ] **Step 6: Commit.** `git add -A src/agentlab tests && git commit -m "feat: the scene coder gets 3Blue1Brown rules, a manimgl translation table, and an example scene"`

---

### Task 6: Render image with TeX Live and CMU fonts, and a pull-request image check

**Files:**
- Create: `docker/texlive-profile.txt`, `.github/workflows/check-images.yml`
- Modify: `Dockerfile.video`

- [ ] **Step 1: Create `docker/texlive-profile.txt`** (the profile Manim's own image uses):

```text
selected_scheme scheme-minimal
TEXDIR /usr/local/texlive
TEXMFCONFIG ~/.texlive/texmf-config
TEXMFHOME ~/texmf
TEXMFLOCAL /usr/local/texlive/texmf-local
TEXMFSYSCONFIG /usr/local/texlive/texmf-config
TEXMFSYSVAR /usr/local/texlive/texmf-var
TEXMFVAR ~/.texlive/texmf-var
option_doc 0
option_src 0
```

- [ ] **Step 2: Add the `tex` stage** to `Dockerfile.video`, before `FROM python:3.12-slim AS base`:

```dockerfile
# TeX Live, installed the way Manim's own image does it (ManimCommunity/manim
# docker/Dockerfile, read 2026-10-05): the minimal scheme plus the package
# list from Manim's installation docs (v0.21.0) without ctex, plus
# cm-unicode for 3Blue1Brown's CMU Serif text font
# (docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md).
FROM python:3.12-slim AS tex
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        perl \
        wget \
    && rm -rf /var/lib/apt/lists/*
COPY docker/texlive-profile.txt /tmp/texlive-profile.txt
ENV PATH="/usr/local/texlive/bin/aarch64-linux:/usr/local/texlive/bin/x86_64-linux:$PATH"
RUN wget -q -O /tmp/install-tl-unx.tar.gz https://mirror.ctan.org/systems/texlive/tlnet/install-tl-unx.tar.gz \
    && mkdir /tmp/install-tl \
    && tar -xzf /tmp/install-tl-unx.tar.gz -C /tmp/install-tl --strip-components=1 \
    && /tmp/install-tl/install-tl --profile=/tmp/texlive-profile.txt \
    && tlmgr install \
        amsmath babel-english cbfonts-fd cm-super count1to doublestroke dvisvgm \
        everysel fontspec frcursive fundus-calligra gnu-freefont jknapltx latex-bin \
        mathastext microtype multitoc physics prelim2e preview ragged2e relsize rsfs \
        setspace standalone tipa wasy wasysym xcolor xetex xkeyval \
        cm-unicode \
    && rm -rf /tmp/install-tl /tmp/install-tl-unx.tar.gz
```

- [ ] **Step 3: Use it in `base` and `final`.** Add `fontconfig` to both apt lists, and after each apt `RUN` add:

```dockerfile
COPY --from=tex /usr/local/texlive /usr/local/texlive
ENV PATH="/usr/local/texlive/bin/aarch64-linux:/usr/local/texlive/bin/x86_64-linux:$PATH"
RUN ln -s /usr/local/texlive/texmf-dist/fonts/opentype/public/cm-unicode /usr/share/fonts/cm-unicode \
    && fc-cache -f
```

In the `test` stage, after `RUN uv run pytest -q`, add:

```dockerfile
# The render tests prove TeX, the CMU font, and the example scene work in
# this image. The default run above excludes them (pyproject addopts).
RUN uv run pytest -q -m render tests/test_story_scene.py
```

Add a line to the header comment: TeX Live and CMU fonts come from the `tex` stage, for LaTeX formulas and 3Blue1Brown's text font.

- [ ] **Step 4: Create `.github/workflows/check-images.yml`:**

```yaml
name: check-images
# Builds both images up to their test stage on every pull request, with no
# AWS sign-in and no push, so a broken Dockerfile or a failing test shows
# up before a change reaches main. build-images.yml stays the only
# workflow that pushes images.
on:
  pull_request:
permissions:
  contents: read
jobs:
  test:
    # Native arm64, the same architecture as the Fargate tasks.
    runs-on: ubuntu-24.04-arm
    strategy:
      fail-fast: false
      matrix:
        dockerfile: [Dockerfile, Dockerfile.video]
    steps:
      - uses: actions/checkout@v7
      - uses: docker/setup-buildx-action@v4
      - uses: docker/build-push-action@v7
        with:
          context: .
          file: ${{ matrix.dockerfile }}
          platforms: linux/arm64
          target: test
          push: false
          cache-from: type=gha,scope=${{ matrix.dockerfile }}
          cache-to: type=gha,mode=max,scope=${{ matrix.dockerfile }}
```

- [ ] **Step 5: Check the YAML parses.** Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/check-images.yml'))"` (or `python3 -c` with PyYAML if present). Expected: no error. The image itself is only built in CI.

- [ ] **Step 6: Commit.** `git add docker Dockerfile.video .github/workflows/check-images.yml && git commit -m "ci: the video image gets TeX Live and CMU fonts, and pull requests build both images to their test stage"`

---

### Task 7: Spec records the decisions this plan adds; commit spec and plan

- [ ] **Step 1: Edit the spec.**
  - Guard section: helper classes are allowed when they are not scenes, which is why `super` is allowed.
  - Look: the final mux applies 3b1b's saturation 1.5 (his `custom_config.yml`, `file_writer`).
  - Layout section: a box wholly outside the frame is skipped (camera pans leave boards off screen on purpose); duplicate text objects are counted once; tilted beats go to `layout_skipped`.
  - Checkpoints: the storyboard and scene fingerprints include the prompts.
- [ ] **Step 2: Commit.** `git add docs/superpowers && git commit -m "docs: 3Blue1Brown-style video generator spec and plan"`

---

### Task 8: Local render check and the prompts page (needs Daniel's OK for TeX packages)

- [ ] **Step 1: Daniel installs the missing TeX pieces** (sudo, so he runs it):
  `sudo tlmgr update --self && sudo tlmgr install standalone preview doublestroke dvisvgm cm-unicode`
  Then `cp /usr/local/texlive/2026basic/texmf-dist/fonts/opentype/public/cm-unicode/*.otf ~/Library/Fonts/`.
- [ ] **Step 2: Render the example.** Run: `uv run pytest -q -m render tests/test_story_scene.py`. Expected: 3 passed. Open the example video and look at every beat.
- [ ] **Step 3: Fix what looks wrong** in the example scene; rerun Step 2.
- [ ] **Step 4: Regenerate the prompts page** with the old prompts (main) next to the new ones (branch), and open it for Daniel.

### Task 9: Quality evaluation (needs Daniel's OK for S3 downloads and about $2 of Bedrock and Polly)

- [ ] **Step 1:** Pick the three most recent production videos (one per track where possible) from the ledger.
- [ ] **Step 2:** Download their videos from S3 (the "before").
- [ ] **Step 3:** Run `uv run python scripts/story_video_for_url.py <url> out/<name>-3b1b` for each (the "after").
- [ ] **Step 4:** Build a side-by-side page of frames (before, after) and open it. Daniel judges against the spec's pass criteria.

### Task 10: Ship (needs Daniel's OK)

- [ ] **Step 1:** Push the branch and open a pull request; `check-images` must pass.
- [ ] **Step 2:** Merge; `build-images` pushes `app-<sha7>` and `video-<sha7>`.
- [ ] **Step 3:** Deploy with `scripts/deploy_ci_images.sh`; run one cloud replay with `EXPLAIN_URL`.
- [ ] **Step 4:** Watch the next 10:30 run. Roll back by deploying the previous tags if a track fails that worked before.
