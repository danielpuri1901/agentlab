"""StoryScene: the base class every generated paper scene subclasses
(docs/specs/2026-09-05-metaphor-videos.md, section 4).

Executed standalone by manim (`python -m manim` in the video image, `uvx
manim` on the laptop), so like video_scenes.py it imports ONLY manim and
the stdlib, never agentlab. story_video.py copies this file next to the
generated scene and puts that directory on PYTHONPATH, which is why the
generated code says `from story_scene import StoryScene`.

Reads SCENE_SPEC_JSON: {"storyboard": <Storyboard.model_dump()>,
"durations": [seconds per beat], "captions": [text per beat]}.
Writes SCENE_TIMING_OUT at the end of construct: {"beats": [{"beat",
"start", "end", "narration", "overrun"}], "total", "layout_warnings"}.

The base class owns: background, the caption band (bottom of frame,
swapped at the start of every beat and pinned to the screen so camera
moves never tilt it), safe-margin fitting, and timing (each beat is padded
with a hold so its length is at least its narration's length, so the audio
muxed later lines up). The generated class owns everything the viewer
watches: one method per beat, beat_1 .. beat_n.

StoryScene is a ThreeDScene, so the generated code may move the camera and
draw 3D mobjects. The camera starts flat, looking straight at the stage,
where 2D content renders exactly as it does in a plain Scene.

Scene.time is manim's renderer clock (Manim Community 0.21: Scene.time
returns renderer.time, advanced by every play and wait).
Final holds are rounded up by one frame when Manim truncates a fractional
static wait, so a visual beat never ends before its narration.

The pure helpers above the manim import (beat_record, layout_warning,
beat_midpoints, beat_lengths, wrap_text, load_spec) are importable from the
main venv without manim, and story_video.py uses them.
"""

import json
import os
import textwrap
from pathlib import Path

BACKGROUND = "#05070c"
ACCENT = "#2f6fd6"
GOLD = "#e8c547"
GREEN = "#4caf7d"
RED = "#d9534f"

STAGE_TOP = 3.6
STAGE_BOTTOM = -2.3
STAGE_LEFT = -6.4
STAGE_RIGHT = 6.4
STAGE_WIDTH = STAGE_RIGHT - STAGE_LEFT
STAGE_HEIGHT = STAGE_TOP - STAGE_BOTTOM

SCENE_CLASS = "PaperStory"
TIMING_ENV = "SCENE_TIMING_OUT"
SPEC_ENV = "SCENE_SPEC_JSON"

CAPTION_FONT_SIZE = 20
CAPTION_RENDER_SIZE = 72
"""Pango drops and squeezes word spaces at small font sizes, so a caption is
drawn at this size and scaled down to CAPTION_FONT_SIZE."""
CAPTION_WRAP_WIDTH = 60
CAPTION_MAX_HEIGHT = 1.4
CAPTION_SWAP_SECONDS = 0.25

FRAME_TIME_TOLERANCE = 1e-6

def load_spec() -> dict:
    path = os.environ.get(SPEC_ENV)
    if not path:
        raise RuntimeError(f"{SPEC_ENV} env var not set; story_scene.py is run through agentlab.story_video")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wrap_text(s: str, width: int = 44) -> str:
    return "\n".join(textwrap.wrap(s, width)) or s


LAYOUT_TOLERANCE = 0.05
"""Slack before a box counts as off-stage. Anti-aliasing and stroke width
put a few hundredths of a unit outside a mobject's nominal bounds."""

CAPTION_CLEARANCE = 0.12
"""Gap a mobject must keep above the caption band."""

MAX_REPORTED_PROBLEMS = 4


def layout_problems(boxes, caption_top=None) -> list[str]:
    """Name every box that leaves the stage or sits on the caption band.

    Pure geometry, deliberately: the faults the frame judge once described
    in prose ("the context window rectangle clips the caption band") are
    exact rectangle arithmetic. The result is advisory, see layout_warning.

    `boxes` is [(name, left, right, bottom, top)]. `caption_top` is the top
    of the caption band, or None when there is no caption yet.
    """
    problems: list[str] = []
    for name, left, right, bottom, top in boxes:
        # A box with no extent draws nothing. ValueTracker is the one that
        # matters: it is a single point whose x coordinate IS the number it
        # stores, so a counter running to 175 parks it far off the stage.
        if right - left < LAYOUT_TOLERANCE and top - bottom < LAYOUT_TOLERANCE:
            continue
        if left < STAGE_LEFT - LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the left edge")
        if right > STAGE_RIGHT + LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the right edge")
        if top > STAGE_TOP + LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the top edge")
        if bottom < STAGE_BOTTOM - LAYOUT_TOLERANCE:
            problems.append(f"{name} runs off the bottom edge")
        if (
            caption_top is not None
            and bottom < caption_top + CAPTION_CLEARANCE
            and top > caption_top - LAYOUT_TOLERANCE
        ):
            problems.append(f"{name} sits on the caption band")
    return problems


def layout_warning(beat: int, boxes, caption_top=None) -> str | None:
    """One line naming what sits off the stage at the end of a beat, or None.

    Advisory: the base class records it in the timing output and never
    fails a render on it. The boxes are world coordinates, and once the
    generated scene moves the camera they stop matching what is on screen.
    """
    problems = layout_problems(boxes, caption_top)
    if not problems:
        return None
    return f"beat {beat} layout: " + "; ".join(problems[:MAX_REPORTED_PROBLEMS])


def beat_record(index: int, start: float, end: float, narration_seconds: float) -> dict:
    return {
        "beat": index,
        "start": float(start),
        "end": float(end),
        "narration": float(narration_seconds),
        "overrun": max(0.0, round((end - start) - narration_seconds, 3)),
    }



def beat_midpoints(timing: dict) -> list[float]:
    return [(b["start"] + b["end"]) / 2 for b in timing.get("beats") or []]


def beat_lengths(timing: dict) -> list[float]:
    return [b["end"] - b["start"] for b in timing.get("beats") or []]


try:
    from manim import (
        BOLD,
        DOWN,
        WHITE,
        FadeIn,
        FadeOut,
        Text,
        ThreeDScene,
        ValueTracker,
    )
    from manim.utils.color import (  # noqa: F401 - re-exported for generated scenes
        GREY_A,
        GREY_B,
        GREY_C,
        GREY_D,
    )

    _MANIM_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit without manim installed
    _MANIM_AVAILABLE = False


if _MANIM_AVAILABLE:

    def _describe_mobject(mobject) -> str:
        """A name the scene coder can act on.

        Its own text when it has some, otherwise the text of the first label
        inside it, so a group reads as "the group holding 'Context window'"
        instead of the bare "VGroup" that tells the coder nothing.
        """
        own = getattr(mobject, "text", None)
        if isinstance(own, str) and own.strip():
            return repr(" ".join(own.split())[:40])
        for child in mobject.get_family():
            text = getattr(child, "text", None)
            if isinstance(text, str) and text.strip():
                label = repr(" ".join(text.split())[:40])
                return f"the group holding {label}"
        shapes = [type(c).__name__ for c in mobject.submobjects]
        if shapes:
            kinds = sorted(set(shapes))
            return f"the {type(mobject).__name__} of {len(shapes)} {'/'.join(kinds[:2])}"
        return type(mobject).__name__


    class StoryScene(ThreeDScene):
        """Subclass as PaperStory, define beat_1 .. beat_n, never override
        construct. See the API cheat-sheet in agentlab.scene_code."""

        def construct(self):
            self.camera.background_color = BACKGROUND
            spec = load_spec()
            self.storyboard = spec["storyboard"]
            durations = spec["durations"]
            captions = spec["captions"]
            n = len(self.storyboard["beats"])
            if len(durations) != n or len(captions) != n:
                raise ValueError(f"spec has {len(durations)} durations and {len(captions)} captions for {n} beats")
            self._caption = None
            self._timing: list[dict] = []
            self._layout_warnings: list[str] = []
            for i in range(n):
                method = getattr(self, f"beat_{i + 1}", None)
                if method is None:
                    raise AttributeError(f"{SCENE_CLASS} is missing beat_{i + 1}")
                start = self.time
                self._swap_caption(captions[i])
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

        def counter(self, start: float, end: float, suffix: str = "", size: int = 44, color=ACCENT, decimals: int = 0):
            """A text number that counts from start to end. Returns (mobject,
            animation): place the mobject, then self.play(animation, run_time=...).
            Call self.freeze(mobject) before any FadeOut/Transform that includes it."""
            tracker = ValueTracker(float(start))

            def render(value: float):
                return Text(f"{value:.{decimals}f}{suffix}", font_size=size, color=color, weight=BOLD)

            mobject = render(float(start))
            mobject.add_updater(lambda m: m.become(render(tracker.get_value()).move_to(m.get_center())))
            return mobject, tracker.animate.set_value(float(end))

        def freeze(self, mobject):
            """Stop a counter updating so later group animations are safe."""
            mobject.clear_updaters()
            return mobject

        def clear_stage(self, run_time: float = 0.4):
            """Fade out everything except the caption. Value trackers draw
            nothing and include the camera's own angles, so they stay and an
            ambient camera rotation keeps turning."""
            keep = id(self._caption) if self._caption is not None else None
            targets = [
                m
                for m in list(self.mobjects)
                if id(m) != keep and not isinstance(m, ValueTracker)
            ]
            for m in targets:
                m.clear_updaters()
            if targets:
                self.play(*[FadeOut(m) for m in targets], run_time=run_time)

        def hold(self, seconds: float):
            """Wait; use it to let a change sink in."""
            self.wait(max(float(seconds), 0.01))

        # -- owned by the base class -------------------------------------------

        def _audit_layout(self, beat: int):
            """Record what is off-stage or on the caption band at the end of
            this beat. Runs after the beat method returns, so every mobject
            is at its resting position. Never fails the render."""
            caption_top = None
            if self._caption is not None:
                caption_top = float(self._caption.get_top()[1])
            boxes = []
            for mobject in self.mobjects:
                if mobject is self._caption:
                    continue
                if not mobject.get_all_points().size:
                    continue
                boxes.append(
                    (
                        _describe_mobject(mobject),
                        float(mobject.get_left()[0]),
                        float(mobject.get_right()[0]),
                        float(mobject.get_bottom()[1]),
                        float(mobject.get_top()[1]),
                    )
                )
            warning = layout_warning(beat, boxes, caption_top)
            if warning:
                self._layout_warnings.append(warning)

        def _swap_caption(self, text: str):
            caption = Text(
                wrap_text(text, CAPTION_WRAP_WIDTH),
                font_size=CAPTION_RENDER_SIZE,
                color=WHITE,
                line_spacing=1.15,
            ).scale(CAPTION_FONT_SIZE / CAPTION_RENDER_SIZE)
            new = self.fit(caption, max_w=STAGE_WIDTH, max_h=CAPTION_MAX_HEIGHT)
            new.to_edge(DOWN, buff=0.3)
            self.add_fixed_in_frame_mobjects(new)
            anims = [FadeIn(new)]
            if self._caption is not None:
                anims.append(FadeOut(self._caption))
            self.play(*anims, run_time=CAPTION_SWAP_SECONDS)
            self._caption = new

        def _write_timing(self):
            path = os.environ.get(TIMING_ENV)
            if not path:
                return
            timing = {
                "beats": self._timing,
                "total": float(self.time),
                "layout_warnings": self._layout_warnings,
            }
            Path(path).write_text(json.dumps(timing, indent=1), encoding="utf-8")
