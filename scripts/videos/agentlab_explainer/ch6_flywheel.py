"""Chapter 6: the taste flywheel, the newest loop.

Episodes come from the ledger at read time, taps and silences alike. Golden
labels join as fixed examples and old episodes count half. Every Sunday a
hash holds 30 percent of the papers out, Claude rewrites the profile from
the rest, a probe gate compares the old and new profile on the held-out
papers, and one DynamoDB pointer names the live version, so one REVERT tap
undoes a swap.
"""

from types import SimpleNamespace

from kit import (
    ACCENT,
    GOLD,
    GREEN,
    INK,
    LINE,
    MUTED,
    PANEL,
    PURPLE,
    RED,
    ExplainerScene,
    bucket_icon,
    chip,
    clock_icon,
    db_icon,
    doc_icon,
    link,
    lit,
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
    Arc,
    Arrow,
    Circle,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    GrowFromEdge,
    LaggedStart,
    Line,
    MoveAlongPath,
    MoveToTarget,
    Rectangle,
    ReplacementTransform,
    Rotate,
    RoundedRectangle,
    Transform,
    Triangle,
    ValueTracker,
    VGroup,
    VMobject,
    normalize,
    rotate_vector,
    smooth,
)

WORDS_PER_SECOND = 2.6
"""make.py's preview guess at the voice; begin() rescales to the real voice."""
CAPTION_SWAP = 0.25
CHAPTER_TAIL = 0.8
MINUS = "\u2212"

BAR_X0, BAR_X1, BAR_SPAN, BAR_H = -2.2, 5.6, 0.15, 0.44
"""The rule bars in beats 8 and 9: x range on stage, metric span, height."""


# -- small pieces this chapter needs beyond the kit ---------------------------


def window(start: float, end: float):
    """A smooth rate function that only moves between start and end (0 to 1).
    Lets one self.play stagger many moves without a LaggedStart group."""

    def rate(t: float) -> float:
        return smooth(min(1.0, max(0.0, (t - start) / (end - start))))

    return rate


def check_mark(color: str = GREEN, size: float = 0.34) -> VMobject:
    mark = VMobject(stroke_color=color, stroke_width=6)
    mark.set_points_as_corners([[-0.5, 0.02, 0], [-0.14, -0.36, 0], [0.5, 0.42, 0]])
    return mark.scale(size)


def cross_mark(color: str = RED, size: float = 0.28) -> VGroup:
    return VGroup(
        Line([-0.5, 0.5, 0], [0.5, -0.5, 0], color=color, stroke_width=6),
        Line([-0.5, -0.5, 0], [0.5, 0.5, 0], color=color, stroke_width=6),
    ).scale(size)


def episode_mark(positive: bool, size: float = 0.28) -> RoundedRectangle:
    """One episode: a small square, green for a positive weight, red for a negative one."""
    color = GREEN if positive else RED
    return RoundedRectangle(
        corner_radius=0.06, width=size, height=size, stroke_color=color, stroke_width=2, fill_color=color, fill_opacity=0.8
    )


def video_icon(color: str = MUTED, w: float = 1.0) -> VGroup:
    screen = RoundedRectangle(
        corner_radius=0.1, width=w, height=w * 0.66, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
    )
    play = Triangle(stroke_width=0, fill_color=color, fill_opacity=1).rotate(-PI / 2).scale_to_fit_height(w * 0.3)
    play.move_to(screen.get_center() + RIGHT * w * 0.03)
    return VGroup(screen, play)


def lock_icon(color: str = MUTED, h: float = 0.5) -> VGroup:
    """An open padlock; `lock.shackle` shifts down by h * 0.2 to close it."""
    body = RoundedRectangle(
        corner_radius=0.06, width=h * 0.9, height=h * 0.62, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
    )
    arc = Arc(radius=h * 0.25, start_angle=0, angle=PI, color=color, stroke_width=2.5)
    legs = VGroup(
        Line(arc.get_start(), arc.get_start() + DOWN * h * 0.22, color=color, stroke_width=2.5),
        Line(arc.get_end(), arc.get_end() + DOWN * h * 0.22, color=color, stroke_width=2.5),
    )
    shackle = VGroup(arc, legs)
    shackle.move_to(body.get_top() + UP * (shackle.height / 2 - 0.04) + UP * h * 0.2)
    hole = Dot(radius=h * 0.07, color=color).move_to(body.get_center() + UP * h * 0.03)
    lock = VGroup(shackle, body, hole)
    lock.shackle = shackle
    lock.travel = h * 0.2
    return lock


def bin_card(title: str, color: str, w: float = 3.0, h: float = 1.6, icon=None) -> VGroup:
    """A box with a bold title near its top, an optional icon left of the
    title, and `card.slots_y`: the height where its marks sit."""
    box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    label = txt(title, 20, INK, bold=True)
    head_y = box.get_top()[1] - 0.42
    parts = [box, label]
    if icon is None:
        label.move_to([box.get_x(), head_y, 0])
    else:
        VGroup(icon, label).arrange(RIGHT, buff=0.18).move_to([box.get_x(), head_y, 0])
        icon.shift(UP * (head_y - icon[1].get_y()))
        parts.insert(1, icon)
    card = VGroup(*parts)
    card.box = box
    card.label = label
    return card


def grid_points(n: int, cols: int, center, step: float = 0.44) -> list:
    """Centres for n small marks in rows of `cols`, the last row centred."""
    rows = (n + cols - 1) // cols
    points = []
    for k in range(n):
        r, c = divmod(k, cols)
        in_row = min(cols, n - r * cols)
        points.append([center[0] + (c - (in_row - 1) / 2) * step, center[1] + ((rows - 1) / 2 - r) * step, 0])
    return points


def scoreboard(title: str, color: str, f1: float, golden: float, center) -> VGroup:
    """A small card with an F1 bar and a golden recall bar (bar length = value).
    `card.bars` is drawn separately so the bars can grow in."""
    box = RoundedRectangle(corner_radius=0.12, width=3.3, height=1.4, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    box.move_to(center)
    parts = [box, txt(title, 18, INK, bold=True).move_to(box.get_top() + DOWN * 0.28)]
    bars = VGroup()
    for k, (name, value) in enumerate((("F1", f1), ("golden recall", golden))):
        y = box.get_top()[1] - 0.72 - k * 0.38
        parts.append(txt(name, 16, MUTED).move_to([box.get_left()[0] + 0.25, y, 0], aligned_edge=LEFT))
        bar = Rectangle(width=value * 1.4, height=0.15, stroke_width=0, fill_color=color, fill_opacity=0.95)
        bars.add(bar.move_to([box.get_left()[0] + 1.68, y, 0], aligned_edge=LEFT))
    card = VGroup(*parts)
    card.box = box
    card.bars = bars
    return card


def value_text(value: float, size: int = 22, color: str = GREEN):
    return txt(f"{value:.2f}", size, color, bold=True)


class Chapter(ExplainerScene):
    # -- pacing -------------------------------------------------------------

    def begin(self, beat: int):
        """Start this beat's clock. Cue points follow the real voice; run
        times shrink when the voice is faster than the preview guess."""
        if not hasattr(self, "_words"):
            self._words = [len(text.split()) for text in load_spec()["captions"]]
        estimate = max(2.5, self._words[beat - 1] / WORDS_PER_SECOND)
        if beat == len(self._words):
            estimate += CHAPTER_TAIL
        ratio = self.d(beat) / estimate
        self._pace = min(1.0, ratio)
        self._cue = ratio
        self._t0 = self.time - CAPTION_SWAP

    def play(self, *animations, **kwargs):
        """Scene.play, with every run_time in this chapter scaled by begin()."""
        if "run_time" in kwargs:
            kwargs["run_time"] *= getattr(self, "_pace", 1.0)
        return super().play(*animations, **kwargs)

    def at(self, seconds: float):
        """Hold until `seconds` into the beat's narration, so a picture lands
        when its words are spoken."""
        rest = self._t0 + seconds * self._cue - self.time
        if rest > 0.02:
            self.wait(rest)

    def adopt(self, group, *mobjects):
        """Fold on-screen mobjects into `group`, so they move and clear with it."""
        self.remove(*mobjects)
        group.add(*mobjects)

    # -- beats ----------------------------------------------------------------

    def beat_1(self):
        self.begin(1)
        self.show_header()
        ring = loop_arrow(radius=1.28, color=PURPLE).move_to(UP * 0.4)
        hub = ring[0].get_arc_center()
        name = txt("taste flywheel", 28, INK, bold=True).move_to(hub)
        newest = chip("newest loop", PURPLE, 16, filled=False).next_to(ring, UP, buff=0.3)
        goal = txt("tomorrow's picks beat today's", 22, MUTED).next_to(ring, DOWN, buff=0.36)
        self.play(FadeIn(newest, shift=DOWN * 0.1), run_time=0.4)
        self.play(FadeIn(ring, scale=0.85), FadeIn(name), run_time=0.8)
        self.play(Rotate(ring, angle=-2 * PI, about_point=hub), run_time=1.6)
        self.at(4.1)
        self.play(FadeIn(goal, shift=UP * 0.12), run_time=0.5)

    def beat_2(self):
        self.begin(2)
        self.clear()
        ledger = VGroup(db_icon(MUTED, w=1.0, h=1.15), txt("ledger", 22, INK, bold=True), txt("DynamoDB", 16, MUTED))
        ledger.arrange(DOWN, buff=0.14).move_to([-5.0, 0.35, 0])
        panel = RoundedRectangle(
            corner_radius=0.14, width=6.6, height=4.3, stroke_color=PURPLE, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        ).move_to([1.85, 0.35, 0])
        left, top, right = panel.get_left()[0], panel.get_top()[1], panel.get_right()[0]
        title = txt("episodes", 24, INK, bold=True).move_to([left + 0.4, top - 0.42, 0], aligned_edge=LEFT)
        cols = (left + 0.4, left + 2.85, right - 0.95)
        head_y = top - 1.0
        heads = VGroup(
            txt("signal", 16, MUTED).move_to([cols[0], head_y, 0], aligned_edge=LEFT),
            txt("from", 16, MUTED).move_to([cols[1], head_y, 0], aligned_edge=LEFT),
            txt("weight", 16, MUTED).move_to([cols[2], head_y, 0]),
        )
        rule = Line([left + 0.3, head_y - 0.26, 0], [right - 0.3, head_y - 0.26, 0], color=LINE, stroke_width=1.5)
        specs = (
            ("APPROVED", "proposal tap", "+1", GREEN, True),
            ("REJECTED", "proposal tap", MINUS + "1", RED, True),
            ("COOL", "video rating", "+1", GREEN, True),
            ("MEH", "video rating", MINUS + "0.5", RED, False),
            ("SKIP", "video rating", MINUS + "1", RED, True),
        )
        rows = []
        for i, (signal, source, weight, color, strong) in enumerate(specs):
            y = head_y - 0.62 - i * 0.54
            rows.append(
                VGroup(
                    txt(signal, 20, INK, bold=True).move_to([cols[0], y, 0], aligned_edge=LEFT),
                    txt(source, 18, MUTED).move_to([cols[1], y, 0], aligned_edge=LEFT),
                    chip(weight, color, 18, filled=strong).move_to([cols[2], y, 0]),
                )
            )
        arrow = link(ledger, panel, label="derived at read time")
        self.draw(ledger, run_time=0.5)
        self.play(Create(arrow[0]), FadeIn(arrow[1]), run_time=0.5)
        self.play(FadeIn(VGroup(panel, title, heads, rule)), run_time=0.5)
        self.play(travel(arrow, PURPLE), run_time=0.7)
        for row, cue in zip(rows, (4.5, 6.4, 8.0, 9.5, 10.5)):
            self.at(cue)
            self.play(FadeIn(row, shift=LEFT * 0.15), run_time=0.45)

    def beat_3(self):
        self.begin(3)
        self.clear()
        dx = 0.3
        rows = []
        for y, icon, title, sub, hours, kind in (
            (1.3, doc_icon(MUTED), "Proposal", "no tap", 48, "IGNORED"),
            (-0.65, video_icon(MUTED), "Video", "no rating", 72, "UNRATED"),
        ):
            row = SimpleNamespace()
            row.card = tile(icon, title, sub, MUTED, w=3.0, h=1.1).move_to([-4.3 + dx, y, 0])
            row.clock = clock_icon(INK, r=0.36).move_to([-1.85 + dx, y, 0])
            row.anchor = row.clock.get_right() + RIGHT * 0.22
            row.hours = hours
            row.tracker = ValueTracker(0)
            row.label = txt("0 h", 26, INK, bold=True).move_to(row.anchor, aligned_edge=LEFT)
            row.arrow = Arrow(
                [-0.3 + dx, y, 0], [0.6 + dx, y, 0], buff=0, color=LINE, stroke_width=3, max_tip_length_to_length_ratio=0.25
            )
            row.kind = chip(kind, MUTED, 18, filled=False).move_to([1.55 + dx, y, 0])
            row.weight = chip(MINUS + "0.5", RED, 18, filled=False).move_to([3.25 + dx, y, 0])
            rows.append(row)
        bx = 3.95 + dx
        top_y, bottom_y = rows[0].weight.get_top()[1], rows[1].weight.get_bottom()[1]
        bracket = VGroup(
            Line([bx - 0.16, top_y, 0], [bx, top_y, 0]),
            Line([bx, top_y, 0], [bx, bottom_y, 0]),
            Line([bx, bottom_y, 0], [bx - 0.16, bottom_y, 0]),
        ).set_stroke(RED, 2.5)
        note = txt("weak\nnegative", 18, RED, bold=True).next_to(bracket, RIGHT, buff=0.2)

        for row, cue in zip(rows, (1.4, 4.2)):
            self.at(cue)
            self.play(FadeIn(row.card, shift=RIGHT * 0.15), run_time=0.4)
            self.play(FadeIn(row.clock), FadeIn(row.label), run_time=0.25)
            row.label.add_updater(
                lambda m, r=row: m.become(txt(f"{r.tracker.get_value():.0f} h", 26, INK, bold=True).move_to(r.anchor, aligned_edge=LEFT))
            )
            center = row.clock[0].get_center()
            self.play(
                Rotate(row.clock[2], angle=-4 * PI, about_point=center),
                Rotate(row.clock[1], angle=-2 * PI, about_point=center),
                row.tracker.animate.set_value(row.hours),
                run_time=0.9,
            )
            row.label.clear_updaters()
            self.play(Create(row.arrow), FadeIn(row.kind, shift=RIGHT * 0.1), run_time=0.45)
        self.at(6.9)
        self.play(*[FadeIn(row.weight, scale=1.2) for row in rows], run_time=0.45)
        self.play(Create(bracket), FadeIn(note, shift=LEFT * 0.1), run_time=0.5)

    def beat_4(self):
        self.begin(4)
        self.clear()
        axis_y, cut_x, now_x = -0.45, -2.55, 1.3
        axis = Line([-5.9, axis_y, 0], [now_x, axis_y, 0], color=LINE, stroke_width=3)
        ticks = VGroup(*[Line([x, axis_y - 0.13, 0], [x, axis_y + 0.13, 0], color=MUTED, stroke_width=3) for x in (cut_x, now_x)])
        tick_labels = VGroup(
            txt("60 days ago", 16, MUTED).next_to(ticks[0], DOWN, buff=0.12),
            txt("today", 16, MUTED).next_to(ticks[1], DOWN, buff=0.12),
        )
        pattern = (True, False, True, True, False, True, False, True, True, False, True, True, False, True)
        marks = VGroup(*[episode_mark(p).move_to([-5.55 + 0.5 * i, -0.05 + 0.38 * (i % 2), 0]) for i, p in enumerate(pattern)])
        old = [m for m in marks if m.get_x() < cut_x]
        title = txt("episodes", 22, INK, bold=True).move_to([(cut_x + now_x) / 2, 0.98, 0])

        stack = VGroup(*[doc_icon(GOLD, h=1.0).shift(RIGHT * 0.2 * k + UP * 0.14 * k) for k in (2, 1, 0)])
        stack.move_to([4.3, 0.35, 0])
        golden = VGroup(txt("26 golden labels", 22, INK, bold=True), txt("hand-labelled, fixed examples", 16, MUTED)).arrange(DOWN, buff=0.1)
        golden.next_to(stack, DOWN, buff=0.28)
        plus = txt("+", 40, MUTED, bold=True).move_to([2.55, 0.1, 0])

        x0, x1, by = old[0].get_left()[0], old[-1].get_right()[0], 0.68
        bracket = VGroup(
            Line([x0, by - 0.12, 0], [x0, by, 0]), Line([x0, by, 0], [x1, by, 0]), Line([x1, by, 0], [x1, by - 0.12, 0])
        ).set_stroke(PURPLE, 2.5)
        decay = VGroup(chip("\u00d70.5", PURPLE, 18, filled=False), txt("older than 60 days", 16, MUTED)).arrange(RIGHT, buff=0.18)
        decay.next_to(bracket, UP, buff=0.14)

        self.play(
            Create(axis), FadeIn(ticks), FadeIn(tick_labels), FadeIn(title), FadeIn(marks, lag_ratio=0.08), run_time=0.7
        )
        self.play(FadeIn(stack, shift=LEFT * 0.5), run_time=0.5)
        self.play(FadeIn(plus), FadeIn(golden, shift=UP * 0.1), run_time=0.4)
        self.at(3.45)
        self.play(*[m.animate.scale(0.7).set_opacity(0.45) for m in old], Create(bracket), run_time=0.55)
        self.play(FadeIn(decay, shift=DOWN * 0.1), run_time=0.4)

    def beat_5(self):
        self.begin(5)
        self.clear()
        clock = clock_icon(INK, r=0.34)
        trigger = VGroup(clock, txt("Sunday 18:00", 22, INK, bold=True)).arrange(RIGHT, buff=0.22)
        job = node("consolidate", "weekly job", ACCENT, w=2.5, h=0.85, size=22)
        VGroup(trigger, job).arrange(RIGHT, buff=1.3).move_to([0, 2.35, 0])
        kick = link(trigger, job, ACCENT)

        pattern = (True, False, True, True, False, True, True, False, True, True)
        pool = VGroup(*[episode_mark(p) for p in pattern]).arrange_in_grid(rows=2, cols=5, buff=0.18).move_to([-4.85, -0.25, 0])
        pool.set_z_index(3)
        tray = RoundedRectangle(corner_radius=0.14, width=pool.width + 0.44, height=pool.height + 0.44, stroke_color=LINE, stroke_width=2)
        tray.move_to(pool)
        tray_label = txt("episodes", 18, MUTED).next_to(tray, UP, buff=0.14)

        gate_box = RoundedRectangle(
            corner_radius=0.14, width=2.6, height=1.3, stroke_color=PURPLE, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        ).move_to([-1.4, -0.25, 0])
        gate_text = VGroup(txt("sha1(identity)", 18, INK, mono=True), txt("mod 10 < 3", 18, INK, mono=True)).arrange(DOWN, buff=0.12)
        gate = VGroup(gate_box, gate_text.move_to(gate_box)).set_z_index(5)
        feed = Arrow(tray.get_right(), gate_box.get_left(), buff=0.12, color=LINE, stroke_width=3, max_tip_length_to_length_ratio=0.25)

        lock = lock_icon(MUTED, 0.34)
        train = bin_card("training", PURPLE).move_to([2.75, 0.85, 0])
        held = bin_card("held-out", MUTED, icon=lock).move_to([2.75, -1.25, 0])
        never = txt("never seen by the model", 16, MUTED).move_to(held.box.get_bottom() + UP * 0.3)

        def branch(card, start_y: float, share: str, color: str, side) -> tuple:
            start = [gate_box.get_right()[0], start_y, 0]
            arrow = Arrow(start, card.box.get_left(), buff=0.12, color=LINE, stroke_width=3, max_tip_length_to_length_ratio=0.16)
            normal = rotate_vector(normalize(arrow.get_end() - arrow.get_start()), PI / 2 * side)
            tag = chip(share, color, 16).move_to(arrow.get_center() + normal * 0.48)
            return arrow, tag

        to_train, train_tag = branch(train, gate_box.get_y() + 0.2, "70%", PURPLE, 1)
        to_held, held_tag = branch(held, gate_box.get_y() - 0.2, "30%", MUTED, -1)

        held_ids = (1, 5, 8)
        top_train = train.box.get_top()[1]
        train_slots = iter(grid_points(7, 4, [train.box.get_x(), top_train - 1.05, 0], step=0.4))
        held_slots = iter(grid_points(3, 3, [held.box.get_x(), held.box.get_top()[1] - 0.9, 0], step=0.4))
        moves, train_marks, held_marks = [], [], []
        n = len(pool)
        departure = sorted(range(n), key=lambda i: (-round(pool[i].get_x(), 2), -pool[i].get_y()))
        for i, mark in enumerate(pool):
            out = i in held_ids
            arrow = to_held if out else to_train
            end = next(held_slots) if out else next(train_slots)
            (held_marks if out else train_marks).append(mark)
            path = VMobject().set_points_as_corners(
                [
                    mark.get_center(),
                    feed.get_start(),
                    feed.get_end(),
                    arrow.get_start(),
                    arrow.get_end(),
                    end,
                ]
            )
            start = departure.index(i) * 0.5 / n
            moves.append(MoveAlongPath(mark, path, rate_func=window(start, start + 0.5)))

        self.play(FadeIn(trigger, shift=RIGHT * 0.1), run_time=0.4)
        self.play(Rotate(clock[2], angle=-2 * PI, about_point=clock[0].get_center()), run_time=0.6)
        self.play(Create(kick[0]), FadeIn(job, shift=LEFT * 0.1), run_time=0.5)
        self.play(travel(kick, ACCENT), lit(job, ACCENT), run_time=0.6)
        self.play(FadeIn(tray), FadeIn(tray_label), FadeIn(pool), run_time=0.4)
        self.at(4.6)
        self.play(GrowFromEdge(feed, LEFT), FadeIn(gate, shift=DOWN * 0.1), run_time=0.5)
        self.play(
            FadeIn(train), FadeIn(held), Create(to_train), Create(to_held), FadeIn(train_tag), FadeIn(held_tag), run_time=0.5
        )
        self.play(*moves, run_time=2.6)
        self.adopt(train, *train_marks)
        self.adopt(held, *held_marks)
        flow = [gate, to_train, to_held, train_tag, held_tag, train, held]
        left_edge, right_edge = gate_box.get_left()[0], train.box.get_right()[0]
        recentre = LEFT * (left_edge + right_edge) / 2
        self.play(
            FadeOut(tray), FadeOut(tray_label), FadeOut(feed), *[m.animate.shift(recentre) for m in flow], run_time=0.45
        )
        never.shift(recentre)
        self.at(9.0)
        self.play(
            lock.shackle.animate.shift(DOWN * lock.travel),
            held.box.animate.set_stroke(color=INK),
            FadeIn(never, shift=UP * 0.08),
            run_time=0.5,
        )
        self.adopt(held, never)
        self.train, self.held, self.never = train, held, never

    def beat_6(self):
        self.begin(6)
        self.held.remove(self.never)
        self.add(self.never)
        self.clear(keep=[self.train, self.held])
        self.held.generate_target()
        box = self.held.target[0]
        box.become(
            RoundedRectangle(
                corner_radius=0.14, width=box.width, height=1.3, stroke_color=INK, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
            ).move_to(box.get_top(), aligned_edge=UP)
        )
        self.held.target.scale(0.8).move_to([-4.85, -1.3, 0])
        self.play(self.train.animate.scale(0.8).move_to([-4.85, 2.0, 0]), MoveToTarget(self.held), run_time=0.6)
        claude = node("Claude Sonnet", "rewrites the profile", GOLD, w=2.6, h=0.95, size=22).move_to([-4.85, 0.3, 0])
        feed = link(self.train, claude, PURPLE)
        doc = RoundedRectangle(
            corner_radius=0.14, width=8.7, height=4.7, stroke_color=PURPLE, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        ).move_to([1.8, 0.3, 0])
        write = link(claude, doc, GOLD)
        x0, top, right, bottom = doc.get_left()[0] + 0.4, doc.get_top()[1], doc.get_right()[0], doc.get_bottom()[1]
        title = txt("Daniel's taste profile", 22, INK, bold=True).move_to([x0, top - 0.38, 0], aligned_edge=LEFT)

        def head(name: str, color: str, x: float, y: float):
            return txt(name, 18, color, bold=True).move_to([x, y, 0], aligned_edge=LEFT)

        def bullet(width: float, x: float, y: float) -> VGroup:
            bar = RoundedRectangle(corner_radius=0.05, width=width, height=0.1, stroke_width=0, fill_color=INK, fill_opacity=0.55)
            bar.move_to([x + 0.05 + width / 2, y, 0])
            return VGroup(bar, txt("(evidence: \u2026)", 16, MUTED).next_to(bar, RIGHT, buff=0.15))

        y_pref = top - 0.9
        avoid_x = x0 + 4.25
        prefer = VGroup(head("Prefer", GREEN, x0, y_pref), *[bullet(w, x0, y_pref - 0.31 * (k + 1)) for k, w in enumerate((1.9, 1.5, 1.7))])
        avoid = VGroup(
            head("Avoid", RED, avoid_x, y_pref), *[bullet(w, avoid_x, y_pref - 0.31 * (k + 1)) for k, w in enumerate((1.6, 1.85, 1.3))]
        )
        y_ex = y_pref - 1.38
        examples_head = head("Examples", PURPLE, x0, y_ex)
        examples = []
        for k, (name, color) in enumerate(
            (
                ("Voyager: an open-ended embodied agent", GREEN),
                ("Constitutional AI: Harmlessness from AI Feedback", RED),
                ("Self-Evolving Agent Swarms", GREEN),
            )
        ):
            dot = Dot(radius=0.06, color=color).move_to([x0 + 0.08, y_ex - 0.32 * (k + 1), 0])
            examples.append(VGroup(dot, txt(name, 16, INK).next_to(dot, RIGHT, buff=0.18)))
        weights_head = head("Source weights", MUTED, x0, y_ex - 1.36)
        weights = VGroup(
            txt("arxiv: approval 0.20 (13 of 64)", 16, INK, mono=True),
            txt("github: approval 0.12 (2 of 17)", 16, INK, mono=True),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.12)
        weights.next_to(weights_head, DOWN, aligned_edge=LEFT, buff=0.14)

        marks_x = right - 0.5
        checks = [check_mark(GREEN, 0.3).move_to([marks_x, ex.get_y(), 0]) for ex in examples[:2]]
        cross = cross_mark(RED, 0.24).move_to([marks_x, examples[2].get_y(), 0])
        strike = Line(examples[2][1].get_left() + LEFT * 0.06, examples[2][1].get_right() + RIGHT * 0.06, color=RED, stroke_width=3)
        stamp_body = VGroup(check_mark(GREEN, 0.3), txt("format checked", 18, GREEN, bold=True)).arrange(RIGHT, buff=0.18)
        stamp_box = RoundedRectangle(
            corner_radius=0.12,
            width=stamp_body.width + 0.44,
            height=stamp_body.height + 0.26,
            stroke_color=GREEN,
            stroke_width=2.5,
            fill_color=PANEL,
            fill_opacity=1,
        ).move_to(stamp_body)
        stamp = VGroup(stamp_box, stamp_body).move_to([right - 0.35, title.get_y(), 0], aligned_edge=RIGHT)
        dropped = VGroup(cross_mark(RED, 0.16), txt("unknown example dropped", 16, RED)).arrange(RIGHT, buff=0.16)
        dropped.move_to(examples[2].get_left(), aligned_edge=LEFT)
        scan = Line(doc.get_left() + RIGHT * 0.15, doc.get_right() + LEFT * 0.15, color=GREEN, stroke_width=3)
        scan.move_to([doc.get_x(), top - 0.15, 0])

        self.draw(feed, claude, run_time=0.5)
        self.play(travel(feed, PURPLE), run_time=0.55)
        self.play(FadeIn(doc), FadeIn(title), Create(write[0]), run_time=0.5)
        self.play(travel(write, GOLD), run_time=0.5)
        self.at(3.5)
        self.play(FadeIn(prefer, shift=UP * 0.1), FadeIn(avoid, shift=UP * 0.1), run_time=0.6)
        self.at(5.3)
        self.play(
            FadeIn(examples_head), *[FadeIn(ex, shift=UP * 0.1) for ex in examples], FadeIn(weights_head), FadeIn(weights),
            run_time=0.6,
        )
        self.at(7.6)
        self.play(FadeIn(scan), run_time=0.15)
        self.play(scan.animate.move_to([doc.get_x(), bottom + 0.15, 0]), run_time=0.8)
        self.play(FadeOut(scan), *[FadeIn(c, scale=1.3) for c in checks], FadeIn(cross, scale=1.3), run_time=0.35)
        self.play(Create(strike), run_time=0.3)
        self.play(FadeOut(VGroup(examples[2], strike, cross), shift=RIGHT * 0.2), run_time=0.35)
        self.play(FadeIn(dropped, shift=RIGHT * 0.1), FadeIn(stamp, scale=1.15), run_time=0.4)

    def beat_7(self):
        self.begin(7)
        self.clear(keep=[self.held])
        held_left = -4.85 - self.held.width / 2
        gate = chip("the gate", PURPLE, 22, filled=False).move_to([held_left, 2.4, 0], aligned_edge=LEFT)
        self.play(FadeIn(gate, shift=DOWN * 0.1), self.held.animate.move_to([-4.85, 0.3, 0]), run_time=0.5)
        probe_box = RoundedRectangle(
            corner_radius=0.14, width=3.9, height=1.45, stroke_color=GOLD, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        ).move_to([-0.35, 0.3, 0])
        question = VGroup(txt("Would Daniel approve this?", 20, INK, bold=True), txt("YES or NO", 18, GOLD, mono=True))
        question.arrange(DOWN, buff=0.16).move_to(probe_box)
        probe = VGroup(probe_box, question)
        probe.box = probe_box
        ask = link(self.held, probe, PURPLE)
        self.at(1.4)
        self.play(FadeIn(probe, shift=UP * 0.1), Create(ask[0]), run_time=0.5)
        self.play(LaggedStart(*[travel(ask, PURPLE, run_time=0.5) for _ in range(3)], lag_ratio=0.3), run_time=0.9)
        self.at(3.8)
        self.play(lit(probe, GOLD), run_time=0.4)

        old_doc = doc_icon(MUTED, h=0.8).move_to([probe_box.get_x(), 2.15, 0])
        new_doc = doc_icon(PURPLE, h=0.8).move_to(old_doc)
        old_tag = txt("old profile", 18, MUTED).next_to(old_doc, RIGHT, buff=0.22)
        new_tag = txt("new profile", 18, PURPLE, bold=True).next_to(new_doc, RIGHT, buff=0.22)
        plug = link(old_doc, probe, MUTED)
        boards = (
            scoreboard("with old profile", MUTED, 0.19, 0.71, [4.55, 1.25, 0]),
            scoreboard("with new profile", PURPLE, 0.14, 0.71, [4.55, -0.7, 0]),
        )
        outs = [link(probe, board, LINE) for board in boards]

        self.at(5.6)
        self.play(FadeIn(old_doc, shift=DOWN * 0.1), FadeIn(old_tag), Create(plug[0]), run_time=0.35)
        self.play(travel(plug, PURPLE), run_time=0.35)
        self.play(Create(outs[0][0]), FadeIn(boards[0]), GrowFromEdge(boards[0].bars, LEFT), run_time=0.5)
        self.at(7.3)
        self.play(ReplacementTransform(old_doc, new_doc), ReplacementTransform(old_tag, new_tag), run_time=0.35)
        self.play(travel(plug, PURPLE), run_time=0.3)
        self.play(Create(outs[1][0]), FadeIn(boards[1]), GrowFromEdge(boards[1].bars, LEFT), run_time=0.5)

    def rule_row(self, name: str, rule: str, y: float, lo: float, old: float, tol: float) -> SimpleNamespace:
        """One swap rule as a bar: red where the new value fails, green where it passes."""

        def x_of(value: float) -> float:
            return BAR_X0 + (value - lo) / BAR_SPAN * (BAR_X1 - BAR_X0)

        row = SimpleNamespace(x_of=x_of, y=y)
        row.label = VGroup(txt(name, 26, INK, bold=True), txt(rule, 16, MUTED, mono=True)).arrange(DOWN, aligned_edge=LEFT, buff=0.14)
        row.label.move_to([-6.1, y, 0], aligned_edge=LEFT)
        row.frame = RoundedRectangle(corner_radius=0.06, width=BAR_X1 - BAR_X0, height=BAR_H, stroke_color=LINE, stroke_width=2)
        row.frame.move_to([(BAR_X0 + BAR_X1) / 2, y, 0])
        cut, mark = x_of(old - tol), x_of(old)
        row.fail = Rectangle(width=cut - BAR_X0, height=BAR_H, stroke_width=0, fill_color=RED, fill_opacity=0.32)
        row.fail.move_to([(BAR_X0 + cut) / 2, y, 0])
        row.keep = Rectangle(width=BAR_X1 - mark, height=BAR_H, stroke_width=0, fill_color=GREEN, fill_opacity=0.32)
        row.keep.move_to([(mark + BAR_X1) / 2, y, 0])
        row.slack = None
        if tol:
            row.slack = Rectangle(width=mark - cut, height=BAR_H, stroke_width=0, fill_color=GREEN, fill_opacity=0.16)
            row.slack.move_to([(cut + mark) / 2, y, 0])
            row.slack_label = txt(f"{tol:.2f}", 16, INK, bold=True).move_to(row.slack)
        row.tick = Line([mark, y - 0.36, 0], [mark, y + 0.36, 0], color=INK, stroke_width=3).set_z_index(2)
        row.old_label = txt("old", 16, INK).next_to(row.tick, DOWN, buff=0.1)
        row.parts = [row.label, row.frame, row.fail, row.keep, row.tick, row.old_label]
        if tol:
            row.parts += [row.slack, row.slack_label]
        return row

    def beat_8(self):
        self.begin(8)
        self.clear()
        title = txt("The new profile ships only if", 26, INK, bold=True).move_to([0, 2.42, 0])
        f1 = self.rule_row("F1", f"new \u2265 old {MINUS} 0.02", 1.0, lo=0.10, old=0.19, tol=0.02)
        golden = self.rule_row("golden recall", "new \u2265 old", -0.95, lo=0.65, old=0.71, tol=0.0)
        self.play(FadeIn(title, shift=DOWN * 0.1), run_time=0.4)
        for row, show_cue, zone_cue in ((f1, 1.5, 3.4), (golden, 6.5, 8.2)):
            self.at(show_cue)
            self.play(FadeIn(row.label), Create(row.frame), FadeIn(row.tick), FadeIn(row.old_label), run_time=0.6)
            self.at(zone_cue)
            self.play(GrowFromEdge(row.keep, LEFT), run_time=0.45)
            if row.slack is not None:
                self.play(GrowFromEdge(row.slack, RIGHT), FadeIn(row.slack_label), run_time=0.5)
            self.play(FadeIn(row.fail), run_time=0.3)
        self.rule_title, self.f1, self.golden = title, f1, golden

    def beat_9(self):
        self.begin(9)
        f1, golden = self.f1, self.golden
        tag = chip("first real run, live ledger, 49 held-out", PURPLE, 18, filled=False).move_to(self.rule_title)
        lifts = ((f1, 0.25), (golden, 0.75))
        self.play(
            FadeOut(self.rule_title, shift=UP * 0.1),
            FadeIn(tag, shift=UP * 0.1),
            *[part.animate.shift(UP * dy) for row, dy in lifts for part in row.parts],
            run_time=0.6,
        )
        for row, dy in lifts:
            row.y += dy

        def result(row, old: float, new: float, color: str):
            marker = Triangle(stroke_width=0, fill_color=color, fill_opacity=1).rotate(PI).scale_to_fit_height(0.2)
            marker.move_to([row.x_of(new), row.y + BAR_H / 2 + 0.16, 0])
            value = txt(f"new {new:.2f}", 20, color, bold=True).next_to(marker, UP, buff=0.08)
            old_value = txt(f"old {old:.2f}", 16, INK).move_to(row.old_label)
            return marker, value, old_value

        marker, value, old_value = result(f1, 0.19, 0.14, RED)
        self.at(1.9)
        self.play(FadeIn(marker, shift=DOWN * 0.2), FadeIn(value, shift=DOWN * 0.2), run_time=0.5)
        self.at(4.4)
        self.play(Transform(f1.old_label, old_value), run_time=0.4)
        cross = cross_mark(RED, 0.3).move_to([BAR_X1 + 0.45, f1.y, 0])
        self.play(FadeIn(cross, scale=1.3), f1.fail.animate.set_fill(opacity=0.5), run_time=0.35)

        marker, value, old_value = result(golden, 0.71, 0.71, GREEN)
        check = check_mark(GREEN, 0.36).move_to([BAR_X1 + 0.45, golden.y, 0])
        self.at(5.6)
        self.play(
            FadeIn(marker, shift=DOWN * 0.2), FadeIn(value, shift=DOWN * 0.2), Transform(golden.old_label, old_value), run_time=0.5
        )
        self.play(FadeIn(check, scale=1.3), run_time=0.3)

        bars_x = (BAR_X0 + BAR_X1) / 2
        verdict = chip("gate kept the old profile", PURPLE, 20).move_to([bars_x, -1.3, 0])
        note = txt("8 held-out positives", 16, MUTED)
        lesson = txt("more taps beat a smarter prompt", 22, INK, bold=True)
        VGroup(note, lesson).arrange(RIGHT, buff=0.45).move_to([bars_x, -1.9, 0])
        self.at(6.8)
        self.play(FadeIn(verdict, scale=1.15), run_time=0.45)
        self.at(8.9)
        self.play(FadeIn(note, shift=UP * 0.1), run_time=0.4)
        self.at(10.4)
        self.play(FadeIn(lesson, shift=UP * 0.1), run_time=0.5)

    def beat_10(self):
        self.begin(10)
        self.clear()
        s3 = VGroup(bucket_icon(MUTED, h=0.55), txt("S3", 22, INK, bold=True), txt("profile/<version>.md", 16, MUTED, mono=True))
        s3.arrange(RIGHT, buff=0.2).move_to([-3.6, 2.45, 0])
        versions = VGroup(
            *[VGroup(doc_icon(PURPLE, h=1.0), txt(f"v{k}", 18, INK, bold=True)).arrange(DOWN, buff=0.12) for k in (1, 2, 3)]
        ).arrange(RIGHT, buff=0.55).move_to([-3.6, 0.95, 0])
        db = db_icon(MUTED, w=0.8, h=0.9)
        item = VGroup(db, VGroup(txt("profile#current", 16, INK, mono=True), txt("DynamoDB pointer", 16, MUTED)).arrange(DOWN, aligned_edge=LEFT, buff=0.08))
        item.arrange(RIGHT, buff=0.22)
        item.shift([versions[1].get_x() - db.get_x(), -1.45 - db.get_y(), 0])

        def aim(target) -> Arrow:
            return Arrow(
                db.get_top() + UP * 0.04, target.get_bottom(), buff=0.1, color=PURPLE, stroke_width=4, max_tip_length_to_length_ratio=0.18
            )

        pointer = aim(versions[2])

        def diff_line(label: str, width: float) -> VGroup:
            words = txt(label, 16, INK)
            bar = RoundedRectangle(corner_radius=0.05, width=width, height=0.1, stroke_width=0, fill_color=INK, fill_opacity=0.55)
            return VGroup(words, bar.next_to(words, RIGHT, buff=0.15))

        heading = VGroup(phone_icon(INK, h=0.6), txt("Taste profile update.", 20, INK, bold=True)).arrange(RIGHT, buff=0.22)
        lines = VGroup(
            txt("Applied: yes. Tap REVERT to undo.", 16, INK),
            diff_line("Eval:", 2.4),
            diff_line("Prefer added:", 1.9),
            diff_line("Avoid removed:", 1.5),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.24)
        body = VGroup(heading, lines).arrange(DOWN, aligned_edge=LEFT, buff=0.32)
        button_box = RoundedRectangle(
            corner_radius=0.12, width=4.6, height=0.55, stroke_color=RED, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        )
        button = VGroup(button_box, txt("REVERT", 18, RED, bold=True).move_to(button_box))
        button.box = button_box
        stack = VGroup(body, button).arrange(DOWN, buff=0.42)
        card = RoundedRectangle(
            corner_radius=0.16, width=5.2, height=stack.height + 0.7, stroke_color=LINE, stroke_width=2, fill_color=PANEL, fill_opacity=1
        ).move_to(stack)
        VGroup(card, stack).move_to([3.55, 0.35, 0])
        callback = Line(button_box.get_left() + LEFT * 0.05, item.get_right() + RIGHT * 0.1)

        self.play(FadeIn(s3, shift=DOWN * 0.1), FadeIn(versions, lag_ratio=0.15), run_time=0.6)
        self.play(FadeIn(item, shift=UP * 0.1), run_time=0.4)
        self.play(GrowFromEdge(pointer, DOWN), versions[2][0][0].animate.set_stroke(width=4.5), run_time=0.5)
        self.at(3.4)
        self.play(FadeIn(card), FadeIn(heading), run_time=0.45)
        self.play(FadeIn(lines, shift=UP * 0.1, lag_ratio=0.2), FadeIn(button), run_time=0.7)
        self.at(6.3)
        ripple = Circle(radius=0.14, color=INK, stroke_width=3).move_to(button_box)
        self.add(ripple)
        self.play(ripple.animate.scale(3.2).set_stroke(opacity=0), lit(button, RED, 5), run_time=0.4)
        self.remove(ripple)
        self.play(travel(callback, RED, run_time=0.6), run_time=0.6)
        self.play(
            Transform(pointer, aim(versions[1])),
            versions[2][0][0].animate.set_stroke(width=2.5),
            versions[1][0][0].animate.set_stroke(width=4.5),
            run_time=0.6,
        )
        self.play(versions[2].animate.fade(0.5), run_time=0.35)

