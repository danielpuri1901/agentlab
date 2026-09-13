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

from agentlab.storyboard import Storyboard

SCENE_CLASS = "PaperStory"
BASE_CLASS = "StoryScene"
MAX_TOKENS = 8000
BEAT_MARGIN_SECONDS = 0.3

ALLOWED_IMPORTS = frozenset(
    {"manim", "story_scene", "math", "random", "itertools", "functools", "numpy", "dataclasses", "typing", "colorsys"}
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

ALLOWED_NUMPY_NAMESPACES = {
    "linalg": frozenset({"det", "eig", "eigh", "inv", "norm", "solve"}),
}

FORBIDDEN_NAMES = frozenset(
    {
        # files, processes, network, introspection
        "open", "exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr",
        "delattr", "vars", "breakpoint", "input", "os", "sys", "subprocess", "socket", "pathlib",
        "shutil", "importlib", "builtins", "__builtins__", "__subclasses__", "__globals__", "__dict__",
        "__class__", "__mro__", "camera",
        # anything that needs LaTeX (the video image has none)
        "Tex", "MathTex", "SingleStringMathTex", "DecimalNumber", "Integer", "Variable", "Title",
        "BulletedList", "Matrix", "IntegerMatrix", "DecimalMatrix", "MobjectMatrix", "Table", "MathTable",
        "IntegerTable", "DecimalTable", "MobjectTable", "BarChart", "TransformMatchingTex",
        "get_axis_labels", "get_x_axis_label", "get_y_axis_label", "add_coordinates", "get_text", "get_tex",
        # media and files
        "ImageMobject", "SVGMobject", "Code", "add_sound", "interactive_embed",
    }
)

_BEAT_RE = re.compile(r"^beat_(\d+)$")


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
        return members[0] in ALLOWED_NUMPY_MEMBERS or members[0] in ALLOWED_NUMPY_NAMESPACES
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
                if alias.name in FORBIDDEN_NAMES:
                    findings.append(f"forbidden name imported: {alias.name}")
                if node.module == "numpy" and alias.name not in ALLOWED_NUMPY_MEMBERS:
                    findings.append(f"numpy import not allowed: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_NAMES:
                findings.append(f"forbidden attribute: {node.attr}")
            path = _attribute_path(node)
            if path and path[0] in numpy_aliases and not _numpy_attribute_allowed(path):
                findings.append(f"numpy attribute not allowed: {'.'.join(path)}")
        elif (
            isinstance(node, ast.keyword)
            and node.arg == "include_numbers"
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ):
            findings.append("include_numbers=True needs LaTeX, which the render image does not have")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant) and key.value == "include_numbers"
                    and isinstance(value, ast.Constant) and value.value is True
                ):
                    findings.append("include_numbers=True needs LaTeX, which the render image does not have")
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    if len(classes) != 1 or classes[0].name != SCENE_CLASS:
        findings.append(f"exactly one top-level class named {SCENE_CLASS} is required")
        return _dedup(findings)
    cls = classes[0]
    if len(cls.bases) != 1 or not isinstance(cls.bases[0], ast.Name) or cls.bases[0].id != BASE_CLASS:
        findings.append(f"{SCENE_CLASS} must have exactly one direct base named {BASE_CLASS}")
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    if "construct" in methods:
        findings.append(f"{SCENE_CLASS} must not override construct; the base class owns it")
    for k in range(1, beat_count + 1):
        if f"beat_{k}" not in methods:
            findings.append(f"missing method beat_{k} (the storyboard has {beat_count} beats)")
    for name in sorted(methods):
        match = _BEAT_RE.match(name)
        if match and int(match.group(1)) > beat_count:
            findings.append(f"unexpected method {name}: the storyboard has only {beat_count} beats")
    return _dedup(findings)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


STORY_SCENE_API = """The base class (already written, do not redefine it) gives you:

Constants (import them from story_scene): BACKGROUND, ACCENT (#2f6fd6, the one accent \
colour), GOLD, GREEN, RED, GREY_A, GREY_B, GREY_C, GREY_D, WHITE, STAGE_TOP (3.6), \
STAGE_BOTTOM (-2.3), STAGE_LEFT (-6.4), STAGE_RIGHT (6.4).

Methods:
- self.fit(mobject, max_w=None, max_h=None): shrink to the stage or the given bounds; returns the mobject. Call it on every text block and every group before placing it.
- self.label(text, size=28, color=WHITE, width=44, bold=False): a wrapped, fitted Text. Place it with next_to / move_to / to_edge.
- self.counter(start, end, suffix="", size=44, color=ACCENT, decimals=0): returns (mobject, animation). Place the mobject, then self.play(animation, run_time=...) to count it up. Call self.freeze(mobject) afterwards, before any FadeOut or Transform that includes it.
- self.freeze(mobject): stop a counter updating.
- self.clear_stage(run_time=0.4): fade out everything except the caption. Use it when the mechanism changes view.
- self.hold(seconds): wait, to let a change sink in.

The base class already: sets the dark background, draws the caption for each beat in the bottom band, and pads each beat so it lasts exactly its narration. You only write beat_1 .. beat_n."""


SCENE_CODE_SYSTEM = """You write one Manim Community v0.21 scene file that animates a \
storyboard for a short paper-explainer video. The storyboard names one real mechanism and a \
list of beats; each beat has narration (already recorded, its duration is given) and a \
visual description. Your job is to draw exactly that visual, beat by beat, with clean \
motion, in the style of 3Blue1Brown: simple shapes, one accent colour, the change on the \
paper's real components is the explanation.

Contract:
- File starts with `from manim import (...)` naming only what you use, then `from \
story_scene import StoryScene` plus any constants you use from it.
- Exactly one class, `class PaperStory(StoryScene):`. Do not override construct.
- Keep the complete file under 7000 output tokens. Import only names you use.
- One method per beat, beat_1 to beat_n, n equal to the storyboard's beat count.
- Show one visual change per beat. Reuse the central object. Do not add a second chart, \
dashboard, panel, or legend beside it.
- Only draw on-screen text listed in the storyboard. Do not invent headings, labels, \
values, round numbers, or annotations. Keep each label clear of every shape and caption.
- Each beat's animations (the sum of run_time values plus any self.hold) must end at \
least 0.3 s before that beat's narration ends. The budget per beat is listed below. The \
base class pads the rest.
- Objects that persist across beats live on self (self.agent, self.evaluator, ...). Later \
beats move, recolour, or transform them; that continuity is the whole point.
- Everything stays inside the stage: x from -6.4 to 6.4, y from -2.3 to 3.6. The band \
below y = -2.3 is the caption's; never draw there. Call self.fit on every text block and \
every group.
- No LaTeX: never Tex, MathTex, DecimalNumber, Integer, Title, Variable, Matrix, Table, \
BarChart, axis labels, include_numbers=True. Numbers are Text or self.counter(...).
- No camera moves (plain Scene): zoom by scaling a group. No emoji, no images, no \
files, no network, no custom fonts, no sound.
- Only these imports: manim, story_scene, math, random, itertools, functools, numpy, \
dataclasses, typing, colorsys.
- Use real Manim vocabulary: Create for shapes, Write or FadeIn for text, \
.animate.move_to / .set_fill / .scale for state changes, Transform / ReplacementTransform \
/ FadeTransform for one thing becoming another, Indicate / Circumscribe / Flash for \
emphasis, MoveAlongPath for travel, VGroup + arrange / arrange_in_grid for layout, \
always with explicit run_time.
- Text sizes: 20 to 30 for labels, 36 to 44 for one headline number. Never more than \
about 12 words on screen at once besides the caption.
- Prefer showing the change over labelling it: a bar growing to 44% beats the words \
"44% improvement".

Answer with exactly one fenced ```python block containing the whole file and nothing \
else outside it."""


def build_scene_code_prompt(
    storyboard: Storyboard, durations: list[float], feedback: str | None, previous_source: str | None
) -> str:
    _validate_durations(storyboard, durations)
    beat_lines = []
    for i, (beat, seconds) in enumerate(zip(storyboard.beats, durations, strict=True), start=1):
        budget = _format_permitted_budget(seconds)
        labels = f" On-screen text: {', '.join(beat.on_screen_text)}." if beat.on_screen_text else ""
        beat_lines.append(
            f"beat_{i}: narration lasts {seconds:.1f} s, so your animations must total at most "
            f"{budget} s.\n  Narration: {beat.narration}\n  Visual: {beat.visual}{labels}"
        )
    storyboard_json = storyboard.model_dump_json(indent=2)
    prompt = (
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
    if feedback:
        prompt += (
            "\n\nThis is a fix round. Your previous file is below, followed by what went wrong. "
            "Return the FULL corrected file, changing only what the feedback needs.\n\n"
            f"<previous_file>\n{previous_source or ''}\n</previous_file>\n\n"
            f"<what_went_wrong>\n{feedback}\n</what_went_wrong>"
        )
    prompt += "\n\nWrite the complete file now, in one fenced python block."
    return prompt


def _validate_durations(storyboard: Storyboard, durations: list[float]) -> None:
    expected = len(storyboard.beats)
    if len(durations) != expected:
        raise ValueError(f"durations must contain one duration per beat ({expected} required, got {len(durations)})")
    for index, seconds in enumerate(durations, start=1):
        try:
            finite = math.isfinite(seconds)
        except (TypeError, ValueError):
            finite = False
        if not finite:
            raise ValueError(f"duration for beat_{index} must be finite")
        if seconds <= BEAT_MARGIN_SECONDS:
            raise ValueError(f"duration for beat_{index} must be greater than {BEAT_MARGIN_SECONDS} seconds")


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
        {"role": "user", "content": build_scene_code_prompt(storyboard, durations, feedback, previous_source)},
    ]
    return extract_python_block(complete(model, messages))
