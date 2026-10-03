"""Shared look for the AgentLab explainer chapters (Manim Community 0.21).

Every chapter file does `from kit import *` and defines
`class Chapter(ExplainerScene)` with methods beat_1 .. beat_n, one per
narration entry in narration.py. The StoryScene base class (copied next to
this file at render time) swaps the caption at the start of every beat,
pads every beat to its narration length, and fails the render when anything
leaves the stage (x -6.4 to 6.4, y -2.3 to 3.6) or sits on the caption band.

Colour meaning, used the same way in every chapter:
ACCENT blue = AWS control flow, GOLD = a model call, GREEN = success or a
positive signal, RED = failure or a negative signal, PURPLE = taste and
memory, MUTED grey = storage and plumbing.
"""

from manim import (
    BOLD,
    DOWN,
    LEFT,
    PI,
    RIGHT,
    UP,
    Arc,
    Arrow,
    Circle,
    Create,
    Dot,
    Ellipse,
    FadeIn,
    FadeOut,
    Line,
    MoveAlongPath,
    Polygon,
    Rectangle,
    RoundedRectangle,
    Succession,
    Text,
    VGroup,
    smooth,
)
from story_scene import (  # noqa: F401 - re-exported for the chapters
    ACCENT,
    BACKGROUND,
    CAPTION_FONT_SIZE,
    CAPTION_MAX_HEIGHT,
    CAPTION_SWAP_SECONDS,
    CAPTION_WRAP_WIDTH,
    GOLD,
    GREEN,
    RED,
    STAGE_BOTTOM,
    STAGE_LEFT,
    STAGE_RIGHT,
    STAGE_TOP,
    STAGE_WIDTH,
    StoryScene,
    load_spec,
    wrap_text,
)

FONT = "Helvetica Neue"
MONO = "Menlo"
INK = "#e8ecf3"
MUTED = "#8a93a6"
LINE = "#4a5568"
PANEL = "#0e1522"
PURPLE = "#9b7fe6"

CONTENT_TOP = 2.85
"""Top of the free stage under the chapter header."""

Text.set_default(font=FONT)


RENDER_SIZE = 72
"""Pango kerns badly at small sizes, so text is drawn large and scaled down."""


def txt(s: str, size: int = 24, color: str = INK, bold: bool = False, mono: bool = False) -> Text:
    """One line (or explicit newlines) of text in the house font."""
    kwargs = {"weight": BOLD} if bold else {}
    if mono:
        kwargs["font"] = MONO
    big = max(size, RENDER_SIZE)
    return Text(s, font_size=big, color=color, line_spacing=1.1, **kwargs).scale(size / big)


def node(title: str, sub: str | None = None, color: str = ACCENT, w: float = 2.6, h: float = 1.0, size: int = 24) -> VGroup:
    """A rounded card: coloured border, a stripe on the left, a bold title and
    an optional muted subtitle. `card.box` is the border, for highlighting."""
    box = RoundedRectangle(
        corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
    )
    stripe = RoundedRectangle(
        corner_radius=0.04, width=0.07, height=h - 0.34, stroke_width=0, fill_color=color, fill_opacity=1
    ).move_to(box.get_left() + RIGHT * 0.17)
    parts = [txt(title, size, INK, bold=True)]
    if sub:
        parts.append(txt(sub, max(12, round(size * 0.66)), MUTED))
    label = VGroup(*parts).arrange(DOWN, buff=0.08)
    room_w, room_h = w - 0.6, h - 0.24
    if label.width > room_w:
        label.scale_to_fit_width(room_w)
    if label.height > room_h:
        label.scale_to_fit_height(room_h)
    label.move_to(box.get_center() + RIGHT * 0.08)
    card = VGroup(box, stripe, label)
    card.box = box
    card.label = label
    return card


def tile(icon, title: str, sub: str | None = None, color: str = ACCENT, w: float = 3.9, h: float = 1.3) -> VGroup:
    """A wide card with an icon on the left and a title plus subtitle on the right."""
    box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    if icon.height > h * 0.62:
        icon.scale_to_fit_height(h * 0.62)
    if icon.width > h * 0.7:
        icon.scale_to_fit_width(h * 0.7)
    icon.move_to(box.get_left() + RIGHT * (0.3 + icon.width / 2))
    parts = [txt(title, 24, INK, bold=True)]
    if sub:
        parts.append(txt(sub, 16, MUTED))
    texts = VGroup(*parts).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
    room = w - icon.width - 0.95
    if texts.width > room:
        texts.scale_to_fit_width(room)
    texts.next_to(icon, RIGHT, buff=0.3)
    card = VGroup(box, icon, texts)
    card.box = box
    return card


def chip(s: str, color: str = ACCENT, size: int = 18, filled: bool = True) -> VGroup:
    """A small pill, for verdicts, weights and tags."""
    label = txt(s, size, BACKGROUND if filled else color, bold=True)
    pill = RoundedRectangle(
        corner_radius=min(0.2, (label.height + 0.24) / 2),
        width=label.width + 0.42,
        height=label.height + 0.24,
        stroke_color=color,
        stroke_width=2,
        fill_color=color,
        fill_opacity=1 if filled else 0,
    )
    label.move_to(pill)
    return VGroup(pill, label)


def link(a, b, color: str = LINE, buff: float = 0.12, label: str | None = None, label_size: int = 16) -> VGroup:
    """A straight arrow between the facing edges of two mobjects. The arrow is
    `group[0]`; an optional small label sits beside its middle."""
    delta = b.get_center() - a.get_center()
    if abs(delta[0]) >= abs(delta[1]):
        start, end = (a.get_right(), b.get_left()) if delta[0] > 0 else (a.get_left(), b.get_right())
    else:
        start, end = (a.get_top(), b.get_bottom()) if delta[1] > 0 else (a.get_bottom(), b.get_top())
    arrow = Arrow(start, end, buff=buff, color=color, stroke_width=3, max_tip_length_to_length_ratio=0.18, max_stroke_width_to_length_ratio=8)
    group = VGroup(arrow)
    if label:
        tag = txt(label, label_size, MUTED)
        horizontal = abs(delta[0]) >= abs(delta[1])
        tag.next_to(arrow, UP if horizontal else RIGHT, buff=0.08)
        group.add(tag)
    return group


def travel(arrow, color: str = GOLD, run_time: float = 0.8, radius: float = 0.075):
    """A glowing dot that rides along an arrow (or any line), then fades."""
    target = arrow[0] if isinstance(arrow, VGroup) else arrow
    path = Line(target.get_start(), target.get_end())
    dot = Dot(radius=radius, color=color).move_to(path.get_start())
    halo = Dot(radius=radius * 2.2, color=color, fill_opacity=0.25).move_to(path.get_start())
    pair = VGroup(halo, dot)
    return Succession(
        FadeIn(pair, run_time=0.1),
        MoveAlongPath(pair, path, run_time=run_time, rate_func=smooth),
        FadeOut(pair, run_time=0.15),
    )


def lit(card, color: str = GOLD, width: float = 4.5):
    """Animation: switch a card's border to `color` (use inside self.play)."""
    return card.box.animate.set_stroke(color=color, width=width)


def header(number: int, title: str) -> VGroup:
    """Chapter chip plus title, pinned to the top-left of the stage."""
    group = VGroup(chip(f"{number:02d}", ACCENT, 18), txt(title, 26, INK, bold=True)).arrange(RIGHT, buff=0.25)
    group.move_to([STAGE_LEFT + group.width / 2, STAGE_TOP - group.height / 2, 0])
    return group


def code_card(lines, w: float | None = None, size: int = 17) -> VGroup:
    """A dark panel of monospaced lines. Each line is a string or (string, colour)."""
    rows = []
    for line in lines:
        text, color = (line, INK) if isinstance(line, str) else line
        rows.append(txt(text, size, color, mono=True))
    body = VGroup(*rows).arrange(DOWN, aligned_edge=LEFT, buff=0.12)
    width = w if w is not None else body.width + 0.6
    panel = RoundedRectangle(
        corner_radius=0.12, width=width, height=body.height + 0.5, stroke_color=LINE, stroke_width=1.5, fill_color="#0a0f18", fill_opacity=1
    )
    body.move_to(panel).align_to(panel, LEFT).shift(RIGHT * 0.3)
    return VGroup(panel, body)


# -- small vector icons, about 1 unit tall ------------------------------------


def phone_icon(color: str = INK, h: float = 1.1) -> VGroup:
    body = RoundedRectangle(corner_radius=0.12, width=h * 0.56, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    speaker = Line(LEFT * 0.08, RIGHT * 0.08, color=color, stroke_width=2.5).move_to(body.get_top() + DOWN * 0.1)
    button = Circle(radius=0.045, color=color, stroke_width=2).move_to(body.get_bottom() + UP * 0.1)
    return VGroup(body, speaker, button)


def doc_icon(color: str = INK, h: float = 1.0) -> VGroup:
    w = h * 0.78
    sheet = Polygon(
        [-w / 2, h / 2, 0], [w / 2 - 0.18, h / 2, 0], [w / 2, h / 2 - 0.18, 0], [w / 2, -h / 2, 0], [-w / 2, -h / 2, 0],
        stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1,
    )
    lines = VGroup(*[Line(LEFT * w * 0.3, RIGHT * w * 0.3, color=color, stroke_width=2) for _ in range(3)]).arrange(DOWN, buff=0.14)
    lines.move_to(sheet.get_center() + DOWN * 0.05)
    return VGroup(sheet, lines)


def db_icon(color: str = MUTED, w: float = 0.9, h: float = 1.0) -> VGroup:
    rim = 0.28
    bottom = Ellipse(width=w, height=rim, stroke_color=color, stroke_width=2.5).move_to(DOWN * (h / 2 - rim / 2))
    body = Rectangle(width=w, height=h - rim, stroke_width=0, fill_color=PANEL, fill_opacity=1)
    sides = VGroup(
        Line(body.get_corner(UP + LEFT), body.get_corner(DOWN + LEFT), color=color, stroke_width=2.5),
        Line(body.get_corner(UP + RIGHT), body.get_corner(DOWN + RIGHT), color=color, stroke_width=2.5),
    )
    top = Ellipse(width=w, height=rim, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1).move_to(UP * (h / 2 - rim / 2))
    return VGroup(bottom, body, sides, top)


def bucket_icon(color: str = MUTED, h: float = 0.95) -> VGroup:
    w = h * 1.05
    pail = Polygon([-w / 2, h / 2, 0], [w / 2, h / 2, 0], [w * 0.36, -h / 2, 0], [-w * 0.36, -h / 2, 0], stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    rim = Ellipse(width=w, height=0.22, stroke_color=color, stroke_width=2.5).move_to(pail.get_top())
    return VGroup(pail, rim)


def queue_icon(color: str = ACCENT, n: int = 4) -> VGroup:
    slots = VGroup(*[RoundedRectangle(corner_radius=0.05, width=0.22, height=0.6, stroke_color=color, stroke_width=2, fill_color=color, fill_opacity=0.35) for _ in range(n)]).arrange(RIGHT, buff=0.08)
    tray = RoundedRectangle(corner_radius=0.1, width=slots.width + 0.3, height=0.85, stroke_color=color, stroke_width=2.5).move_to(slots)
    return VGroup(tray, slots)


def cpu_icon(color: str = ACCENT, size: float = 0.8) -> VGroup:
    """A compute chip: a square core with pins on every side."""
    core = RoundedRectangle(corner_radius=0.08, width=size, height=size, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    inner = RoundedRectangle(corner_radius=0.04, width=size * 0.45, height=size * 0.45, stroke_width=0, fill_color=color, fill_opacity=0.6)
    pins = VGroup()
    for k in (-0.25, 0.0, 0.25):
        offset = k * size
        pins.add(Line([offset, size / 2, 0], [offset, size / 2 + 0.12, 0], color=color, stroke_width=2.5))
        pins.add(Line([offset, -size / 2, 0], [offset, -size / 2 - 0.12, 0], color=color, stroke_width=2.5))
        pins.add(Line([size / 2, offset, 0], [size / 2 + 0.12, offset, 0], color=color, stroke_width=2.5))
        pins.add(Line([-size / 2, offset, 0], [-size / 2 - 0.12, offset, 0], color=color, stroke_width=2.5))
    return VGroup(pins, core, inner)


def clock_icon(color: str = INK, r: float = 0.45) -> VGroup:
    face = Circle(radius=r, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    hour = Line(face.get_center(), face.get_center() + UP * r * 0.55, color=color, stroke_width=3)
    minute = Line(face.get_center(), face.get_center() + RIGHT * r * 0.7, color=color, stroke_width=2.5)
    return VGroup(face, hour, minute)


def lambda_icon(color: str = GOLD, size: float = 0.9) -> VGroup:
    box = RoundedRectangle(corner_radius=0.12, width=size, height=size, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    glyph = txt("λ", round(size * 46), color, bold=True).move_to(box)
    return VGroup(box, glyph)


def faded(mobject, darkness: float = 0.75):
    """A dimmed copy for a "not yet" state. Reveal the real one with
    ReplacementTransform(faded_copy, original): fading a ring in place would
    switch its fill on."""
    return mobject.copy().fade(darkness)


def loop_arrow(radius: float = 0.9, color: str = PURPLE, angle: float = 1.7 * PI) -> VGroup:
    """An almost-closed circular arrow, for the three loops and the flywheel."""
    arc = Arc(radius=radius, start_angle=PI / 2, angle=-angle, color=color, stroke_width=5)
    arc.add_tip(tip_length=0.22)
    return VGroup(arc)


class ExplainerScene(StoryScene):
    """StoryScene plus the chapter header and per-beat time budgets.

    self.d(n) is the narration length of beat n in seconds (1-based). Keep
    each beat's animations shorter than self.d(n) minus 0.4 so the voice
    finishes over a still frame; the base class pads the rest.
    """

    def construct(self):
        spec = load_spec()
        self.durations = spec["durations"]
        self.chapter_number = spec.get("chapter", 0)
        self.chapter_title = spec.get("title", "")
        self.header = None
        super().construct()

    def _swap_caption(self, text: str):
        """The base class caption, drawn through txt() so small text keeps
        normal letter spacing (Pango spaces small sizes too widely)."""
        new = self.fit(
            txt(wrap_text(text, CAPTION_WRAP_WIDTH), CAPTION_FONT_SIZE, INK),
            max_w=STAGE_WIDTH,
            max_h=CAPTION_MAX_HEIGHT,
        )
        new.to_edge(DOWN, buff=0.3)
        anims = [FadeIn(new)]
        if self._caption is not None:
            anims.append(FadeOut(self._caption))
        self.play(*anims, run_time=CAPTION_SWAP_SECONDS)
        self._caption = new

    def d(self, beat: int) -> float:
        return float(self.durations[beat - 1])

    def show_header(self, run_time: float = 0.5):
        self.header = header(self.chapter_number, self.chapter_title)
        self.play(FadeIn(self.header, shift=RIGHT * 0.2), run_time=run_time)
        return self.header

    def clear(self, keep=(), run_time: float = 0.4):
        """Fade out everything except the caption, the header and `keep`."""
        kept = {id(self._caption), id(self.header), *(id(m) for m in keep)}
        targets = [m for m in list(self.mobjects) if id(m) not in kept]
        for m in targets:
            m.clear_updaters()
        if targets:
            self.play(*[FadeOut(m) for m in targets], run_time=run_time)

    def draw(self, *mobjects, run_time: float = 0.8, lag: float = 0.15):
        """Bring cards and arrows in, one after another."""
        anims = [Create(m) if isinstance(m, (Line, Arrow)) else FadeIn(m, shift=UP * 0.15) for m in mobjects]
        if anims:
            from manim import LaggedStart

            self.play(LaggedStart(*anims, lag_ratio=lag), run_time=run_time)
