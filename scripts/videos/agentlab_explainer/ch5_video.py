"""Chapter 5: how the explain task turns one approved source into one lesson.

Six stages run inside one Fargate task. Beat 1 shows them as the task's
inside; from beat 2 on they sit in a strip under the header, and each beat
lights the stage it explains before it shows that stage's own check:
grounded numbers, grounded beats, the import guard, the layout check, the
judge, the retry bounds, the honest fallback, and the delivered message.

Animations are cued to the words they show (`self.cue`), so a voice that
runs faster or slower than the preview estimate moves the visuals with it.
"""

import math
from itertools import pairwise

from kit import (
    ACCENT,
    BACKGROUND,
    GOLD,
    GREEN,
    INK,
    LINE,
    MUTED,
    PANEL,
    RED,
    ExplainerScene,
    chip,
    clock_icon,
    code_card,
    cpu_icon,
    doc_icon,
    faded,
    link,
    load_spec,
    loop_arrow,
    node,
    phone_icon,
    tile,
    travel,
    txt,
)
from manim import (
    DOWN,
    LEFT,
    PI,
    RIGHT,
    UP,
    AnimationGroup,
    Arc,
    ArcBetweenPoints,
    Arrow,
    Circle,
    Create,
    DashedLine,
    DashedVMobject,
    Dot,
    FadeIn,
    FadeOut,
    GrowFromCenter,
    GrowFromEdge,
    LaggedStart,
    Line,
    Polygon,
    Rectangle,
    ReplacementTransform,
    Rotate,
    RoundedRectangle,
    Transform,
    TransformFromCopy,
    VGroup,
)
from story_scene import CAPTION_SWAP_SECONDS

STAGES = ("Deep read", "Storyboard", "Scene code", "Render", "Judge", "Deliver")
STRIP_Y = 2.55
"""Height of the stage strip that sits under the header from beat 2 on."""
BIG = 1.2
"""The strip's scale in beat 1, where it is drawn as the explain task's inside."""
BREATH = 0.35
"""Silence make.py appends to every narration clip."""
TAIL = 0.8
"""Extra hold make.py adds to a chapter's last beat."""
DARK = "#0a0f18"
"""Background of code panels and film bands, as in the kit's code_card."""

LOOKS = {
    "idle": (PANEL, LINE, MUTED),
    "done": (PANEL, ACCENT, INK),
    "active": (ACCENT, ACCENT, BACKGROUND),
    "shipped": (GREEN, GREEN, BACKGROUND),
}
"""Strip pill looks: (fill, border, text)."""


# -- small pieces this chapter needs that the kit lacks ------------------------


def stage_chip(name: str) -> VGroup:
    """One strip pill. Labels sit on their cap height, so a name with a
    descender (Deep read, Judge) shares a baseline with one without."""
    label = txt(name, 16, MUTED, bold=True)
    pill = RoundedRectangle(
        corner_radius=0.21, width=label.width + 0.44, height=0.42, stroke_color=LINE, stroke_width=2, fill_color=PANEL, fill_opacity=1
    )
    cap = txt("H", 16, bold=True).height
    label.move_to(pill).align_to(pill.get_center() + UP * cap / 2, UP)
    return VGroup(pill, label)


def look(stage: VGroup, name: str) -> list:
    """Animations that give a strip pill one of the LOOKS."""
    fill, border, text = LOOKS[name]
    return [stage[0].animate.set_fill(fill, opacity=1).set_stroke(border), stage[1].animate.set_color(text)]


def set_look(stage: VGroup, name: str) -> None:
    """The same as look(), applied at once."""
    fill, border, text = LOOKS[name]
    stage[0].set_fill(fill, opacity=1).set_stroke(border)
    stage[1].set_color(text)


def arrow(start, end, color: str = LINE) -> Arrow:
    """A straight arrow between two points, in the kit's link style."""
    return Arrow(start, end, buff=0, color=color, stroke_width=3, max_tip_length_to_length_ratio=0.18, max_stroke_width_to_length_ratio=8)


def badge(ok: bool = True, r: float = 0.15) -> VGroup:
    """A filled disc with a check (GREEN) or a cross (RED)."""
    disc = Circle(radius=r, stroke_width=0, fill_color=GREEN if ok else RED, fill_opacity=1)
    if ok:
        a, b, c = [-0.45 * r, 0.02 * r, 0], [-0.12 * r, -0.32 * r, 0], [0.48 * r, 0.36 * r, 0]
        mark = VGroup(Line(a, b), Line(b, c))
    else:
        k = 0.36 * r
        mark = VGroup(Line([-k, -k, 0], [k, k, 0]), Line([-k, k, 0], [k, -k, 0]))
    mark.set_stroke(BACKGROUND, width=3.5)
    return VGroup(disc, mark)


def video_icon(w: float = 1.4, color: str = INK) -> VGroup:
    """A 16:9 frame with a play triangle: a video or a lesson."""
    h = w * 9 / 16
    frame = RoundedRectangle(corner_radius=min(0.12, h * 0.15), width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    s = h * 0.2
    tri = Polygon([-s * 0.8, s, 0], [-s * 0.8, -s, 0], [s, 0, 0], stroke_width=0, fill_color=color, fill_opacity=1)
    tri.move_to(frame.get_center() + RIGHT * s * 0.1)
    return VGroup(frame, tri)


def motif(progress: float, w: float = 0.62, color: str = GOLD) -> VGroup:
    """The storyboard's one visual idea: a dot riding a hill. `progress`
    (0 to 1) is how far along the hill the dot has come."""
    path = ArcBetweenPoints([-w / 2, -w * 0.12, 0], [w / 2, -w * 0.12, 0], angle=-PI / 2)
    path.set_stroke(MUTED, width=2)
    dot = Dot(path.point_from_proportion(0.04 + 0.92 * progress), radius=w * 0.085, color=color)
    return VGroup(path, dot)


def code_block(lines: list[str], size: int = 16) -> VGroup:
    """The kit's code_card, keeping each line's leading spaces as indentation."""
    advance = (txt("M" * 21, size, mono=True).width - txt("M", size, mono=True).width) / 20
    indents = [advance * (len(line) - len(line.lstrip())) for line in lines]
    widths = [txt(line.strip(), size, mono=True).width for line in lines]
    card = code_card([line.strip() for line in lines], w=max(indent + width for indent, width in zip(indents, widths)) + 0.6, size=size)
    for row, indent in zip(card[1], indents):
        row.shift(RIGHT * indent)
    return card


def code_chip(s: str) -> VGroup:
    """One line of code on its own, as it travels to the guard."""
    label = txt(s, 16, INK, mono=True)
    box = RoundedRectangle(corner_radius=0.1, width=label.width + 0.4, height=0.48, stroke_color=LINE, stroke_width=2, fill_color=DARK, fill_opacity=1)
    label.move_to(box)
    return VGroup(box, label)


def tree_icon(color: str = ACCENT, s: float = 0.34) -> VGroup:
    """A three-leaf syntax tree."""
    root = Dot([0, s / 2, 0], radius=0.05, color=color)
    leaves = [Dot([x, -s / 2, 0], radius=0.05, color=color) for x in (-s / 2, 0, s / 2)]
    edges = [Line(root.get_center(), leaf.get_center(), color=color, stroke_width=2) for leaf in leaves]
    return VGroup(*edges, root, *leaves)


def page_card(w: float = 1.9, h: float = 2.4, token_row: int = 3) -> VGroup:
    """The fetched page text: grey lines and one number, "27"."""
    fold = 0.32
    sheet = Polygon(
        [-w / 2, h / 2, 0], [w / 2 - fold, h / 2, 0], [w / 2, h / 2 - fold, 0], [w / 2, -h / 2, 0], [-w / 2, -h / 2, 0],
        stroke_color=INK, stroke_width=2.5, fill_color=PANEL, fill_opacity=1,
    )
    ear = Polygon([w / 2 - fold, h / 2, 0], [w / 2 - fold, h / 2 - fold, 0], [w / 2, h / 2 - fold, 0], stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1)
    left, room = -w / 2 + 0.24, w - 0.48
    lines = VGroup()
    token = txt("27", 16, INK, bold=True)
    for i, frac in enumerate((0.62, 0.95, 0.84, 0.0, 0.9, 0.7, 0.86)):
        y = h / 2 - 0.42 - i * 0.27
        if i == token_row:
            lines.add(Line([left, y, 0], [left + 0.42, y, 0]))
            token.move_to([left + 0.54 + token.width / 2, y, 0])
            lines.add(Line([token.get_right()[0] + 0.12, y, 0], [left + room * 0.92, y, 0]))
        else:
            lines.add(Line([left, y, 0], [left + room * frac, y, 0]))
    lines.set_stroke(MUTED, width=2.5)
    page = VGroup(sheet, ear, lines, token)
    page.token, page.lines = token, lines
    return page


def digest_card(w: float = 4.3) -> VGroup:
    """The digest's sections, with the numbers it quotes on their own row."""
    cap = txt("H", 16).height
    pitch = 0.42
    title = txt("digest", 24, INK, bold=True)
    title.align_to([0, 0, 0], UP + LEFT)
    rows, nums, limits = VGroup(), None, None
    y = -0.6
    for name in ("Headline", "What the paper shows", None, "Mechanism", "How it connects", "Limits"):
        if name is None:
            nums = VGroup(chip("27", INK, 16, filled=False), chip("30", INK, 16, filled=False)).arrange(RIGHT, buff=0.42)
            nums.move_to([0, y, 0]).align_to([0.42, 0, 0], LEFT)
            rows.add(nums)
        else:
            hashes = txt("##", 16, MUTED, mono=True)
            label = txt(name, 16, INK)
            label.next_to(hashes, RIGHT, buff=0.14).align_to(hashes, UP)
            row = VGroup(hashes, label)
            row.align_to([0, y + cap / 2, 0], UP).align_to([0, 0, 0], LEFT)
            rows.add(row)
            limits = row
        y -= pitch
    body = VGroup(title, rows)
    box = RoundedRectangle(corner_radius=0.14, width=w, height=body.height + 0.6, stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1)
    box.move_to(body).align_to(body.get_left() + LEFT * 0.32, LEFT)
    card = VGroup(box, body)
    card.box, card.n27, card.n30, card.limits = box, nums[0], nums[1], limits
    return card


def plan_card() -> VGroup:
    """The scene plan: a title, a "strict JSON" tag, and the JSON keys."""
    code = code_block(['{"title": "...",', ' "mechanism_steps": [...],', ' "key_numbers": [...]}'])
    title = txt("scene plan", 24, INK, bold=True)
    strict = chip("strict JSON", MUTED, 16, filled=False)
    head = VGroup(title, strict)
    title.align_to(code, LEFT)
    strict.align_to(code, RIGHT)
    head.next_to(code, UP, buff=0.18)
    strict.match_y(title)
    card = VGroup(head, code)
    card.title, card.strict, card.code = title, strict, code
    return card


def filmstrip(n: int = 10, solid: int = 8) -> VGroup:
    """A strip of n beat frames; frames past `solid` are optional (dashed).
    Every frame shows the same motif a little further along, plus a caption line."""
    cells = VGroup()
    for k in range(n):
        if k < solid:
            frame = RoundedRectangle(corner_radius=0.05, width=0.98, height=0.55, stroke_color=LINE, stroke_width=1.5, fill_color=PANEL, fill_opacity=1)
        else:
            outline = RoundedRectangle(corner_radius=0.05, width=0.98, height=0.55, stroke_color=MUTED, stroke_width=1.5)
            back = RoundedRectangle(corner_radius=0.05, width=0.98, height=0.55, stroke_width=0, fill_color=PANEL, fill_opacity=1)
            frame = VGroup(back, DashedVMobject(outline, num_dashes=22))
        scene = motif(k / (n - 1)).move_to(frame.get_center() + UP * 0.06)
        words = Line(LEFT * 0.22, RIGHT * 0.22, color=INK, stroke_width=2).move_to(frame.get_bottom() + UP * 0.09)
        if k >= solid:
            scene.fade(0.5)
            words.fade(0.5)
        cells.add(VGroup(frame, scene, words))
    cells.arrange(RIGHT, buff=0.16)
    band = RoundedRectangle(corner_radius=0.08, width=cells.width + 0.36, height=1.08, stroke_color=LINE, stroke_width=1.5, fill_color=DARK, fill_opacity=1)
    band.move_to(cells)
    holes = VGroup()
    x = band.get_left()[0] + 0.18
    while x < band.get_right()[0] - 0.12:
        for y in (band.get_top()[1] - 0.12, band.get_bottom()[1] + 0.12):
            holes.add(RoundedRectangle(corner_radius=0.02, width=0.1, height=0.07, stroke_width=0, fill_color=LINE, fill_opacity=1).move_to([x, y, 0]))
        x += 0.24
    strip = VGroup(band, holes, cells)
    strip.band, strip.holes, strip.cells = band, holes, cells
    return strip


def voice_block(w: float, seed: float, h: float = 0.5) -> VGroup:
    """One beat's narration clip: a box with a waveform."""
    box = RoundedRectangle(corner_radius=0.08, width=w, height=h, stroke_color=ACCENT, stroke_width=2, fill_color=ACCENT, fill_opacity=0.15)
    bars = VGroup()
    for i in range(int((w - 0.2) / 0.085)):
        amp = 0.03 + 0.13 * abs(math.sin(1.9 * i + seed)) * (0.5 + 0.5 * abs(math.sin(0.37 * i + 2 * seed)))
        bars.add(Line(UP * amp, DOWN * amp, color=ACCENT, stroke_width=2.5))
    bars.arrange(RIGHT, buff=0.085).move_to(box)
    return VGroup(box, bars)


def bubble(rows: VGroup) -> VGroup:
    """A Telegram-style message bubble around `rows`, its tail on the left."""
    box = RoundedRectangle(corner_radius=0.18, width=rows.width + 0.6, height=rows.height + 0.56, stroke_color=LINE, stroke_width=2, fill_color=PANEL, fill_opacity=1)
    box.move_to(rows).align_to(rows.get_left() + LEFT * 0.3, LEFT)
    corner = box.get_corner(DOWN + LEFT)
    tail = Polygon(
        corner + UP * 0.42 + RIGHT * 0.02, corner + LEFT * 0.22 + UP * 0.08, corner + UP * 0.18 + RIGHT * 0.02,
        stroke_width=0, fill_color=PANEL, fill_opacity=1,
    )
    edge = VGroup(
        Line(corner + UP * 0.42, corner + LEFT * 0.22 + UP * 0.08, color=LINE, stroke_width=2),
        Line(corner + LEFT * 0.22 + UP * 0.08, corner + UP * 0.18, color=LINE, stroke_width=2),
    )
    return VGroup(box, tail, edge)


def button(label: str, w: float) -> VGroup:
    """A Telegram inline-keyboard button."""
    text = txt(label, 16, INK, bold=True)
    box = RoundedRectangle(corner_radius=0.1, width=w, height=0.44, stroke_color=LINE, stroke_width=2, fill_color=PANEL, fill_opacity=1)
    text.move_to(box)
    return VGroup(box, text)


class Chapter(ExplainerScene):
    # -- timing ----------------------------------------------------------------

    def begin(self, beat: int) -> None:
        """Call first in every beat: remembers when this beat's voice starts."""
        if not hasattr(self, "narration_text"):
            self.narration_text = load_spec()["captions"]
        self.beat = beat
        self.voice_start = self.time - CAPTION_SWAP_SECONDS

    def cue(self, phrase: str) -> None:
        """Hold until the voice reaches `phrase`, assuming an even speaking rate."""
        text = self.narration_text[self.beat - 1]
        spoken = self.d(self.beat) - BREATH - (TAIL if self.beat == len(self.narration_text) else 0)
        at = self.voice_start + spoken * len(text[: text.index(phrase)].split()) / len(text.split())
        if at - self.time > 0.05:
            self.wait(at - self.time)

    def turn(self, beat: int, active: int | None, final: str = "active") -> None:
        """Start beats 3 to 9: fade the last beat's content and light this beat's stage."""
        self.begin(beat)
        kept = {id(self._caption), id(self.header), id(self.strip)}
        old = [m for m in self.mobjects if id(m) not in kept]
        for m in old:
            m.clear_updaters()
        self.play(*[FadeOut(m) for m in old], *self.looks(active, final), run_time=0.45)

    def looks(self, active: int | None, final: str = "active") -> list:
        """Strip animations: stages before `active` done, `active` in `final`, the rest idle."""
        anims = []
        for k, stage in enumerate(self.stages):
            name = "idle" if active is None or k > active else ("done" if k < active else final)
            anims += look(stage, name)
        return anims

    # -- beats -----------------------------------------------------------------

    def beat_1(self):
        self.begin(1)
        self.show_header(run_time=0.4)
        source = doc_icon(INK, h=1.0)
        task = tile(cpu_icon(ACCENT), "explain task", "Fargate task", ACCENT, w=5.3, h=1.3)
        lesson = video_icon(1.6)
        VGroup(source, task, lesson).arrange(RIGHT, buff=1.1).move_to(UP * 1.25)
        specs = VGroup(chip("4 vCPU", ACCENT, 16), chip("8 GB", ACCENT, 16, filled=False)).arrange(DOWN, buff=0.1)
        specs.move_to(task.box.get_right() + LEFT * (0.3 + specs.width / 2))
        names = VGroup(txt("one source", 18, MUTED), txt("one lesson", 18, MUTED))
        for name, icon in zip(names, (source, lesson)):
            name.move_to([icon.get_x(), 0.4, 0])
        into, out = link(source, task), link(task, lesson)

        self.stages = [stage_chip(name) for name in STAGES]
        row = VGroup(*self.stages).arrange(RIGHT, buff=0.36)
        joints = VGroup(*[Line(a.get_right() + RIGHT * 0.06, b.get_left() + LEFT * 0.06, color=LINE, stroke_width=2) for a, b in pairwise(row)])
        self.strip = VGroup(joints, *self.stages).scale(BIG).move_to(DOWN * 0.95)
        fan = VGroup(
            DashedLine(task.box.get_corner(DOWN + LEFT) + RIGHT * 0.2, self.strip.get_corner(UP + LEFT) + UP * 0.12, dash_length=0.08, color=LINE, stroke_width=2),
            DashedLine(task.box.get_corner(DOWN + RIGHT) + LEFT * 0.2, self.strip.get_corner(UP + RIGHT) + UP * 0.12, dash_length=0.08, color=LINE, stroke_width=2),
        )

        self.play(FadeIn(source, shift=RIGHT * 0.6), FadeIn(names[0]), run_time=0.35)
        self.draw(into, task, out, lesson, names[1], run_time=0.6)
        self.cue("four virtual")
        self.play(LaggedStart(*[GrowFromCenter(s) for s in specs], lag_ratio=0.35), run_time=0.4)
        self.play(Create(fan[0]), Create(fan[1]), FadeIn(self.strip, shift=UP * 0.1, lag_ratio=0.02), run_time=0.7)
        self.cue("turns one source")
        self.play(travel(into, ACCENT, run_time=0.3))
        self.play(LaggedStart(*[AnimationGroup(*look(s, "done")) for s in self.stages], lag_ratio=0.4), run_time=0.5)
        # The lagged group above wraps the strip's pills in a new top-level
        # group; re-adding the strip keeps it one mobject for later beats.
        self.remove(self.strip)
        self.add(self.strip)
        self.play(
            travel(out, GREEN, run_time=0.3),
            lesson[0].animate.set_stroke(GREEN, width=3.5),
            lesson[1].animate.set_fill(GREEN),
            run_time=0.5,
        )

    def beat_2(self):
        self.begin(2)
        self.clear(keep=[self.strip])
        target = self.strip.copy().scale(1 / BIG).move_to(UP * STRIP_Y)
        for k, stage in enumerate(target[1:]):
            set_look(stage, "active" if k == 0 else "idle")
        self.play(Transform(self.strip, target), run_time=0.6)

        digest = digest_card().move_to(RIGHT * 4.0).align_to(UP * 2.05, UP)
        page = page_card().move_to(LEFT * 5.05)
        page.shift(UP * (digest.n27.get_y() - page.token.get_y()))
        page_name = VGroup(txt("page text", 18, INK), txt("HTML stripped", 16, MUTED)).arrange(DOWN, buff=0.08)
        page_name.next_to(page, DOWN, buff=0.22)
        claude = node("Claude Sonnet", "deep read", GOLD, w=2.95, h=1.0).move_to(LEFT * 1.35 + UP * 1.35)
        y = claude.get_y()
        reads = VGroup(arrow([page.get_right()[0] + 0.12, y, 0], [claude.get_left()[0] - 0.12, y, 0]))
        reads.add(txt("reads", 16, MUTED).next_to(reads[0], UP, buff=0.08))
        to_digest = arrow([claude.get_right()[0] + 0.12, y, 0], [digest.get_left()[0] - 0.12, y, 0])
        plan = plan_card().next_to(claude, DOWN, buff=0.7)
        to_plan = arrow(claude.get_bottom() + DOWN * 0.1, plan.get_top() + UP * 0.1)

        self.play(FadeIn(page, shift=RIGHT * 0.3), FadeIn(page_name), run_time=0.5)
        self.cue("Claude Sonnet")
        self.draw(reads, claude, run_time=0.6)
        self.play(travel(reads, GOLD, run_time=0.5))
        self.cue("writes")
        self.draw(to_digest, digest, run_time=0.7)
        self.draw(to_plan, plan.title, plan.code, run_time=0.6)
        self.cue("strict")
        self.play(GrowFromCenter(plan.strict), run_time=0.35)

        self.cue("Every number")
        top, found = page.lines[0].get_y(), page.token.get_y()
        scan = Rectangle(width=page.width - 0.4, height=0.26, stroke_width=0, fill_color=GREEN, fill_opacity=0.25)
        scan.move_to([page.get_x() - 0.08, top, 0])
        self.play(FadeIn(scan), run_time=0.15)
        self.play(scan.animate.set_y(found), run_time=0.45)
        ok = badge(True, 0.13).move_to(digest.n27.get_corner(UP + RIGHT) + LEFT * 0.04 + DOWN * 0.02)
        self.play(
            page.token.animate.set_color(GREEN),
            digest.n27[0].animate.set_fill(GREEN, opacity=1).set_stroke(GREEN),
            digest.n27[1].animate.set_color(BACKGROUND),
            FadeIn(ok, scale=0.5),
            run_time=0.4,
        )
        self.play(scan.animate.set_fill(RED, opacity=0.25).set_y(top), run_time=0.2)
        self.play(scan.animate.set_y(page.lines[-1].get_y()), run_time=0.5)
        no = badge(False, 0.13).move_to(digest.n30.get_corner(UP + RIGHT) + LEFT * 0.04 + DOWN * 0.02)
        strike = Line(digest.n30.get_left() + RIGHT * 0.08, digest.n30.get_right() + LEFT * 0.08, color=RED, stroke_width=3)
        missing = txt("not in source", 16, RED).next_to(digest.n30, RIGHT, buff=0.3)
        self.play(
            FadeOut(scan),
            digest.n30[0].animate.set_stroke(RED),
            digest.n30[1].animate.set_color(RED),
            Create(strike),
            FadeIn(no, scale=0.5),
            FadeIn(missing, shift=LEFT * 0.1),
            run_time=0.45,
        )

        self.cue("and every digest")
        row = digest.limits
        mark = RoundedRectangle(corner_radius=0.1, width=row.width + 0.3, height=0.4, stroke_color=GREEN, stroke_width=2, fill_color=GREEN, fill_opacity=0.12)
        mark.move_to(row)
        tick = badge(True, 0.13).next_to(mark, RIGHT, buff=0.15)
        self.play(Create(mark), row[1].animate.set_color(GREEN), FadeIn(tick, scale=0.5), run_time=0.5)

    def beat_3(self):
        self.turn(3, 1)
        film = filmstrip().move_to(UP * 0.1)
        band, cells = film.band, film.cells
        icon = motif(0.62, w=1.0)
        words = VGroup(txt("one visual metaphor", 24, INK, bold=True), txt("carries the whole paper", 16, MUTED)).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
        inside = VGroup(icon, words).arrange(RIGHT, buff=0.35)
        box = RoundedRectangle(corner_radius=0.14, width=inside.width + 0.7, height=1.0, stroke_color=GOLD, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
        box.move_to(inside)
        card = VGroup(box, inside).align_to(band, LEFT).set_y(1.52)
        count = txt("8 to 10 narrated beats", 24, INK, bold=True).align_to(band, RIGHT).set_y(1.52)

        ticks, stubs = VGroup(), VGroup()
        rail_y = -1.3
        for k, cell in enumerate(cells):
            tick = badge(True, 0.13).move_to([cell.get_x(), band.get_bottom()[1] - 0.32, 0])
            stub = Line([cell.get_x(), rail_y, 0], [cell.get_x(), tick.get_bottom()[1], 0], color=GREEN, stroke_width=2)
            if k >= 8:
                tick.fade(0.5)
                stub.fade(0.5)
            ticks.add(tick)
            stubs.add(stub)
        digest = doc_icon(INK, h=0.6).move_to([band.get_left()[0] + 0.25, rail_y, 0])
        rail = Line([digest.get_right()[0] + 0.1, rail_y, 0], [cells[-1].get_x(), rail_y, 0], color=GREEN, stroke_width=2)
        rail_name = txt("each grounded in the digest", 16, MUTED).next_to(digest, DOWN, buff=0.16).align_to(digest, LEFT)

        self.play(FadeIn(card, shift=UP * 0.15), run_time=0.5)
        self.cue("carries")
        self.play(FadeIn(band), FadeIn(film.holes), *[FadeIn(c[0]) for c in cells[:8]], run_time=0.5)
        self.play(LaggedStart(*[TransformFromCopy(icon, c[1]) for c in cells[:8]], lag_ratio=0.12), run_time=1.0)
        self.cue("eight to ten")
        self.play(FadeIn(count, shift=LEFT * 0.15), *[FadeIn(VGroup(c[0], c[1])) for c in cells[8:]], run_time=0.5)
        self.cue("narrated")
        self.play(LaggedStart(*[Create(c[2]) for c in cells], lag_ratio=0.1), run_time=0.5)
        self.cue("each grounded")
        self.play(FadeIn(digest), Create(rail), FadeIn(rail_name), run_time=0.45)
        self.play(LaggedStart(*[AnimationGroup(Create(s), GrowFromCenter(t)) for s, t in zip(stubs, ticks)], lag_ratio=0.12), run_time=0.7)

    def beat_4(self):
        self.turn(4, 2)
        claude = node("Claude Sonnet", "writes the code", GOLD, w=2.95, h=1.0)
        script = code_block(["class PaperStory(StoryScene):", "  def beat_1(self):", "    self.play(FadeIn(Circle()))"])
        base = node("StoryScene", "small base class", MUTED, w=2.5, h=1.0)
        VGroup(claude, script, base).arrange(RIGHT, buff=1.0).move_to(UP * 1.3)
        writes, extends = link(claude, script, label="writes"), link(script, base, label="extends")

        gate = RoundedRectangle(corner_radius=0.08, width=0.18, height=1.75, stroke_color=ACCENT, stroke_width=2, fill_color=ACCENT, fill_opacity=0.45)
        gate.move_to([-1.6, -1.05, 0])
        gate_name = VGroup(tree_icon(), txt("syntax tree guard", 24, INK, bold=True)).arrange(RIGHT, buff=0.18).next_to(gate, UP, buff=0.18)
        good, bad = code_chip("from manim import ..."), code_chip("import os")
        good.move_to([-6.1 + good.width / 2, -0.62, 0])
        bad.move_to([-6.1 + bad.width / 2, -1.48, 0])
        runs = node("runs", "manim render", GREEN, w=2.3, h=0.9).move_to([4.95, -0.62, 0])
        ghost = faded(runs)
        passed = gate.get_right()[0] + 0.3 + good.width / 2
        ok = badge(True).move_to([passed + good.width / 2 + 0.3, -0.62, 0])
        ok_seg = RoundedRectangle(corner_radius=0.08, width=0.18, height=0.5, stroke_width=0, fill_color=GREEN, fill_opacity=1).move_to([gate.get_x(), -0.62, 0])
        no_seg = ok_seg.copy().set_fill(RED).set_y(-1.48)
        no = badge(False).move_to([gate.get_right()[0] + 0.32, -1.48, 0])
        error = txt("import not allowed: os", 16, RED, mono=True).next_to(no, RIGHT, buff=0.18)
        to_runs = arrow(ok.get_right() + RIGHT * 0.12, runs.get_left() + LEFT * 0.12)

        self.play(FadeIn(claude, shift=UP * 0.15), run_time=0.4)
        self.play(Create(writes[0]), FadeIn(writes[1]), FadeIn(script[0]), run_time=0.4)
        self.play(LaggedStart(*[FadeIn(line, shift=RIGHT * 0.15) for line in script[1]], lag_ratio=0.4), run_time=0.7)
        self.cue("against")
        self.draw(extends, base, run_time=0.6)
        self.cue("A syntax tree")
        self.play(FadeIn(gate), FadeIn(gate_name), FadeIn(good, shift=RIGHT * 0.2), FadeIn(bad, shift=RIGHT * 0.2), FadeIn(ghost), run_time=0.6)
        self.cue("allows only")
        self.play(good.animate.set_x(passed), run_time=0.7)
        self.play(good[0].animate.set_stroke(GREEN), FadeIn(ok_seg), FadeIn(ok, scale=0.5), run_time=0.3)
        self.play(bad.animate.set_x(gate.get_left()[0] - 0.08 - bad.width / 2), run_time=0.45)
        bounced = bad.copy().shift(LEFT * 0.14)
        bounced[0].set_stroke(RED)
        self.play(Transform(bad, bounced), FadeIn(no_seg), FadeIn(no, scale=0.5), FadeIn(error), run_time=0.35)
        self.cue("before anything runs")
        self.play(Create(to_runs), ReplacementTransform(ghost, runs), run_time=0.5)

    def beat_5(self):
        self.turn(5, 3)
        voice_w, anim_w = (1.35, 0.95, 1.6, 1.15), (0.9, 0.65, 1.0, 0.75)
        x0, vy, by, gap = -5.0, 0.95, 0.0, 0.05
        lefts = [x0 + sum(voice_w[:k]) + gap * k for k in range(4)]
        voices = VGroup(*[voice_block(w, k * 1.3).move_to([left + w / 2, vy, 0]) for k, (w, left) in enumerate(zip(voice_w, lefts))])
        anims = VGroup(*[
            RoundedRectangle(corner_radius=0.08, width=w, height=0.5, stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1)
            .move_to([x0 + sum(anim_w[:k]) + gap * k + w / 2, by, 0])
            for k, w in enumerate(anim_w)
        ])
        holds = VGroup(*[
            Rectangle(width=vw - aw, height=0.5, stroke_color=MUTED, stroke_width=1.5, fill_color=MUTED, fill_opacity=0.3)
            .move_to([left + aw + (vw - aw) / 2, by, 0])
            for vw, aw, left in zip(voice_w, anim_w, lefts)
        ])
        guides = VGroup(*[
            DashedLine([left + vw, vy + 0.38, 0], [left + vw, by - 0.38, 0], dash_length=0.06, color=MUTED, stroke_width=1.5)
            for vw, left in zip(voice_w, lefts)
        ])
        polly = txt("Polly", 24, INK, bold=True).move_to([x0 - 0.22, vy, 0], aligned_edge=RIGHT)
        beats = txt("beats", 24, INK, bold=True).move_to([x0 - 0.22, by, 0], aligned_edge=RIGHT)
        swatch = Rectangle(width=0.32, height=0.22, stroke_color=MUTED, stroke_width=1.5, fill_color=MUTED, fill_opacity=0.3)
        legend = VGroup(swatch, txt("held until its narration ends", 16, MUTED)).arrange(RIGHT, buff=0.15)
        legend.move_to([x0, -0.75, 0], aligned_edge=LEFT)

        frame = RoundedRectangle(corner_radius=0.08, width=4.3, height=2.42, stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1)
        frame.move_to([3.1, 0.4, 0])
        stage_name = txt("stage", 16, MUTED).next_to(frame, UP, buff=0.1, aligned_edge=LEFT)
        band = Rectangle(width=4.18, height=0.5, stroke_width=0, fill_color=BACKGROUND, fill_opacity=1).move_to(frame.get_bottom() + UP * 0.31)
        band_text = txt("captions burned in", 16, INK).move_to(band)
        ball = Circle(radius=0.22, stroke_color=GOLD, stroke_width=2.5, fill_color=GOLD, fill_opacity=0.35).move_to([1.8, 0.4, 0])
        square = RoundedRectangle(corner_radius=0.06, width=0.48, height=0.48, stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1)
        square.move_to([3.05, 0.4, 0])
        pointer = arrow(ball.get_right() + RIGHT * 0.12, square.get_left() + LEFT * 0.12)
        box = VGroup(RoundedRectangle(corner_radius=0.08, width=1.05, height=0.46, stroke_color=INK, stroke_width=2, fill_color=PANEL, fill_opacity=1), txt("label", 16, INK))
        box.move_to([4.35, 1.0, 0])
        fail = chip("render fails", RED, 16).next_to(frame, DOWN, buff=0.3)

        self.play(FadeIn(polly), LaggedStart(*[FadeIn(v, shift=RIGHT * 0.1) for v in voices], lag_ratio=0.25), run_time=0.8)
        self.cue("The base class")
        self.play(FadeIn(beats), LaggedStart(*[FadeIn(a) for a in anims], lag_ratio=0.2), run_time=0.5)
        self.cue("times")
        self.play(*[a.animate.set_x(left + w / 2) for a, w, left in zip(anims, anim_w, lefts)], run_time=0.5)
        self.play(*[GrowFromEdge(h, LEFT) for h in holds], run_time=0.4)
        self.play(*[Create(g) for g in guides], FadeIn(legend), run_time=0.35)
        self.cue("burns")
        self.play(FadeIn(frame), FadeIn(stage_name), FadeIn(ball), Create(pointer), FadeIn(square), FadeIn(box), run_time=0.5)
        self.play(FadeIn(band, shift=UP * 0.2), FadeIn(band_text, shift=UP * 0.2), run_time=0.4)
        self.cue("and fails")
        self.play(box.animate.set_x(5.62), run_time=0.8)
        self.play(box[0].animate.set_stroke(RED), frame.animate.set_stroke(RED), GrowFromCenter(fail), run_time=0.4)

    def beat_6(self):
        self.turn(6, 4)
        rows = VGroup()
        for b in range(2):
            frames = VGroup()
            for i in range(3):
                cell = RoundedRectangle(corner_radius=0.05, width=0.72, height=0.405, stroke_color=LINE, stroke_width=1.5, fill_color=PANEL, fill_opacity=1)
                frames.add(VGroup(cell, motif(0.15 + 0.14 * (3 * b + i), w=0.46).move_to(cell)))
            frames.arrange(RIGHT, buff=0.1)
            rows.add(VGroup(txt(f"beat {b + 1}", 16, MUTED), frames).arrange(RIGHT, buff=0.25))
        rows.arrange(DOWN, aligned_edge=RIGHT, buff=0.3).move_to([-4.55, 0.75, 0])
        per_beat = txt("3 frames per beat", 16, MUTED).next_to(rows, UP, buff=0.25).align_to(rows[0][1], LEFT)

        judge = node("Judge", "scores out of 10", GOLD, w=2.7, h=1.0).move_to([-0.9, 0.75, 0])
        into = link(rows, judge)
        story_cells = VGroup(*[RoundedRectangle(corner_radius=0.03, width=0.3, height=0.2, stroke_color=LINE, stroke_width=1.5, fill_color=PANEL, fill_opacity=1) for _ in range(3)]).arrange(RIGHT, buff=0.06)
        story_band = RoundedRectangle(corner_radius=0.05, width=story_cells.width + 0.16, height=0.36, stroke_color=LINE, stroke_width=1.5, fill_color=DARK, fill_opacity=1).move_to(story_cells)
        story = VGroup(VGroup(story_band, story_cells), txt("storyboard", 16, MUTED)).arrange(RIGHT, buff=0.2)
        story.next_to(judge, DOWN, buff=0.55)
        up = arrow(story[0].get_top() + UP * 0.1 + RIGHT * 0.0, [story[0].get_x(), judge.get_bottom()[1] - 0.1, 0])

        center, r = [3.75, 0.25, 0], 1.3

        def at(value: float, radius: float):
            angle = PI - value / 10 * PI
            return [center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle), 0]

        track = VGroup(
            Arc(radius=r, start_angle=PI, angle=-0.7 * PI, arc_center=center, stroke_color=LINE, stroke_width=7),
            Arc(radius=r, start_angle=0.3 * PI, angle=-0.3 * PI, arc_center=center, stroke_color=GREEN, stroke_width=7),
        )
        ticks = VGroup(*[Line(at(v, r - 0.24 if v in (0, 7, 10) else r - 0.17), at(v, r - 0.09), color=MUTED, stroke_width=2) for v in range(11)])
        marks = VGroup(
            txt("0", 18, MUTED).move_to(at(0, r) + DOWN * 0.0).shift(DOWN * 0.24),
            txt("10", 18, MUTED).move_to(at(10, r)).shift(DOWN * 0.24),
            txt("7", 20, GREEN, bold=True).move_to(at(7, r + 0.3)),
        )
        needle = Line(center, at(0, r - 0.3), color=INK, stroke_width=4)
        pivot = Dot(center, radius=0.07, color=INK)
        verdict = chip("PASS", GREEN, 16).move_to([center[0], center[1] - 0.5, 0])
        to_gauge = arrow([judge.get_right()[0] + 0.12, judge.get_y(), 0], [center[0] - math.sqrt(r * r - 0.25) - 0.15, judge.get_y(), 0])

        substance = node("weak substance", "not grounded in the storyboard", RED, w=3.7, h=1.0)
        retry = VGroup(loop_arrow(radius=0.24, color=GOLD), txt("retry", 24, INK, bold=True)).arrange(RIGHT, buff=0.18)
        cosmetic = node("weak cosmetics", "overlap, cut-off label", MUTED, w=3.2, h=1.0)
        ships = chip("still ships", GREEN, 16)
        outcome = VGroup(substance, retry, cosmetic, ships).arrange(RIGHT, buff=0.55)
        outcome[2:].shift(RIGHT * 0.25)
        outcome.move_to([0, -1.5, 0])
        to_retry, to_ships = link(substance, retry), link(cosmetic, ships)

        self.play(FadeIn(per_beat), LaggedStart(*[FadeIn(row, shift=RIGHT * 0.1) for row in rows], lag_ratio=0.35), run_time=0.8)
        self.cue("scored")
        self.draw(into, judge, story, up, run_time=0.8)
        self.play(travel(into, GOLD, run_time=0.4), travel(up, GOLD, run_time=0.4))
        self.cue("Seven")
        self.play(Create(track), FadeIn(ticks), FadeIn(marks), FadeIn(needle), FadeIn(pivot), Create(to_gauge), run_time=0.5)
        self.play(Rotate(needle, angle=-0.84 * PI, about_point=center), run_time=0.8)
        self.play(GrowFromCenter(verdict), run_time=0.3)
        self.cue("Weak substance")
        self.draw(substance, to_retry, retry, run_time=0.6)
        self.play(Rotate(retry[0], angle=-2 * PI, about_point=retry[0].get_center()), run_time=0.5)
        self.cue("Weak cosmetics")
        self.draw(cosmetic, to_ships, ships, run_time=0.6)

    def beat_7(self):
        self.turn(7, None)
        names = VGroup(
            txt("3 storyboards", 24, INK, bold=True),
            txt("4 scene attempts", 24, INK, bold=True),
            txt("15 minute deadline", 24, INK, bold=True),
        )
        for name, y in zip(names, (1.35, 0.3, -0.75)):
            name.move_to([-2.55, y, 0], aligned_edge=RIGHT)
        boards = VGroup(*[RoundedRectangle(corner_radius=0.05, width=0.6, height=0.34, stroke_color=GOLD, stroke_width=2, fill_color=PANEL, fill_opacity=1) for _ in range(3)])
        boards.arrange(RIGHT, buff=0.16).move_to([-2.2, 1.35, 0], aligned_edge=LEFT)
        tries = VGroup(*[RoundedRectangle(corner_radius=0.06, width=0.4, height=0.4, stroke_color=GOLD, stroke_width=2, fill_color=PANEL, fill_opacity=1) for _ in range(4)])
        tries.arrange(RIGHT, buff=0.16).move_to([-2.2, 0.3, 0], aligned_edge=LEFT)
        caps = VGroup(*[Line(UP * 0.28, DOWN * 0.28, color=INK, stroke_width=4).next_to(m, RIGHT, buff=0.2) for m in (boards, tries)])
        clock = clock_icon(INK, r=0.22).move_to([-2.2 + 0.22, -0.75, 0])
        bar = RoundedRectangle(corner_radius=0.08, width=2.2, height=0.3, stroke_color=ACCENT, stroke_width=2).next_to(clock, RIGHT, buff=0.2)
        fill = RoundedRectangle(corner_radius=0.08, width=2.2, height=0.3, stroke_width=0, fill_color=ACCENT, fill_opacity=0.6).move_to(bar)

        paper = doc_icon(RED, h=0.85).move_to([1.9, 1.35, 0])
        paper_name = txt("failed paper", 16, MUTED).next_to(paper, DOWN, buff=0.15)
        again = chip("1 more try", ACCENT, 16).move_to([4.9, 1.35, 0])
        release = link(paper, again, label="released once")
        loop = loop_arrow(radius=0.48, color=MUTED).move_to([1.9, -0.75, 0])
        strike = Line(loop.get_corner(DOWN + LEFT) + UP * 0.05 + RIGHT * 0.05, loop.get_corner(UP + RIGHT) + DOWN * 0.05 + LEFT * 0.05, color=RED, stroke_width=5)
        never = txt("never an endless loop", 18, RED).next_to(loop, RIGHT, buff=0.35)

        self.play(FadeIn(names, lag_ratio=0.2), FadeIn(boards), FadeIn(tries), FadeIn(clock), Create(bar), run_time=0.5)
        self.cue("three storyboards")
        self.play(
            LaggedStart(*[b.animate.set_fill(GOLD, opacity=0.8) for b in boards], lag_ratio=0.3),
            Create(caps[0]),
            *look(self.stages[1], "active"),
            run_time=0.6,
        )
        self.cue("four scene")
        self.play(
            LaggedStart(*[t.animate.set_fill(GOLD, opacity=0.8) for t in tries], lag_ratio=0.3),
            Create(caps[1]),
            *look(self.stages[1], "idle"),
            *look(self.stages[2], "active"),
            run_time=0.7,
        )
        self.cue("fifteen")
        # The deadline covers the whole task, so every stage lights.
        self.play(GrowFromEdge(fill, LEFT), *self.looks(5, "done"), run_time=0.9)
        self.cue("A failed paper")
        self.play(FadeIn(paper, shift=UP * 0.1), FadeIn(paper_name), run_time=0.4)
        self.cue("one more chance")
        self.draw(release, again, run_time=0.6)
        self.cue("never")
        self.play(FadeIn(loop), run_time=0.3)
        self.play(Create(strike), FadeIn(never, shift=LEFT * 0.1), run_time=0.4)

    def beat_8(self):
        self.turn(8, None)
        failed = video_icon(1.7, MUTED)
        failed_name = txt("no video survived", 16, MUTED).next_to(failed, DOWN, buff=0.18)
        lost = VGroup(failed, failed_name).move_to([-4.55, 1.05, 0])
        cross = badge(False, 0.2).move_to(failed.get_corner(UP + RIGHT))
        template = video_icon(1.7, MUTED)
        template_name = txt("template filler", 16, MUTED).next_to(template, DOWN, buff=0.18)
        filler = VGroup(template, template_name).move_to([-4.55, -1.15, 0])
        slash = Line(template.get_corner(DOWN + LEFT), template.get_corner(UP + RIGHT), color=RED, stroke_width=4)
        stamp = chip("never sent", RED, 16).move_to(template.get_center()).rotate(0.12)

        head = txt("No video today for the core track.", 18, INK)
        instead = txt("Read the full digest instead.", 16, INK)
        url = txt("digest link", 16, ACCENT)
        url.add(Line(url.get_corner(DOWN + LEFT), url.get_corner(DOWN + RIGHT), color=ACCENT, stroke_width=1.5).shift(DOWN * 0.05))
        reason = txt("What broke: quality gate failed after 4 attempts", 16, MUTED)
        lines = VGroup(head, instead, url, reason).arrange(DOWN, aligned_edge=LEFT, buff=0.18)
        phone = phone_icon(INK, h=1.9)
        lines.move_to([2.55, 0.15, 0])
        message = bubble(lines)
        phone.next_to(message, LEFT, buff=0.45).align_to(message, DOWN)

        self.play(FadeIn(lost, shift=UP * 0.1), run_time=0.4)
        self.play(GrowFromCenter(cross), run_time=0.3)
        self.cue("Daniel gets")
        self.play(FadeIn(phone, shift=UP * 0.1), FadeIn(message), run_time=0.45)
        self.play(FadeIn(head), FadeIn(instead), run_time=0.35)
        self.cue("digest link")
        self.play(FadeIn(url, shift=UP * 0.05), run_time=0.3)
        self.cue("the reason")
        self.play(FadeIn(reason, shift=UP * 0.05), run_time=0.35)
        self.cue("Template")
        self.play(FadeIn(filler, shift=UP * 0.1), run_time=0.35)
        self.cue("is never")
        self.play(Create(slash), GrowFromCenter(stamp), run_time=0.4)

    def beat_9(self):
        self.turn(9, 5, "shipped")
        thumb = RoundedRectangle(corner_radius=0.12, width=3.1, height=3.1 * 9 / 16, stroke_color=INK, stroke_width=2.5, fill_color=DARK, fill_opacity=1)
        scene = motif(0.88, w=1.8).move_to(thumb.get_center() + DOWN * 0.45)
        play = VGroup(Circle(radius=0.3, stroke_width=0, fill_color=GREEN, fill_opacity=1))
        play.add(Polygon([-0.09, 0.13, 0], [-0.09, -0.13, 0], [0.14, 0, 0], stroke_width=0, fill_color=BACKGROUND, fill_opacity=1).move_to(play[0].get_center() + RIGHT * 0.02))
        play.move_to(thumb.get_center() + UP * 0.15)
        video = VGroup(thumb, scene, play)
        cost = txt("Estimated model cost: $...", 18, GOLD)
        lines = VGroup(video, cost).arrange(DOWN, aligned_edge=LEFT, buff=0.22)
        message = bubble(lines)
        content = VGroup(message, lines)
        width = message[0].width
        top_row = VGroup(*[button(s, (width - 0.2) / 3) for s in ("COOL", "MEH", "SKIP")]).arrange(RIGHT, buff=0.1)
        bottom_row = VGroup(*[button(s, (width - 0.1) / 2) for s in ("CLEAR", "UNCLEAR")]).arrange(RIGHT, buff=0.1)
        keys = VGroup(top_row, bottom_row).arrange(DOWN, buff=0.1)
        keys.next_to(message[0], DOWN, buff=0.14)
        topic = txt("topic", 18, MUTED).next_to(top_row, RIGHT, buff=0.35)
        teaching = txt("teaching", 18, MUTED).next_to(bottom_row, RIGHT, buff=0.35)
        phone = phone_icon(INK, h=1.9)
        group = VGroup(content, keys, topic, teaching)
        phone.next_to(message[0], LEFT, buff=0.45).align_to(message[0], DOWN)
        everything = VGroup(phone, group)
        everything.move_to([0.0, -0.02, 0])

        self.play(FadeIn(phone, shift=UP * 0.1), FadeIn(message), FadeIn(video), run_time=0.6)
        self.cue("model cost")
        self.play(FadeIn(cost, shift=UP * 0.1), run_time=0.4)
        self.cue("two rows")
        self.play(LaggedStart(*[FadeIn(b, shift=UP * 0.1) for b in [*top_row, *bottom_row]], lag_ratio=0.12), run_time=0.6)
        for word, key, color in (("cool", top_row[0], GREEN), ("meh", top_row[1], MUTED), ("skip", top_row[2], RED)):
            self.cue(word)
            self.play(key[0].animate.set_stroke(color, width=3), key[1].animate.set_color(color), run_time=0.3)
        self.cue("topic")
        self.play(FadeIn(topic, shift=LEFT * 0.1), run_time=0.3)
        for word, key, color in (("clear", bottom_row[0], GREEN), ("unclear", bottom_row[1], RED)):
            self.cue(word)
            self.play(key[0].animate.set_stroke(color, width=3), key[1].animate.set_color(color), run_time=0.3)
        self.cue("teaching")
        self.play(FadeIn(teaching, shift=LEFT * 0.1), run_time=0.3)
