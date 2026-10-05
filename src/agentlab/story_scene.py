"""StoryScene: the base class every generated paper scene subclasses
(docs/specs/2026-09-05-metaphor-videos.md, section 4; the 3Blue1Brown look
from docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md).

Executed standalone by manim (`python -m manim` in the video image, `uvx
manim` on the laptop), so like video_scenes.py it imports ONLY manim and
the stdlib, never agentlab. story_video.py copies this file next to the
generated scene and puts that directory on PYTHONPATH, which is why the
generated code says `from story_scene import StoryScene`.

Reads SCENE_SPEC_JSON: {"storyboard": <Storyboard.model_dump()>,
"durations": [seconds per beat], "subtitles": [narration per beat]}.
"subtitles" is optional.
Writes SCENE_TIMING_OUT at the end of construct: {"beats": [{"beat",
"start", "end", "narration", "overrun"}], "total", "layout_warnings",
"layout_skipped"}.

The base class owns: the 3Blue1Brown look (black background, CMU Serif
text, the palette below), safe-margin fitting, the end-of-beat layout
audit, the subtitles, and timing (each beat is padded with a hold so its
length is at least its narration's length, so the audio muxed later lines
up). Subtitles show the narration one chunk of at most two lines at a time
in a slim strip at the bottom (Daniel, 2026-10-05: "i need subtitles"); the
stage ends above that strip. The generated class owns everything else the
viewer watches: one method per beat, beat_1 .. beat_n.

StoryScene is a ThreeDScene, so the generated code may move the camera and
draw 3D mobjects. The camera starts flat, looking straight at the stage,
where 2D content renders exactly as it does in a plain Scene.

Scene.time is manim's renderer clock (Manim Community 0.21: Scene.time
returns renderer.time, advanced by every play and wait).
Final holds are rounded up by one frame when Manim truncates a fractional
static wait, so a visual beat never ends before its narration.

The pure helpers above the manim import (beat_record, layout_warning,
to_screen, camera_is_flat, beat_midpoints, beat_lengths, wrap_text,
load_spec) are importable from the main venv without manim, and
story_video.py uses them.
"""

import json
import math
import os
import re
import textwrap
from pathlib import Path

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

SUBTITLE_FONT_SIZE = 22
SUBTITLE_RENDER_SIZE = 72
"""Pango drops and squeezes word spaces at small font sizes, so a subtitle
is drawn at this size and scaled down to SUBTITLE_FONT_SIZE."""
SUBTITLE_WRAP_WIDTH = 64
SUBTITLE_MAX_LINES = 2
SUBTITLE_BOTTOM_BUFF = 0.25
SUBTITLE_STRIP = 1.1
"""Height kept free for two subtitle lines at the bottom of the frame."""

STAGE_TOP = FRAME_HALF_HEIGHT - EDGE_MARGIN
STAGE_BOTTOM = -FRAME_HALF_HEIGHT + SUBTITLE_STRIP
STAGE_RIGHT = round(FRAME_HALF_WIDTH - EDGE_MARGIN, 2)
STAGE_LEFT = -STAGE_RIGHT
STAGE_WIDTH = STAGE_RIGHT - STAGE_LEFT
STAGE_HEIGHT = STAGE_TOP - STAGE_BOTTOM

SCENE_CLASS = "PaperStory"
TIMING_ENV = "SCENE_TIMING_OUT"
SPEC_ENV = "SCENE_SPEC_JSON"

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

OVERLAP_SHARE = 0.10
"""Two text boxes overlap when the shared area passes this share of the
smaller box, so text that only touches stays quiet."""

FLAT_TOLERANCE = 1e-3

SPILL_SHARE = (0.10, 0.90)
"""Text spills out of a box when this much of it, and no more, lies inside:
fully inside is a label in its box, fully outside is a label beside it."""
CROSS_MARGIN = 0.15
"""A line crosses text when it enters the text box shrunk by this share on
each side, so an arrow that stops at a label's edge stays quiet."""

MAX_REPORTED_PROBLEMS = 4


def subtitle_chunks(
    text: str,
    seconds: float,
    width: int = SUBTITLE_WRAP_WIDTH,
    max_lines: int = SUBTITLE_MAX_LINES,
) -> list[tuple[float, str]]:
    """Split one beat's narration into chunks of at most max_lines lines,
    each with its start offset in the beat.

    ponytail: offsets follow each chunk's share of the characters, an
    estimate of when the voice reaches it; Polly sentence speech marks give
    exact times if subtitles drift.
    """
    chunks = []
    for sentence in re.split(r"(?<=[.!?])\s+", text.strip()):
        lines = textwrap.wrap(sentence, width)
        for start in range(0, len(lines), max_lines):
            chunks.append("\n".join(lines[start : start + max_lines]))
    total = sum(len(chunk) for chunk in chunks) or 1
    cues, done = [], 0
    for chunk in chunks:
        cues.append((seconds * done / total, chunk))
        done += len(chunk)
    return cues


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


def _segment_hits_box(x0: float, y0: float, x1: float, y1: float, box) -> bool:
    """Liang-Barsky clip: does the segment enter the box?"""
    _, left, right, bottom, top = box
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - bottom), (dy, top - y0)):
        if p == 0:
            if q < 0:
                return False
            continue
        u = q / p
        if p < 0:
            low = max(low, u)
        else:
            high = min(high, u)
        if low > high:
            return False
    return True


def _shrunk(box, share: float):
    name, left, right, bottom, top = box
    width, height = right - left, top - bottom
    return (name, left + share * width, right - share * width, bottom + share * height, top - share * height)


def layout_problems(boxes, texts=(), shapes=(), segments=()) -> list[str]:
    """Name every box that leaves the stage, every pair of texts that
    overlap, every text that spills out of a box, and every line that runs
    through a text. Pure screen-space geometry, deliberately: the faults the
    frame judge once described in prose are exact rectangle arithmetic.
    Advisory, see layout_warning.

    `boxes`, `texts`, and `shapes` (closed shapes such as rectangles) are
    [(name, left, right, bottom, top)] in screen units; `segments` (lines
    and arrows) are [(name, x0, y0, x1, y1)]. A box wholly outside the
    frame belongs to a part of the board the camera is not showing, so it
    is skipped.
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
    for text in shown:
        for shape in shapes:
            inside = _overlap_area(text, shape) / _area(text)
            if SPILL_SHARE[0] < inside < SPILL_SHARE[1]:
                problems.append(f"{text[0]} spills out of {shape[0]}")
        core = _shrunk(text, CROSS_MARGIN)
        for name, x0, y0, x1, y1 in segments:
            if _segment_hits_box(x0, y0, x1, y1, core):
                problems.append(f"the {name} crosses {text[0]}")
    return list(dict.fromkeys(problems))


def layout_warning(beat: int, boxes, texts=(), shapes=(), segments=()) -> str | None:
    """One line naming what crosses the frame margin or overlaps at the end
    of a beat, or None. The base class records it in the timing output and
    never fails a render on it; story_video.py spends at most one edit
    round on it."""
    problems = layout_problems(boxes, texts, shapes, segments)
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
        DecimalNumber,
        FadeOut,
        Line,
        MarkupText,
        Paragraph,
        Polygram,
        SingleStringMathTex,
        Text,
        ThreeDScene,
        ValueTracker,
        VMobject,
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
        """A name the scene coder can act on.

        Its own text when it has some, otherwise the text of the first label
        inside it, so a group reads as "the group holding 'Context window'"
        instead of the bare "VGroup" that tells the coder nothing.
        """
        own = _label_of(mobject)
        if own:
            return repr(" ".join(own.split())[:40])
        for child in mobject.get_family():
            text = _label_of(child)
            if text:
                label = repr(" ".join(text.split())[:40])
                return f"the group holding {label}"
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

    def _shapes_in(mobject) -> list:
        """Lines and closed polygons in a family, outside any text."""
        if isinstance(mobject, TEXT_TYPES):
            return []
        found = [mobject] if isinstance(mobject, (Line, Polygram)) else []
        for sub in mobject.submobjects:
            found.extend(_shapes_in(sub))
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
            subtitles = spec.get("subtitles")
            n = len(self.storyboard["beats"])
            if len(durations) != n or (subtitles is not None and len(subtitles) != n):
                raise ValueError(f"spec durations and subtitles do not match {n} beats")
            self._subtitle = None
            self._subtitle_text = None
            self._subtitle_cues: list[tuple[float, str]] = []
            self._timing: list[dict] = []
            self._layout_warnings: list[str] = []
            self._layout_skipped: list[int] = []
            for i in range(n):
                method = getattr(self, f"beat_{i + 1}", None)
                if method is None:
                    raise AttributeError(f"{SCENE_CLASS} is missing beat_{i + 1}")
                start = self.time
                if subtitles is not None:
                    self._start_subtitles(start, subtitles[i], durations[i])
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
            """Fade out everything but the subtitle. Value trackers draw nothing and include
            the camera's own angles, so they stay and an ambient camera
            rotation keeps turning."""
            targets = [
                m
                for m in list(self.mobjects)
                if m is not self._subtitle and not isinstance(m, ValueTracker)
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
            """Record text that overlaps text, or anything that crosses the
            frame margin, at the end of this beat, in screen units. Runs
            after the beat method returns, so every mobject is at its
            resting position. A tilted camera turns boxes into perspective
            shapes, so that beat is recorded as skipped. Never fails the
            render."""
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

            def screen_point(mobject, point):
                x, y = float(point[0]), float(point[1])
                if mobject in fixed:
                    return x, y
                return (x - float(center[0])) * zoom, (y - float(center[1])) * zoom

            boxes, texts, shapes, segments, seen = [], [], [], [], set()
            for mobject in self.mobjects:
                if mobject is self._subtitle or not mobject.get_all_points().size:
                    continue
                boxes.append(screen_box(mobject))
                for text in _texts_in(mobject):
                    # Swap and similar animations leave a group holding
                    # mobjects that already sit in another family.
                    if id(text) in seen or not _visible(text):
                        continue
                    seen.add(id(text))
                    texts.append(screen_box(text))
                for shape in _shapes_in(mobject):
                    if id(shape) in seen or not _visible(shape) or not shape.points.size:
                        continue
                    seen.add(id(shape))
                    if isinstance(shape, Line):
                        name = type(shape).__name__.lower()
                        start = screen_point(shape, shape.get_start())
                        end = screen_point(shape, shape.get_end())
                        segments.append((name, *start, *end))
                    else:
                        shapes.append(screen_box(shape))
            warning = layout_warning(beat, boxes, texts, shapes, segments)
            if warning:
                self._layout_warnings.append(warning)

        def _start_subtitles(self, start: float, text: str, seconds: float):
            self._subtitle_cues = [
                (start + offset, chunk) for offset, chunk in subtitle_chunks(text, seconds)
            ]
            if self._subtitle is None:
                self._subtitle = VMobject()
                # A dt argument makes the updater time-based, so it also runs
                # through static waits, where a chunk change can fall.
                self._subtitle.add_updater(lambda m, dt: self._show_subtitle(m))
            if self._subtitle not in self.mobjects:
                # The beat's own code may have removed it; bring it back.
                self.add_fixed_in_frame_mobjects(self._subtitle)
            self._show_subtitle(self._subtitle)

        def _show_subtitle(self, mobject):
            current = None
            for at, chunk in self._subtitle_cues:
                if self.time + FRAME_TIME_TOLERANCE >= at:
                    current = chunk
            if current == self._subtitle_text:
                return
            self._subtitle_text = current
            if current is None:
                mobject.become(VMobject())
                return
            text = Text(
                current, font_size=SUBTITLE_RENDER_SIZE, color=WHITE, line_spacing=1.1
            ).scale(SUBTITLE_FONT_SIZE / SUBTITLE_RENDER_SIZE)
            text.set_stroke(BLACK, width=5, background=True)
            text.to_edge(DOWN, buff=SUBTITLE_BOTTOM_BUFF)
            mobject.become(text)
            # become() swaps in new glyphs; pin them too, so camera moves
            # never carry the subtitle along.
            self.camera.add_fixed_in_frame_mobjects(mobject)

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
