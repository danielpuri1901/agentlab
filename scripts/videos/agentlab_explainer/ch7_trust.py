"""Chapter 7: why AgentLab can be trusted, then the closing recap.

Everything is Terraform, both images test themselves, every run reports,
the three loops close on one person's taps, and the title card ends it.
"""

from kit import (
    ACCENT,
    BACKGROUND,
    GOLD,
    GREEN,
    INK,
    LINE,
    MUTED,
    PANEL,
    PURPLE,
    RED,
    ExplainerScene,
    chip,
    faded,
    lit,
    load_spec,
    loop_arrow,
    node,
    phone_icon,
    travel,
    txt,
)
from manim import (
    DOWN,
    LEFT,
    ORIGIN,
    PI,
    RIGHT,
    UP,
    Arc,
    Arrow,
    Circle,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    LaggedStart,
    Line,
    Polygon,
    Rectangle,
    ReplacementTransform,
    Rotate,
    RoundedRectangle,
    Square,
    UpdateFromAlphaFunc,
    VGroup,
    VMobject,
)

CAPTION_SWAP = 0.25
"""The base class swaps the caption for this long before each beat method runs."""

BREATH = 0.35
TAIL = 0.8
"""make.py pads every beat by BREATH after its narration, and the last beat by TAIL more."""

TESTS = 500


def speech_weight(text: str) -> float:
    """Rough speaking time of a text: its characters plus a pause at punctuation."""
    return len(text) + 3 * text.count(".") + 2 * (text.count(",") + text.count(":"))


def painted(text: str, size: int, color: str, marks: dict[str, str], bold: bool = True):
    """A txt() line with some words in other colours. Glyph counts come from
    rendering the pieces, because spaces have no glyph and ligatures (the ff
    in "difference") merge two letters into one."""
    line = txt(text, size, color, bold=bold)
    for word, tint in marks.items():
        head = text[: text.index(word)].strip()
        start = len(txt(head, size, bold=bold)) if head else 0
        line[start : start + len(txt(word, size, bold=bold))].set_color(tint)
    return line


def arrow(start, end, color: str = LINE, buff: float = 0.12) -> VGroup:
    """An arrow between two points, styled like kit.link."""
    return VGroup(
        Arrow(start, end, buff=buff, color=color, stroke_width=3, max_tip_length_to_length_ratio=0.18, max_stroke_width_to_length_ratio=8)
    )


# -- small icons this chapter needs -----------------------------------------


def lock_icon(color: str = INK, h: float = 0.8) -> VGroup:
    body = RoundedRectangle(corner_radius=0.07, width=h * 0.8, height=h * 0.56, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    body.move_to(DOWN * h * 0.22)
    r, top = h * 0.22, body.get_top()[1]
    legs = VGroup(
        Line([-r, top, 0], [-r, top + h * 0.1, 0], color=color, stroke_width=3),
        Line([r, top, 0], [r, top + h * 0.1, 0], color=color, stroke_width=3),
    )
    bow = Arc(radius=r, start_angle=0, angle=PI, color=color, stroke_width=3).shift(UP * (top + h * 0.1))
    hole = VGroup(
        Dot(radius=h * 0.055, color=color).move_to(body.get_center() + UP * h * 0.03),
        Line(body.get_center(), body.get_center() + DOWN * h * 0.12, color=color, stroke_width=3),
    )
    icon = VGroup(VGroup(legs, bow), body, hole)
    icon.shackle = icon[0]
    return icon


def tick_mark(color: str = GREEN, w: float = 0.26) -> VMobject:
    mark = VMobject(stroke_color=color, stroke_width=4)
    mark.set_points_as_corners([[-0.5, 0.05, 0], [-0.15, -0.3, 0], [0.5, 0.35, 0]])
    return mark.scale_to_fit_width(w)


def cross_mark(color: str = RED, w: float = 0.22) -> VGroup:
    a = Line([-0.5, 0.5, 0], [0.5, -0.5, 0], color=color, stroke_width=4)
    b = Line([-0.5, -0.5, 0], [0.5, 0.5, 0], color=color, stroke_width=4)
    return VGroup(a, b).scale_to_fit_width(w)


def shield_icon(color: str = ACCENT, h: float = 0.85) -> VGroup:
    w = h * 0.78
    outline = Polygon(
        [-w / 2, h * 0.36, 0], [0, h / 2, 0], [w / 2, h * 0.36, 0], [w / 2, -h * 0.02, 0], [0, -h / 2, 0], [-w / 2, -h * 0.02, 0],
        stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1,
    )
    return VGroup(outline, tick_mark(INK, w * 0.48).move_to(UP * h * 0.02))


def coin_icon(color: str = GREEN, r: float = 0.38) -> VGroup:
    face = Circle(radius=r, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    return VGroup(face, txt("$", 30, color, bold=True).move_to(face))


def container_icon(color: str = INK, w: float = 0.95) -> VGroup:
    h = w * 0.6
    box = RoundedRectangle(corner_radius=0.06, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    ribs = VGroup(*[Line(UP * h * 0.28, DOWN * h * 0.28, color=color, stroke_width=2) for _ in range(5)])
    ribs.arrange(RIGHT, buff=w * 0.13).move_to(box)
    return VGroup(box, ribs)


def gauge(r: float = 0.5) -> VGroup:
    """A spend dial: grey track, red alarm zone at the top end, a needle at zero."""
    track = Arc(radius=r, start_angle=PI, angle=-PI, color=LINE, stroke_width=7)
    alarm = Arc(radius=r, start_angle=PI * 0.2, angle=-PI * 0.2, color=RED, stroke_width=7)
    needle = Line(ORIGIN, LEFT * r * 0.78, color=INK, stroke_width=3.5).set_z_index(2)
    hub = Dot(radius=0.055, color=INK).set_z_index(3)
    dial = VGroup(track, alarm, needle, hub)
    dial.radius, dial.needle, dial.hub = r, needle, hub
    return dial


def test_grid(cols: int = 50, rows: int = 10, step: float = 0.088, size: float = 0.056) -> VGroup:
    """TESTS small squares, filled column by column, so index order reads as progress."""
    cells = VGroup()
    for c in range(cols):
        for r in range(rows):
            cells.add(Square(side_length=size, stroke_width=0, fill_color=LINE, fill_opacity=1).move_to([c * step, -r * step, 0]))
    return cells


def message_bubble(lines, color: str = GREEN) -> VGroup:
    """A chat bubble with its tail on the left, pointing at the phone."""
    body = VGroup(*lines).arrange(DOWN, aligned_edge=LEFT, buff=0.1)
    box = RoundedRectangle(corner_radius=0.18, width=body.width + 0.6, height=body.height + 0.44, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    body.move_to(box)
    x, y = box.get_left()[0], box.get_center()[1] - 0.05
    cover = Polygon([x + 0.05, y + 0.15, 0], [x + 0.05, y - 0.15, 0], [x - 0.24, y - 0.2, 0], stroke_width=0, fill_color=PANEL, fill_opacity=1)
    edges = VGroup(
        Line([x, y + 0.15, 0], [x - 0.24, y - 0.2, 0], color=color, stroke_width=2.5),
        Line([x, y - 0.15, 0], [x - 0.24, y - 0.2, 0], color=color, stroke_width=2.5),
    )
    return VGroup(box, cover, edges, body)


def trust_row(icon, title: str, sub: str, color: str, detail, w: float = 11.2, h: float = 1.1) -> VGroup:
    """A wide card: icon, title and subtitle on the left, a small picture of the rule on the right."""
    box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    icon.move_to(box.get_left() + RIGHT * 0.8)
    words = VGroup(txt(title, 24, INK, bold=True), txt(sub, 16, MUTED)).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
    words.move_to(box.get_left() + RIGHT * (1.5 + words.width / 2))
    detail.move_to(box.get_center() + RIGHT * (1.15 + detail.width / 2))
    row = VGroup(box, icon, words, detail)
    row.box, row.detail = box, detail
    return row


class Chapter(ExplainerScene):
    # -- timing: each picture lands when the narration says it ---------------

    def begin(self, beat: int):
        self.beat = beat
        self.beat_start = self.time - CAPTION_SWAP

    def at(self, phrase: str, lead: float = 0.15):
        """Wait until the narration is about to say `phrase` (estimated from its place in the text)."""
        text = load_spec()["captions"][self.beat - 1]
        speech = self.d(self.beat) - BREATH - (TAIL if self.beat == len(self.durations) else 0.0)
        share = speech_weight(text[: text.index(phrase)]) / speech_weight(text)
        gap = self.beat_start + speech * share - lead - self.time
        if gap > 0.05:
            self.wait(gap)

    # -- 1: everything is code, secrets, roles, budget ------------------------

    def beat_1(self):
        self.begin(1)
        self.show_header()
        panel = RoundedRectangle(corner_radius=0.22, width=12.0, height=4.4, stroke_color=ACCENT, stroke_width=2).move_to(UP * 0.4)
        legend_text = "Terraform  ·  everything as code"
        legend = painted(legend_text, 24, MUTED, {"Terraform": INK})
        legend.move_to(panel.get_corner(UP + LEFT) + RIGHT * (0.55 + legend.width / 2))
        mask = Rectangle(width=legend.width + 0.4, height=legend.height + 0.2, stroke_width=0, fill_color=BACKGROUND, fill_opacity=1).move_to(legend)
        self.play(Create(panel), FadeIn(VGroup(mask, legend), shift=RIGHT * 0.15), run_time=0.9)

        secrets = VGroup()
        for name in ("bot-token", "webhook-secret"):
            key = txt(name, 16, MUTED, mono=True)
            dots = txt("••••••••", 16, INK, mono=True).move_to(key.get_left() + RIGHT * 2.25, aligned_edge=LEFT)
            secrets.add(VGroup(key, dots))
        secrets.arrange(DOWN, aligned_edge=LEFT, buff=0.14)
        grants = VGroup(
            VGroup(tick_mark(GREEN), txt("the actions it uses", 18, INK)).arrange(RIGHT, buff=0.22),
            VGroup(cross_mark(RED), txt("everything else", 18, MUTED)).arrange(RIGHT, buff=0.26),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.14)
        dial = gauge(0.56)
        alarm_tag = txt("alarm", 16, RED).next_to(dial[1], RIGHT, buff=0.18)
        budget = VGroup(dial, alarm_tag)

        lock = lock_icon(INK)
        rows = [
            trust_row(lock, "SSM Parameter Store", "every secret lives here", MUTED, secrets),
            trust_row(shield_icon(ACCENT), "IAM", "one role per task", ACCENT, grants),
            trust_row(coin_icon(GREEN), "Budget", "$50 monthly alarm", GREEN, budget),
        ]
        VGroup(*rows).arrange(DOWN, buff=0.2).move_to(panel.get_center() + DOWN * 0.12)
        lock.shackle.shift(UP * 0.1)

        self.at("Secrets live")
        self.play(FadeIn(VGroup(rows[0].box, rows[0][1], rows[0][2]), shift=UP * 0.15), run_time=0.5)
        self.play(lock.shackle.animate.shift(DOWN * 0.1), LaggedStart(*[FadeIn(s, shift=RIGHT * 0.1) for s in secrets], lag_ratio=0.3), run_time=0.6)

        self.at("each task role")
        self.play(FadeIn(VGroup(rows[1].box, rows[1][1], rows[1][2]), shift=UP * 0.15), run_time=0.5)
        self.play(LaggedStart(*[FadeIn(g, shift=RIGHT * 0.1) for g in grants], lag_ratio=0.45), run_time=0.7)

        self.at("and a fifty")
        self.play(FadeIn(VGroup(rows[2].box, rows[2][1], rows[2][2], dial[0], dial[1], dial.hub, dial.needle), shift=UP * 0.15), run_time=0.5)
        spend = 0.32
        hub = dial.hub.get_center()
        used = Arc(radius=dial.radius, start_angle=PI, angle=-PI * spend, color=GREEN, stroke_width=7, arc_center=hub)
        self.play(Create(used), Rotate(dial.needle, angle=-PI * spend, about_point=hub), run_time=0.9)
        self.play(FadeIn(alarm_tag, shift=LEFT * 0.1), run_time=0.35)

    # -- 2: two images, 500 tests each, one failure stops the build ------------

    def _image_card(self, title: str, sub: str) -> VGroup:
        w, h = 5.5, 3.55
        box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=LINE, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
        grid = test_grid().move_to(box.get_center() + DOWN * 0.3)
        left = grid.get_left()[0]
        words = VGroup(txt(title, 24, INK, bold=True), txt(sub, 16, MUTED, mono=True)).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
        head = VGroup(container_icon(INK), words).arrange(RIGHT, buff=0.3)
        head.move_to([left + head.width / 2, box.get_top()[1] - 0.38 - head.height / 2, 0])
        run = txt("RUN uv run pytest -q", 16, MUTED, mono=True)
        run.move_to([left + run.width / 2, (head.get_bottom()[1] + grid.get_top()[1]) / 2, 0])
        count = txt("0 passed", 20, MUTED, bold=True)
        count.move_to([left + count.width / 2, box.get_bottom()[1] + 0.42, 0])
        card = VGroup(box, head, run, grid, count)
        card.box, card.grid, card.count = box, grid, count
        return card

    def _run_tests(self, card) -> list:
        grid, count = card.grid, card.count
        anchor = count.get_left()

        def fill(cells, alpha):
            done = round(alpha * TESTS)
            for i, cell in enumerate(cells):
                cell.set_fill(GREEN if i < done else LINE)

        def tally(label, alpha):
            label.become(txt(f"{round(alpha * TESTS)} passed", 20, GREEN, bold=True))
            label.move_to(anchor, aligned_edge=LEFT)

        return [UpdateFromAlphaFunc(grid, fill), UpdateFromAlphaFunc(count, tally)]

    def beat_2(self):
        self.begin(2)
        self.clear()
        worker = self._image_card("worker image", "Dockerfile")
        video = self._image_card("video image", "Dockerfile.video")
        VGroup(worker, video).arrange(RIGHT, buff=0.5).move_to(UP * 0.3)
        self.play(LaggedStart(FadeIn(worker, shift=UP * 0.15), FadeIn(video, shift=UP * 0.15), lag_ratio=0.35), run_time=0.9)

        self.at("Both builds")
        self.play(*self._run_tests(worker), *self._run_tests(video), run_time=1.3)
        self.play(lit(worker, GREEN, 3), lit(video, GREEN, 3), run_time=0.35)

        self.at("and a single")
        bad = video.grid[337]
        ring = Circle(radius=0.17, stroke_color=RED, stroke_width=3.5).move_to(bad)
        self.play(bad.animate.set_fill(RED).scale(2.4), run_time=0.35)
        failed = txt("1 failed", 20, RED, bold=True).move_to(video.count.get_left(), aligned_edge=LEFT)
        stopped = chip("build stopped", RED, 16).move_to([video.box.get_right()[0] - 0.35, video.count.get_center()[1], 0], aligned_edge=RIGHT)
        self.play(
            bad.animate.scale(0.65),
            Create(ring),
            ReplacementTransform(video.count, failed),
            lit(video, RED, 3),
            FadeIn(stopped, shift=LEFT * 0.1),
            run_time=0.5,
        )

    # -- 3: silence must mean broken -------------------------------------------

    def beat_3(self):
        self.begin(3)
        self.clear()
        runs = VGroup(
            node("proposer", "daily, 9:30", ACCENT, w=2.9, h=0.9),
            node("explain task", "per approval", ACCENT, w=2.9, h=0.9),
            node("consolidate", "Sundays 18:00", ACCENT, w=2.9, h=0.9),
        ).arrange(DOWN, buff=0.28).move_to([-3.95, -0.6, 0])
        phone = phone_icon(INK, h=1.5).move_to([0.55, -0.6, 0])
        self.play(LaggedStart(*[FadeIn(r, shift=RIGHT * 0.15) for r in runs], FadeIn(phone), lag_ratio=0.2), run_time=0.8)

        self.at("silence must")
        rule_text = "silence must mean broken"
        rule = painted(rule_text, 40, INK, {"broken": RED}).move_to(UP * 1.95)
        self.play(FadeIn(rule, shift=UP * 0.15), run_time=0.7)

        self.at("Every run")
        ends = [phone.get_left() + UP * 0.45, phone.get_left(), phone.get_left() + DOWN * 0.45]
        arrows = [arrow(r.get_right(), end, buff=0.15) for r, end in zip(runs, ends, strict=True)]
        self.play(*[Create(a[0]) for a in arrows], run_time=0.5)
        self.play(*[travel(a, GREEN, run_time=0.65) for a in arrows])

        self.at("even when")
        bubble = message_bubble([txt("The proposer ran.", 16, MUTED), txt("Nothing new today.", 22, INK, bold=True)])
        bubble.next_to(phone, RIGHT, buff=0.45)
        self.play(FadeIn(bubble, shift=RIGHT * 0.15), phone[0].animate.set_stroke(color=GREEN), run_time=0.5)
        pings = chip("every run reports", GREEN, 16).next_to(bubble, DOWN, buff=0.3).align_to(bubble, LEFT)
        self.play(FadeIn(pings, shift=UP * 0.1), run_time=0.4)

    # -- 4: the three loops close on one person's taps -------------------------

    def beat_4(self):
        self.begin(4)
        self.clear()
        specs = [("Experiments", "experiment lab", ACCENT), ("Lessons", "lesson factory", GOLD), ("Taste", "taste loop", PURPLE)]
        loops = []
        for name, role, color in specs:
            ring = loop_arrow(radius=0.85, color=color)  # adding the tip grows the arc to about 1.07
            label = VGroup(txt(name, 22, INK, bold=True), txt(role, 16, MUTED)).arrange(DOWN, buff=0.1)
            label.move_to(ring[0].get_arc_center())
            loops.append(VGroup(ring, label))
        for loop, x in zip(loops, (-4.2, 0.0, 4.2), strict=True):
            loop.shift([x, 1.6, 0] - loop[0][0].get_arc_center())
        phone = phone_icon(INK, h=1.15).move_to(DOWN * 0.95)
        ghosts = [faded(loop) for loop in loops]
        ghost_phone = faded(phone)
        self.play(*[FadeIn(g) for g in ghosts], FadeIn(ghost_phone), run_time=0.5)

        for loop, ghost, cue in zip(loops, ghosts, ("An experiment", "a lesson", "and a taste"), strict=True):
            self.at(cue)
            self.play(ReplacementTransform(ghost, loop), run_time=0.5)

        self.at("all closing")
        starts = [loop[0].get_bottom() for loop in loops]
        ends = [phone.get_left() + UP * 0.15, phone.get_top(), phone.get_right() + UP * 0.15]
        arrows = [arrow(s, e, buff=0.14) for s, e in zip(starts, ends, strict=True)]
        self.play(*[Create(a[0]) for a in arrows], ReplacementTransform(ghost_phone, phone), run_time=0.45)
        taps = txt("one person’s taps", 20, INK, bold=True).next_to(phone, DOWN, buff=0.22)
        ripple = Circle(radius=0.32, stroke_color=GREEN, stroke_width=3).move_to(phone)
        self.play(
            *[travel(a, color, run_time=0.75) for a, (_, _, color) in zip(arrows, specs, strict=True)],
            *[Rotate(loop[0], angle=-2 * PI, about_point=loop[0][0].get_arc_center()) for loop in loops],
            phone[0].animate.set_stroke(color=GREEN),
            FadeIn(taps, shift=UP * 0.1),
            FadeOut(ripple, scale=2.6),
            run_time=1.0,
        )

    # -- 5: the closing lines and the title card -------------------------------

    def beat_5(self):
        self.begin(5)
        self.header = None  # the end card has no chapter header, so clear() fades it too
        self.clear()
        lines = [
            ("Approve what you want to learn.", "Approve", GREEN),
            ("Ignore what you don’t.", "Ignore", RED),
            ("The lab learns the difference.", "learns the difference", PURPLE),
        ]
        rows = VGroup(*[painted(s, 32, INK, {word: color}) for s, word, color in lines])
        rows.arrange(DOWN, buff=0.34).move_to(DOWN * 0.55)
        title = txt("AgentLab", 96, INK, bold=True).move_to(UP * 1.75)
        bar = Line(LEFT * 2.4, RIGHT * 2.4, color=ACCENT, stroke_width=6).next_to(title, DOWN, buff=0.22)

        self.play(FadeIn(rows[0], shift=UP * 0.15), run_time=0.5)
        self.at("Ignore")
        self.play(FadeIn(rows[1], shift=UP * 0.15), run_time=0.5)
        self.at("The lab")
        self.play(FadeIn(rows[2], shift=UP * 0.15), run_time=0.5)
        self.wait(0.35)
        self.play(FadeIn(title, shift=UP * 0.3), Create(bar), run_time=0.8)
