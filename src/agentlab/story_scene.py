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
"start", "end", "narration", "overrun"}], "total"}.

The base class owns: background, the caption band (bottom of frame,
swapped at the start of every beat), safe-margin fitting, and timing (each
beat is padded with a hold so its length is at least its narration's
length, so the audio muxed later lines up). The generated class owns
everything the viewer watches: one method per beat, beat_1 .. beat_n.

Scene.time is manim's renderer clock (Manim Community 0.21: Scene.time
returns renderer.time, advanced by every play and wait), which is what
makes per-beat padding exact.

The pure helpers above the manim import (beat_record, overrun_report,
beat_midpoints, beat_lengths, wrap_text, load_spec) are importable from the
main venv without manim, and story_video.py uses them.
"""

import json
import os
import re
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
CAPTION_WRAP_WIDTH = 60
CAPTION_MAX_HEIGHT = 1.4
CAPTION_SWAP_SECONDS = 0.25

PER_BEAT_OVERRUN_LIMIT = 0.75
TOTAL_OVERRUN_LIMIT = 3.0

_BEAT_METHOD_RE = re.compile(r"^beat_(\d+)$")


def load_spec() -> dict:
    path = os.environ.get(SPEC_ENV)
    if not path:
        raise RuntimeError(f"{SPEC_ENV} env var not set; story_scene.py is run through agentlab.story_video")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wrap_text(s: str, width: int = 44) -> str:
    return "\n".join(textwrap.wrap(s, width)) or s


def beat_record(index: int, start: float, end: float, narration_seconds: float) -> dict:
    return {
        "beat": index,
        "start": float(start),
        "end": float(end),
        "narration": float(narration_seconds),
        "overrun": max(0.0, round((end - start) - narration_seconds, 3)),
    }


def overrun_report(
    timing: dict, per_beat_limit: float = PER_BEAT_OVERRUN_LIMIT, total_limit: float = TOTAL_OVERRUN_LIMIT
) -> str | None:
    """None when every beat fits its narration (within the limits); else a
    message the coder can act on, naming each offending beat."""
    beats = timing.get("beats") or []
    bad = [b for b in beats if b.get("overrun", 0.0) > per_beat_limit]
    total = sum(b.get("overrun", 0.0) for b in beats)
    if not bad and total <= total_limit:
        return None
    lines = [f"beat {b['beat']} ran {b['overrun']:.1f} s past its {b['narration']:.1f} s narration" for b in bad]
    if total > total_limit:
        lines.append(f"total overrun {total:.1f} s is above the {total_limit:.1f} s limit")
    return (
        "Beats ran longer than their narration. Shorten run_time values or drop animations "
        "so each beat's animations end at least 0.3 s before its narration ends:\n- "
        + "\n- ".join(lines)
    )


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
        Scene,
        Text,
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

    class StoryScene(Scene):
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
            """Fade out everything except the caption."""
            keep = id(self._caption) if self._caption is not None else None
            targets = [m for m in list(self.mobjects) if id(m) != keep]
            for m in targets:
                m.clear_updaters()
            if targets:
                self.play(*[FadeOut(m) for m in targets], run_time=run_time)

        def hold(self, seconds: float):
            """Wait; use it to let a change sink in."""
            self.wait(max(float(seconds), 0.01))

        # -- owned by the base class -------------------------------------------

        def _swap_caption(self, text: str):
            new = self.fit(
                Text(wrap_text(text, CAPTION_WRAP_WIDTH), font_size=CAPTION_FONT_SIZE, color=WHITE, line_spacing=1.15),
                max_w=STAGE_WIDTH,
                max_h=CAPTION_MAX_HEIGHT,
            )
            new.to_edge(DOWN, buff=0.3)
            anims = [FadeIn(new)]
            if self._caption is not None:
                anims.append(FadeOut(self._caption))
            self.play(*anims, run_time=CAPTION_SWAP_SECONDS)
            self._caption = new

        def _write_timing(self):
            path = os.environ.get(TIMING_ENV)
            if not path:
                return
            Path(path).write_text(
                json.dumps({"beats": self._timing, "total": float(self.time)}, indent=1), encoding="utf-8"
            )
