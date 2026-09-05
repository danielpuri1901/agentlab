"""Scene code: the coder prompt and the guard for model-written Manim
scenes (docs/specs/2026-09-05-metaphor-videos.md, section 3).

The guard is an AST allowlist, not a sandbox: it keeps honest generated
code inside manim + a few pure stdlib modules, and refuses everything that
needs LaTeX (the video image has none) or touches files, processes, the
network, or Python's introspection escape hatches. The render itself runs
in a credential-free, time-limited subprocess (video_render.render_env),
which is the real containment; the guard is there so a bad file fails in
milliseconds with a message the model can act on, instead of minutes into
a render.
"""

import ast
import re
from collections.abc import Callable

from agentlab.storyboard import Storyboard

SCENE_CLASS = "PaperStory"
BASE_CLASS = "StoryScene"
MAX_TOKENS = 8000
BEAT_MARGIN_SECONDS = 0.3

ALLOWED_IMPORTS = frozenset(
    {"manim", "story_scene", "math", "random", "itertools", "functools", "numpy", "dataclasses", "typing", "colorsys"}
)

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


def check_scene_code(source: str, beat_count: int) -> list[str]:
    """Findings (empty means clean). Order preserved, duplicates removed."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: line {exc.lineno}: {exc.msg}"]
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    findings.append(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_IMPORTS:
                findings.append(f"import not allowed: from {node.module or '.'}")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    findings.append(f"forbidden name imported: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            findings.append(f"forbidden attribute: {node.attr}")
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
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == SCENE_CLASS]
    if len(classes) != 1:
        findings.append(f"exactly one top-level class named {SCENE_CLASS} is required")
        return _dedup(findings)
    cls = classes[0]
    bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "" for b in cls.bases]
    if BASE_CLASS not in bases:
        findings.append(f"{SCENE_CLASS} must subclass {BASE_CLASS}")
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
- self.clear_stage(run_time=0.4): fade out everything except the caption. Use it when the metaphor changes view.
- self.hold(seconds): wait, to let a change sink in.

The base class already: sets the dark background, draws the caption for each beat in the bottom band, and pads each beat so it lasts exactly its narration. You only write beat_1 .. beat_n."""


SCENE_CODE_SYSTEM = """You write one Manim Community v0.21 scene file that animates a \
storyboard for a short paper-explainer video. The storyboard names one metaphor and a \
list of beats; each beat has narration (already recorded, its duration is given) and a \
visual description. Your job is to draw exactly that visual, beat by beat, with clean \
motion, in the style of 3Blue1Brown: simple shapes, one accent colour, the change on the \
object is the explanation.

Contract:
- File starts with `from manim import (...)` naming only what you use, then `from \
story_scene import StoryScene` plus any constants you use from it.
- Exactly one class, `class PaperStory(StoryScene):`. Do not override construct.
- One method per beat, beat_1 to beat_n, n equal to the storyboard's beat count.
- Each beat's animations (the sum of run_time values plus any self.hold) must end at \
least 0.3 s before that beat's narration ends. The budget per beat is listed below. The \
base class pads the rest.
- Objects that persist across beats live on self (self.house, self.case, ...). Later \
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
    beat_lines = []
    for i, (beat, seconds) in enumerate(zip(storyboard.beats, durations, strict=True), start=1):
        budget = max(seconds - BEAT_MARGIN_SECONDS, 0.5)
        labels = f" On-screen text: {', '.join(beat.on_screen_text)}." if beat.on_screen_text else ""
        beat_lines.append(
            f"beat_{i}: narration lasts {seconds:.1f} s, so your animations must total at most "
            f"{budget:.1f} s.\n  Narration: {beat.narration}\n  Visual: {beat.visual}{labels}"
        )
    prompt = (
        f"Metaphor: {storyboard.metaphor}\nWhy: {storyboard.why_this_metaphor}\n"
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
