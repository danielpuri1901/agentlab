"""Scene code: the coder prompt and guard for model-written Manim scenes.

The guard is a best-effort AST validator, not a sandbox.
It keeps normal generated code inside Manim, a few stdlib modules, and a
narrow numeric NumPy surface, while rejecting known file, process, network,
native-library, and introspection entry points.
The render subprocess retains the worker's filesystem, user identity, and
network access, so this check only reduces accidental misuse.
It also makes bad files fail quickly with feedback the model can act on.
"""

import ast
import math
import re
from collections.abc import Callable
from decimal import ROUND_DOWN, Decimal
from pathlib import Path

from agentlab.bedrock import FIX_ROUND
from agentlab.storyboard import Storyboard

SCENE_CLASS = "PaperStory"
BASE_CLASS = "StoryScene"
BEAT_MARGIN_SECONDS = 0.3
EDIT_MAX_TOKENS = 16000
"""Output budget of one edit call, thinking included. The first live edit
(2026-10-04, Sonnet 4.6 at high effort) used all of 8000, so a smaller cap
risks a cut-off reply and a costly whole-file rewrite."""

ALLOWED_IMPORTS = frozenset(
    {
        "manim",
        "story_scene",
        "math",
        "random",
        "itertools",
        "functools",
        "numpy",
        "dataclasses",
        "typing",
        "colorsys",
    }
)

ALLOWED_NUMPY_MEMBERS = frozenset(
    {
        "abs",
        "absolute",
        "allclose",
        "arange",
        "arccos",
        "arcsin",
        "arctan",
        "arctan2",
        "argmax",
        "argmin",
        "argsort",
        "array",
        "asarray",
        "bool_",
        "ceil",
        "clip",
        "column_stack",
        "concatenate",
        "cos",
        "cross",
        "cumprod",
        "cumsum",
        "degrees",
        "diff",
        "dot",
        "e",
        "empty",
        "exp",
        "float32",
        "float64",
        "floor",
        "full",
        "geomspace",
        "gradient",
        "hstack",
        "inf",
        "int32",
        "int64",
        "interp",
        "isclose",
        "isfinite",
        "isnan",
        "linspace",
        "log",
        "log10",
        "logspace",
        "matmul",
        "max",
        "maximum",
        "mean",
        "median",
        "min",
        "minimum",
        "nan",
        "ndarray",
        "newaxis",
        "ones",
        "outer",
        "pi",
        "power",
        "prod",
        "radians",
        "round",
        "sign",
        "sin",
        "sort",
        "sqrt",
        "square",
        "stack",
        "std",
        "sum",
        "tan",
        "unique",
        "var",
        "vstack",
        "where",
        "zeros",
    }
)

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
        {
            "RandomState",
            "choice",
            "default_rng",
            "normal",
            "randint",
            "random",
            "seed",
            "uniform",
        }
    ),
}

FORBIDDEN_NAMES = frozenset(
    {
        # files, processes, network, introspection
        "open",
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "type",
        "object",
        "dir",
        "vars",
        "breakpoint",
        "input",
        "os",
        "sys",
        "subprocess",
        "socket",
        "pathlib",
        "shutil",
        "importlib",
        "builtins",
        "__builtins__",
        "__subclasses__",
        "__globals__",
        "__dict__",
        "__class__",
        "__mro__",
        "camera",
        "renderer",
        "file_writer",
        "window",
        "config",
        # media and files
        "ImageMobject",
        "SVGMobject",
        "add_sound",
        "interactive_embed",
    }
)

_BEAT_RE = re.compile(r"^beat_(\d+)$")
SCENE_BASES = frozenset({BASE_CLASS, SCENE_CLASS, "Scene", "ThreeDScene", "MovingCameraScene"})


def _is_super_init(node: ast.Attribute) -> bool:
    """super().__init__, the one dunder a helper class needs."""
    return (
        node.attr == "__init__"
        and isinstance(node.value, ast.Call)
        and _call_name(node.value) == "super"
    )


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None
_VISUAL_DIRECTION_RE = re.compile(r"^# Visual direction:\s*(\S.*)$", re.MULTILINE)


def visual_direction(source: str) -> str:
    match = _VISUAL_DIRECTION_RE.search(source)
    return match.group(1).strip() if match else ""


def _attribute_path(node: ast.Attribute) -> tuple[str, ...] | None:
    parts = []
    current: ast.expr = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    return (current.id, *reversed(parts))


def _numpy_attribute_allowed(path: tuple[str, ...]) -> bool:
    members = path[1:]
    if len(members) == 1:
        return (
            members[0] in ALLOWED_NUMPY_MEMBERS
            or members[0] in ALLOWED_NUMPY_NAMESPACES
        )
    if len(members) == 2:
        return members[1] in ALLOWED_NUMPY_NAMESPACES.get(members[0], ())
    return False


def check_scene_code(source: str, beat_count: int) -> list[str]:
    """Return best-effort validation findings, preserving order without duplicates."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: line {exc.lineno}: {exc.msg}"]
    findings: list[str] = []
    if not visual_direction(source):
        findings.append("missing '# Visual direction: ...' scene concept comment")
    numpy_aliases = {
        alias.asname or "numpy"
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
        if alias.name == "numpy"
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    findings.append(f"import not allowed: {alias.name}")
                elif alias.name.startswith("numpy."):
                    findings.append(f"numpy submodule import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_IMPORTS:
                findings.append(f"import not allowed: from {node.module or '.'}")
            elif root == "numpy" and node.module != "numpy":
                findings.append(f"numpy submodule import not allowed: {node.module}")
            for alias in node.names:
                if alias.name == "*":
                    findings.append("wildcard imports are not allowed")
                if alias.name in FORBIDDEN_NAMES:
                    findings.append(f"forbidden name imported: {alias.name}")
                if node.module == "numpy" and alias.name not in ALLOWED_NUMPY_MEMBERS:
                    findings.append(f"numpy import not allowed: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") and not _is_super_init(node):
                findings.append(f"private attribute not allowed: {node.attr}")
            elif node.attr in FORBIDDEN_NAMES:
                findings.append(f"forbidden attribute: {node.attr}")
            path = _attribute_path(node)
            if path and path[0] in numpy_aliases and not _numpy_attribute_allowed(path):
                findings.append(f"numpy attribute not allowed: {'.'.join(path)}")
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.fullmatch(r"__[^\s]+__", node.value)
        ):
            findings.append(f"dunder name literal not allowed: {node.value}")
        elif (
            isinstance(node, ast.Call)
            and _call_name(node) == "Code"
            and (
                node.args
                or not any(k.arg == "code_string" for k in node.keywords)
                or any(k.arg in (None, "code_file") for k in node.keywords)
            )
        ):
            findings.append("Code takes code_string=... only, never a file path")
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
    if (
        len(cls.bases) != 1
        or not isinstance(cls.bases[0], ast.Name)
        or cls.bases[0].id != BASE_CLASS
    ):
        findings.append(
            f"{SCENE_CLASS} must have exactly one direct base named {BASE_CLASS}"
        )
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    if "construct" in methods:
        findings.append(
            f"{SCENE_CLASS} must not override construct; the base class owns it"
        )
    for k in range(1, beat_count + 1):
        if f"beat_{k}" not in methods:
            findings.append(
                f"missing method beat_{k} (the storyboard has {beat_count} beats)"
            )
    for name in sorted(methods):
        match = _BEAT_RE.match(name)
        if match and int(match.group(1)) > beat_count:
            findings.append(
                f"unexpected method {name}: the storyboard has only {beat_count} beats"
            )
    return _dedup(findings)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


# The render cost line is from the first live free-mode run (2026-10-04): a
# scene with about 100 Dot3D and Line3D meshes did not render within 600 s on
# Fargate; the edit to flat Dot and Line shipped.
STORY_SCENE_API = """The base class is a ThreeDScene (already written, do not redefine \
it). It gives you:

Constants (import them from story_scene): BACKGROUND (#000000), the 3Blue1Brown palette \
BLUE, BLUE_E, TEAL, GREEN, YELLOW, GOLD, RED, MAROON, MAROON_B, PURPLE, PINK, ORANGE, \
GREY_A, GREY_B, GREY_C, GREY_D, GREY_E, GREY_BROWN, WHITE, BLACK, and the stage bounds \
STAGE_TOP (3.5), STAGE_BOTTOM (-2.9), STAGE_LEFT (-6.61), STAGE_RIGHT (6.61).

Methods:
- self.fit(mobject, max_w=None, max_h=None): shrink to the stage or the given bounds; returns the mobject.
- self.label(text, size=28, color=WHITE, width=44, bold=False): a wrapped, fitted Text.
- self.clear_stage(run_time=0.4): fade out everything except the subtitle. The camera stays where it is.
- self.hold(seconds): wait, to let a change sink in.

Camera and 3D (angles in radians, for example 70 * DEGREES):
- self.move_camera(phi=..., theta=..., zoom=..., frame_center=..., run_time=...): animate the camera. added_anims=[...] plays other animations at the same time. With phi and theta left alone, it pans and zooms a flat board.
- self.set_camera_orientation(phi=..., theta=..., zoom=...): set the camera at once.
- self.begin_ambient_camera_rotation(rate=0.02) and self.stop_ambient_camera_rotation(): a slow orbit that runs until you stop it. rate is radians per second.
- self.add_fixed_in_frame_mobjects(mobject): pin text or a formula to the screen, so camera moves never tilt or move it. It adds the mobject to the scene at once, so call it just before you animate the mobject in.
- 3D mobjects: Surface, Sphere, Cube, Prism, Cylinder, Line3D, Arrow3D, Dot3D, and ThreeDAxes.
- Render cost: the renderer draws every face of every 3D mobject on every frame, and the whole video must render within 10 minutes on 4 CPUs. Use 3D mobjects for a few large objects only. Draw grids, particles, and other repeated small shapes with flat Dot, Line, and Circle.
The camera starts flat, looking straight at the stage (phi=0, theta=-90 * DEGREES), and stays wherever you leave it.

The base class already sets the black background and the CMU Serif font, shows the \
narration as subtitles in a strip below STAGE_BOTTOM, and pads each beat so it lasts at \
least its narration. You only write beat_1 .. beat_n."""


SCENE_CODER_EXAMPLE = (
    Path(__file__).with_name("scene_coder_example.py").read_text(encoding="utf-8")
)
"""Original scene in 3Blue1Brown's style for the fictional SortNet paper
(the deep-read prompt's example). Shown to the scene coder as style only,
and used by the tests and the CI render as the golden scene."""


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
- The stage is the frame above the subtitle strip: keep content between STAGE_LEFT and \
STAGE_RIGHT and between STAGE_BOTTOM and STAGE_TOP. At the end of each beat, the base \
class reports text that overlaps other text and anything that leaves the stage.
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

EDIT_FORMAT = """Fix the current file with the smallest edits that remove this failure. \
Answer only with search/replace blocks in this exact format, as many as you need:

<<<<<<< SEARCH
exact lines copied from the current file
=======
the lines that replace them
>>>>>>> REPLACE

Each SEARCH text must match the current file exactly, indentation included, and occur \
exactly once in it. Do not send the whole file."""


def _storyboard_prompt(storyboard: Storyboard, durations: list[float]) -> str:
    """The part of every scene prompt that stays the same across fix rounds."""
    _validate_durations(storyboard, durations)
    beat_lines = []
    for i, (beat, seconds) in enumerate(
        zip(storyboard.beats, durations, strict=True), start=1
    ):
        budget = _format_permitted_budget(seconds)
        labels = (
            f" On-screen text: {', '.join(beat.on_screen_text)}."
            if beat.on_screen_text
            else ""
        )
        beat_lines.append(
            f"beat_{i}: narration lasts {seconds:.1f} s. Aim for animations that total at "
            f"most {budget} s.\n  Narration: {beat.narration}\n  Visual: {beat.visual}{labels}"
        )
    storyboard_json = storyboard.model_dump_json(indent=2)
    return (
        "<storyboard_json>\n"
        f"{storyboard_json}\n"
        "</storyboard_json>\n\n"
        f"Title: {storyboard.title}\nSimple definition: {storyboard.simple_definition}\n"
        f"Visual focus: {storyboard.visual_focus}\n"
        "Mapping:\n"
        + "\n".join(f"- {m.paper_term} = {m.visual}" for m in storyboard.mapping)
        + "\n\nBeats:\n"
        + "\n".join(beat_lines)
        + "\n\n"
        + STORY_SCENE_API
    )


def build_scene_code_prompt(
    storyboard: Storyboard,
    durations: list[float],
    feedback: str | None,
    previous_source: str | None,
) -> str:
    prompt = _storyboard_prompt(storyboard, durations)
    if feedback:
        prompt += f"{FIX_ROUND} The previous attempt failed.\n\n"
        if previous_source:
            prompt += f"<previous_file>\n{previous_source}\n</previous_file>\n\n"
        prompt += f"<what_went_wrong>\n{feedback}\n</what_went_wrong>"
    prompt += (
        "\n\nWrite the complete file now: exactly one fenced python block with the "
        "whole file and nothing outside it."
    )
    return prompt


def build_edit_prompt(
    storyboard: Storyboard, durations: list[float], source: str, feedback: str
) -> str:
    return (
        _storyboard_prompt(storyboard, durations)
        + f"{FIX_ROUND} The current file failed.\n\n"
        + f"<current_file>\n{source}\n</current_file>\n\n"
        + f"<what_went_wrong>\n{feedback}\n</what_went_wrong>\n\n"
        + EDIT_FORMAT
    )


_EDIT_BLOCK_RE = re.compile(
    r"^<<<<<<< SEARCH[ \t]*\n(.*?)^=======[ \t]*\n(.*?)^>>>>>>> REPLACE[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


def apply_edits(source: str, reply: str) -> str | None:
    """Apply the reply's search/replace blocks in order with plain string
    replacement. None when the reply has no block or any SEARCH text does
    not occur exactly once at that point; the caller then asks for a whole
    new file instead."""
    blocks = _EDIT_BLOCK_RE.findall(reply or "")
    if not blocks:
        return None
    text = source.rstrip("\n") + "\n"
    for search, replace in blocks:
        if text.count(search) != 1:
            return None
        text = text.replace(search, replace, 1)
    return text


def _validate_durations(storyboard: Storyboard, durations: list[float]) -> None:
    expected = len(storyboard.beats)
    if len(durations) != expected:
        raise ValueError(
            f"durations must contain one duration per beat ({expected} required, got {len(durations)})"
        )
    for index, seconds in enumerate(durations, start=1):
        try:
            finite = math.isfinite(seconds)
        except (TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"duration for beat_{index} must be finite")
        if seconds <= BEAT_MARGIN_SECONDS:
            raise ValueError(
                f"duration for beat_{index} must be greater than {BEAT_MARGIN_SECONDS} seconds"
            )


def _format_permitted_budget(seconds: float) -> str:
    permitted = Decimal(str(seconds)) - Decimal(str(BEAT_MARGIN_SECONDS))
    conservative = permitted.quantize(Decimal("0.1"), rounding=ROUND_DOWN)
    return f"{conservative:.1f}"


_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def extract_python_block(raw: str) -> str:
    blocks = _FENCE_RE.findall(raw or "")
    if blocks:
        return blocks[-1].strip()
    return (raw or "").strip()


def write_scene_code(
    storyboard: Storyboard,
    durations: list[float],
    complete: Callable[[str, list[dict]], str],
    model: str,
    feedback: str | None = None,
    previous_source: str | None = None,
) -> str:
    messages = [
        {"role": "system", "content": SCENE_CODE_SYSTEM},
        {
            "role": "user",
            "content": build_scene_code_prompt(
                storyboard, durations, feedback, previous_source
            ),
        },
    ]
    return extract_python_block(complete(model, messages))


def edit_scene_code(
    storyboard: Storyboard,
    durations: list[float],
    source: str,
    feedback: str,
    complete: Callable[..., str],
    model: str,
) -> str | None:
    """Ask for the smallest fix to a failed file. None when the edits do not
    apply exactly."""
    messages = [
        {"role": "system", "content": SCENE_CODE_SYSTEM},
        {
            "role": "user",
            "content": build_edit_prompt(storyboard, durations, source, feedback),
        },
    ]
    return apply_edits(source, complete(model, messages, max_tokens=EDIT_MAX_TOKENS))
