"""Chapter 2: the experiment loop.

A candidate must beat the baseline on the same tasks. SQS, an EventBridge
Pipe and Step Functions run both arms as Fargate tasks, a finalizer runs a
paired t-test, and the verdict lands in the ledger, in S3 and on the phone.
"""

import math

from kit import (
    ACCENT,
    GOLD,
    GREEN,
    INK,
    LINE,
    MUTED,
    PANEL,
    RED,
    ExplainerScene,
    bucket_icon,
    chip,
    cpu_icon,
    db_icon,
    doc_icon,
    faded,
    link,
    lit,
    load_spec,
    node,
    phone_icon,
    queue_icon,
    tile,
    travel,
    txt,
)
from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    Arrow,
    Create,
    DashedLine,
    Dot,
    FadeIn,
    FadeOut,
    GrowFromEdge,
    GrowFromPoint,
    LaggedStart,
    Line,
    MoveAlongPath,
    Polygon,
    Rectangle,
    ReplacementTransform,
    RoundedRectangle,
    Succession,
    UpdateFromAlphaFunc,
    VGroup,
    VMobject,
    smooth,
    there_and_back,
)

WORDS_PER_SECOND = 2.6
"""make.py's preview speech rate. Every run time below is tuned to it."""
CAPTION_SWAP = 0.25
END_HOLD = 0.5
CHAPTER_TAIL = 0.8

# Illustrative per-task scores for the paired comparison (no numbers are shown).
BASELINE = [0.52, 0.40, 0.61, 0.47, 0.36, 0.60, 0.44, 0.55]
CANDIDATE = [0.69, 0.59, 0.69, 0.60, 0.51, 0.56, 0.66, 0.65]
T_CRIT_DF7 = 2.365
"""Two-sided 95 percent t critical value for 8 pairs (7 degrees of freedom)."""


def flask_icon(color: str = ACCENT, h: float = 0.8) -> VGroup:
    """A lab flask with liquid in it: the emblem of the experiment lab."""
    neck, base = h * 0.28, h * 0.84
    top, shoulder, bottom = h / 2, h * 0.04, -h / 2
    cut = 0.07
    glass = Polygon(
        [-neck / 2, top, 0], [neck / 2, top, 0], [neck / 2, shoulder, 0],
        [base / 2, bottom + cut, 0], [base / 2 - cut, bottom, 0],
        [-base / 2 + cut, bottom, 0], [-base / 2, bottom + cut, 0], [-neck / 2, shoulder, 0],
        stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1,
    )

    def inner(y: float) -> float:
        """Half the glass's inner width at height y, on the flared part."""
        frac = (shoulder - y) / (shoulder - bottom - cut)
        return neck / 2 + frac * (base - neck) / 2 - 0.07

    level, floor = -h * 0.14, bottom + cut + 0.05
    liquid = Polygon(
        [-inner(level), level, 0], [inner(level), level, 0],
        [inner(floor), floor, 0], [inner(floor) - cut, bottom + 0.06, 0],
        [-inner(floor) + cut, bottom + 0.06, 0], [-inner(floor), floor, 0],
        stroke_width=0, fill_color=color, fill_opacity=0.6,
    )
    lip = Line([-neck / 2 - 0.07, top, 0], [neck / 2 + 0.07, top, 0], color=color, stroke_width=3.5)
    return VGroup(glass, liquid, lip)


def path_through(*points) -> VMobject:
    """A polyline through (x, y) points, for elbow connectors."""
    return VMobject().set_points_as_corners([[x, y, 0] for x, y in points])


def ride(path, color: str = ACCENT, radius: float = 0.075):
    """kit.travel along any path: a glowing dot rides it, then fades."""
    dot = Dot(radius=radius, color=color)
    halo = Dot(radius=radius * 2.2, color=color, fill_opacity=0.25)
    pair = VGroup(halo, dot).move_to(path.get_start())
    return Succession(
        FadeIn(pair, run_time=0.1),
        MoveAlongPath(pair, path, run_time=0.8, rate_func=smooth),
        FadeOut(pair, run_time=0.15),
    )


def short_arrow(start, end, color: str = LINE) -> Arrow:
    """An arrow too short for kit.link's tip ratio, with a full-size tip."""
    return Arrow(start, end, buff=0, color=color, stroke_width=3, tip_length=0.16, max_tip_length_to_length_ratio=0.5)


def tick_mark(color: str = GREEN, size: float = 0.26) -> VMobject:
    return path_through((-size / 2, 0), (-size / 8, -size * 0.4), (size / 2, size * 0.42)).set_stroke(color, 4)


def task_row(n: int = 8, size: float = 0.26, buff: float = 0.1, color: str = LINE) -> VGroup:
    """A row of small task squares."""
    return VGroup(
        *[
            RoundedRectangle(
                corner_radius=size * 0.18, width=size, height=size, stroke_color=color, stroke_width=2, fill_color=PANEL, fill_opacity=1
            )
            for _ in range(n)
        ]
    ).arrange(RIGHT, buff=buff)


def state_card(title: str, role: str, color: str, w: float = 3.4, h: float = 1.4) -> VGroup:
    """A Step Functions state: a compute chip, its name and role. `card.tag`
    is the "Fargate · ARM64" line, placed inside the card but shown later."""
    box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    icon = cpu_icon(ACCENT, 0.46).move_to(box.get_left() + RIGHT * 0.62)
    name = txt(title, 24, INK, bold=True)
    role_text = txt(role, 16, MUTED)
    tag = txt("Fargate · ARM64", 16, ACCENT)
    texts = VGroup(name, role_text, tag).arrange(DOWN, aligned_edge=LEFT, buff=0.1)
    texts.move_to(box).align_to(icon, LEFT).shift(RIGHT * (icon.width + 0.3))
    card = VGroup(box, icon, name, role_text)
    card.box, card.tag = box, tag
    return card


def interval(lo: float, hi: float, y: float, color: str, thick: float = 8, cap: float = 0.15) -> VGroup:
    """A confidence interval: a thick bar with end caps."""
    return VGroup(
        Line([lo, y, 0], [hi, y, 0], color=color, stroke_width=thick),
        Line([lo, y - cap, 0], [lo, y + cap, 0], color=color, stroke_width=4),
        Line([hi, y - cap, 0], [hi, y + cap, 0], color=color, stroke_width=4),
    )


def swatch(color: str, name: str) -> VGroup:
    """A legend entry: a colour square and its name."""
    square = RoundedRectangle(corner_radius=0.04, width=0.26, height=0.26, stroke_width=0, fill_color=color, fill_opacity=1)
    return VGroup(square, txt(name, 18, INK)).arrange(RIGHT, buff=0.14)


def event_pill(name: str) -> VGroup:
    """One ledger event: light text in a muted outline."""
    label = txt(name, 16, INK)
    pill = RoundedRectangle(
        corner_radius=0.2, width=label.width + 0.42, height=0.42, stroke_color=MUTED, stroke_width=2, fill_color=PANEL, fill_opacity=1
    )
    return VGroup(pill, label.move_to(pill))


def bracket_under(group, color: str = LINE, gap: float = 0.18, rise: float = 0.12) -> VMobject:
    """A thin bracket under a group, ends turned up."""
    y = group.get_bottom()[1] - gap
    left, right = group.get_left()[0], group.get_right()[0]
    return path_through((left, y + rise), (left, y), (right, y), (right, y + rise)).set_stroke(color, 2)


class Chapter(ExplainerScene):
    def pace(self, beat: int) -> None:
        """Fit this beat's run times to its narration.

        Run times are tuned against the preview, where a beat lasts its word
        count at 2.6 words a second. The final render uses the real voice, so
        every run time is scaled by the same factor and each animation still
        lands on its words and ends before the voice does.
        """
        captions = load_spec()["captions"]
        words = len(captions[beat - 1].split())
        planned = max(2.5, words / WORDS_PER_SECOND) + (CHAPTER_TAIL if beat == len(captions) else 0.0)
        room = self.d(beat) - CAPTION_SWAP - END_HOLD
        self._k = min(1.3, room / (planned - CAPTION_SWAP - END_HOLD))

    def t(self, seconds: float) -> float:
        return seconds * self._k

    # -- 1: a candidate must beat the baseline on the same tasks -------------

    def beat_1(self):
        self.pace(1)
        self.show_header(run_time=self.t(0.5))
        lab = VGroup(flask_icon(ACCENT, 0.72), txt("an experiment lab", 30, INK, bold=True)).arrange(RIGHT, buff=0.3)
        lab.move_to(UP * 2.3)
        self.play(FadeIn(lab, shift=UP * 0.15), run_time=self.t(0.6))

        base = node("Baseline", "current champion", MUTED, w=3.8, h=1.3).move_to([-3.1, 0.75, 0])
        cand = node("Candidate", "new context compression", GOLD, w=3.8, h=1.3).move_to([3.1, 0.75, 0])
        versus = txt("vs", 26, MUTED, bold=True).move_to([0, 0.75, 0])
        title, sub = cand.label
        title_home = title.get_center()
        title.set_y(cand.box.get_y())
        self.wait(self.t(0.9))
        self.play(FadeIn(VGroup(cand.box, cand[1], title), shift=UP * 0.15), run_time=self.t(0.6))
        self.wait(self.t(2.2))
        self.play(title.animate.move_to(title_home), FadeIn(sub, shift=UP * 0.1), lit(cand, GOLD), run_time=self.t(0.5))
        self.wait(self.t(0.95))
        self.play(FadeIn(base, shift=UP * 0.15), FadeIn(versus), cand.box.animate.set_stroke(GOLD, width=2.5), run_time=self.t(0.6))
        self.wait(self.t(0.45))

        tasks = task_row(8, 0.38, 0.16, INK).move_to([0, -1.3, 0])
        same = txt("same tasks", 18, MUTED).next_to(tasks, DOWN, buff=0.2)
        fork_y = -0.5
        bottom = base.get_bottom()[1] - 0.08
        stem = Line(tasks.get_top() + UP * 0.08, [0, fork_y, 0], color=LINE, stroke_width=3)
        bar = Line([-3.1, fork_y, 0], [3.1, fork_y, 0], color=LINE, stroke_width=3)
        up_a = short_arrow([-3.1, fork_y, 0], [-3.1, bottom, 0])
        up_b = short_arrow([3.1, fork_y, 0], [3.1, bottom, 0])
        self.play(LaggedStart(*[FadeIn(sq, shift=UP * 0.1) for sq in tasks], lag_ratio=0.1), FadeIn(same), run_time=self.t(0.6))
        self.play(Create(stem), Create(bar), Create(up_a), Create(up_b), run_time=self.t(0.4))
        start = tasks.get_top()[1] + 0.08
        left = path_through((0, start), (0, fork_y), (-3.1, fork_y), (-3.1, bottom))
        right = path_through((0, start), (0, fork_y), (3.1, fork_y), (3.1, bottom))
        self.wait(self.t(0.2))
        self.play(ride(left, INK), ride(right, INK), run_time=self.t(0.7))

    # -- 2: SQS -> EventBridge Pipe -> Step Functions ------------------------

    def beat_2(self):
        self.pace(2)
        self.clear(run_time=self.t(0.4))
        sqs = tile(queue_icon(ACCENT), "SQS", "message queue", ACCENT, w=3.4, h=1.2)
        pipe = node("EventBridge Pipe", "hands it on", ACCENT, w=3.45, h=1.2)
        flow = node("Step Functions", "standard workflow", ACCENT, w=3.3, h=1.2)
        VGroup(sqs, pipe, flow).arrange(RIGHT, buff=0.8).move_to(UP * 0.05)
        a1, a2 = link(sqs, pipe), link(pipe, flow)
        self.draw(sqs, a1, pipe, a2, flow, run_time=self.t(0.8))

        label = chip("experiment", ACCENT, 18)
        pointer = Polygon([-0.11, 0, 0], [0.11, 0, 0], [0, -0.13, 0], stroke_width=0, fill_color=ACCENT, fill_opacity=1)
        pointer.next_to(label, DOWN, buff=0)
        msg = VGroup(label, pointer)

        def above(card):
            return card.get_top() + UP * (0.2 + msg.height / 2)

        msg.move_to(above(sqs) + UP * 0.3)
        slot = sqs[1][1][-1]
        self.play(FadeIn(msg), msg.animate.move_to(above(sqs)), run_time=self.t(0.5))
        self.play(slot.animate.set_fill(INK, opacity=1), run_time=self.t(0.4))
        self.wait(self.t(0.75))
        self.play(travel(a1, ACCENT), msg.animate.move_to(above(pipe)), slot.animate.set_fill(ACCENT, opacity=0.35), run_time=self.t(0.8))
        self.wait(self.t(0.6))
        self.play(travel(a2, ACCENT), msg.animate.move_to(above(flow)), run_time=self.t(0.8))
        self.sqs, self.pipe, self.flow, self.arrows, self.msg = sqs, pipe, flow, VGroup(a1, a2), msg

    # -- 3: baseline arm, then candidate arm, each a Fargate task -------------

    def beat_3(self):
        self.pace(3)
        flow = self.flow
        target = UP * 2.2
        shift = target - flow.get_center()
        self.play(
            FadeOut(self.sqs), FadeOut(self.pipe), FadeOut(self.arrows), FadeOut(self.msg, shift=shift),
            flow.animate.move_to(target),
            run_time=self.t(0.6),
        )
        arm_a = state_card("Arm A", "baseline", MUTED)
        arm_b = state_card("Arm B", "candidate", GOLD)
        final = state_card("Finalize", "pairs the logs", ACCENT)
        VGroup(arm_a, arm_b, final).arrange(RIGHT, buff=0.85).move_to(UP * 0.15)
        for card in (arm_a, arm_b, final):
            card.tag.next_to(card[3], DOWN, buff=0.1, aligned_edge=LEFT)
        ab, bf = link(arm_a, arm_b), link(arm_b, final)
        ghost_bf, ghost_final = faded(bf), faded(final)
        rows = [task_row().next_to(card, DOWN, buff=0.35) for card in (arm_a, arm_b)]
        entry_y = flow.get_y()
        entry = path_through((flow.get_left()[0] - 0.06, entry_y), (arm_a.get_x(), entry_y), (arm_a.get_x(), arm_a.get_top()[1] + 0.08))
        corner = Line([flow.get_left()[0] - 0.06, entry_y, 0], [arm_a.get_x(), entry_y, 0], color=LINE, stroke_width=3)
        drop = short_arrow([arm_a.get_x(), entry_y, 0], [arm_a.get_x(), arm_a.get_top()[1] + 0.08, 0])
        self.play(
            LaggedStart(
                Create(corner), Create(drop), FadeIn(arm_a, shift=UP * 0.1), Create(ab[0]), FadeIn(arm_b, shift=UP * 0.1),
                FadeIn(ghost_bf), FadeIn(ghost_final), FadeIn(rows[0]), FadeIn(rows[1]),
                lag_ratio=0.12,
            ),
            run_time=self.t(0.8),
        )
        self.play(ride(entry, ACCENT), lit(arm_a, INK), run_time=self.t(0.6))
        self.play(LaggedStart(*[sq.animate.set_fill(MUTED, opacity=1) for sq in rows[0]], lag_ratio=0.3), run_time=self.t(0.7))
        self.play(travel(ab, ACCENT), arm_a.box.animate.set_stroke(MUTED, width=2.5), lit(arm_b, INK), run_time=self.t(0.6))
        self.play(LaggedStart(*[sq.animate.set_fill(GOLD, opacity=1) for sq in rows[1]], lag_ratio=0.3), run_time=self.t(0.7))
        self.wait(self.t(0.4))
        self.play(
            FadeIn(arm_a.tag), FadeIn(arm_b.tag), arm_b.box.animate.set_stroke(GOLD, width=2.5),
            *[card[1].animate(rate_func=there_and_back).scale(1.25) for card in (arm_a, arm_b)],
            run_time=self.t(0.6),
        )
        both = VGroup(rows[0], rows[1])
        brace = bracket_under(both)
        same = txt("same tasks", 18, MUTED).next_to(brace, DOWN, buff=0.14)
        self.wait(self.t(0.8))
        self.play(Create(brace), FadeIn(same, shift=UP * 0.1), run_time=self.t(0.6))
        self.final, self.ghost_final, self.bf, self.ghost_bf = final, ghost_final, bf, ghost_bf

    # -- 4: the finalizer pairs the logs and runs a paired t-test -------------

    def beat_4(self):
        self.pace(4)
        self.play(
            ReplacementTransform(self.ghost_bf, self.bf), ReplacementTransform(self.ghost_final, self.final),
            FadeIn(self.final.tag), run_time=self.t(0.4),
        )
        self.play(travel(self.bf, ACCENT), lit(self.final, INK), run_time=self.t(0.5))
        self.clear(run_time=self.t(0.35))

        base_y, unit = 0.35, 2.4
        xs = [-3.85 + 1.1 * i for i in range(len(BASELINE))]
        bars, pairs, dots = VGroup(), VGroup(), VGroup()
        marks = task_row(len(xs), 0.2)
        for mark, x in zip(marks, xs, strict=True):
            mark.move_to([x, base_y - 0.26, 0])
        for x, b, c in zip(xs, BASELINE, CANDIDATE, strict=True):
            left = Rectangle(width=0.3, height=b * unit, stroke_width=0, fill_color=MUTED, fill_opacity=1)
            right = Rectangle(width=0.3, height=c * unit, stroke_width=0, fill_color=GOLD, fill_opacity=1)
            left.move_to([x - 0.24, base_y + b * unit / 2, 0])
            right.move_to([x + 0.24, base_y + c * unit / 2, 0])
            bars.add(left, right)
            pair = Line(left.get_corner(UP + RIGHT), right.get_corner(UP + LEFT), color=INK, stroke_width=3)
            pairs.add(pair)
            dots.add(Dot(pair.get_center(), radius=0.07, color=INK))
        axis = Line([-4.45, base_y, 0], [4.45, base_y, 0], color=LINE, stroke_width=2)
        legend = VGroup(swatch(MUTED, "baseline log"), swatch(GOLD, "candidate log")).arrange(RIGHT, buff=0.6).move_to(UP * 2.5)
        self.play(
            FadeIn(legend), Create(axis),
            LaggedStart(*[GrowFromEdge(bar, DOWN) for bar in bars], lag_ratio=0.06),
            run_time=self.t(0.8),
        )
        self.play(
            LaggedStart(*[Create(p) for p in pairs], lag_ratio=0.12),
            LaggedStart(*[FadeIn(m, shift=UP * 0.08) for m in marks], lag_ratio=0.12),
            run_time=self.t(0.7),
        )

        line_y, zero_x, scale = -1.2, -1.0, 14.0
        number_line = Line([-4.1, line_y, 0], [4.4, line_y, 0], color=LINE, stroke_width=3)
        zero_tick = Line([zero_x, line_y - 0.16, 0], [zero_x, line_y + 0.16, 0], color=INK, stroke_width=3)
        worse = txt("candidate worse", 16, MUTED).next_to(number_line, DOWN, buff=0.26).align_to(number_line, LEFT)
        better = txt("candidate better", 16, MUTED).next_to(number_line, DOWN, buff=0.26).align_to(number_line, RIGHT)
        zero = txt("0", 18, INK).move_to([zero_x, better.get_y(), 0])
        test = chip("paired t-test", INK, 18, filled=False).next_to(number_line, LEFT, buff=0.3)
        self.play(Create(number_line), FadeIn(zero_tick), FadeIn(zero), FadeIn(worse), FadeIn(better), run_time=self.t(0.5))
        self.play(FadeIn(test, shift=RIGHT * 0.1), run_time=self.t(0.5))

        diffs = [c - b for b, c in zip(BASELINE, CANDIDATE, strict=True)]
        moves = [dot.animate.move_to([zero_x + d * scale, line_y, 0]) for dot, d in zip(dots, diffs, strict=True)]
        self.play(LaggedStart(*moves, lag_ratio=0.08), run_time=self.t(1.1))

        n = len(diffs)
        mean = sum(diffs) / n
        sd = math.sqrt(sum((d - mean) ** 2 for d in diffs) / (n - 1))
        half = T_CRIT_DF7 * sd / math.sqrt(n)
        ci_y = line_y + 0.32
        lo, hi = zero_x + (mean - half) * scale, zero_x + (mean + half) * scale
        ci = interval(lo, hi, ci_y, INK, thick=6, cap=0.12)
        center = Dot([zero_x + mean * scale, ci_y, 0], radius=0.09, color=INK)
        label = txt("95% CI of the paired difference", 18, INK).next_to(ci, RIGHT, buff=0.35)
        self.wait(self.t(0.7))
        self.play(GrowFromPoint(ci, center.get_center()), FadeIn(center, scale=0.5), run_time=self.t(0.7))
        self.play(FadeIn(label, shift=LEFT * 0.1), run_time=self.t(0.5))
        self.number_line, self.ci = number_line, ci
        self.ci_span = ((mean - half) * scale, (mean + half) * scale)

    # -- 5: the verdict rule ----------------------------------------------------

    def beat_5(self):
        self.pace(5)
        self.clear(keep=[self.number_line, self.ci], run_time=self.t(0.35))
        zero_x, left_x, right_x = -1.9, -5.3, 1.9
        rows_y = [1.55, 0.15, -1.25]
        axis = DashedLine([zero_x, 2.2, 0], [zero_x, -1.75, 0], color=MUTED, stroke_width=2, dash_length=0.1)
        zero = txt("0", 20, INK, bold=True).next_to(axis, UP, buff=0.12)
        below = txt("below zero", 16, MUTED).next_to(zero, LEFT, buff=1.0)
        above = txt("above zero", 16, MUTED).next_to(zero, RIGHT, buff=1.0)
        lines = VGroup(*[Line([left_x, y, 0], [right_x, y, 0], color=LINE, stroke_width=3) for y in rows_y])

        def verdict(name: str, color: str, y: float) -> VGroup:
            arrow = short_arrow([right_x + 0.15, y, 0], [right_x + 0.75, y, 0])
            badge = chip(name, color, 20).next_to(arrow, RIGHT, buff=0.15)
            return VGroup(arrow, badge)

        low, high = self.ci_span
        specs = [(zero_x + low, zero_x + high, GREEN, "PROMOTE"), (-4.7, -2.6, RED, "REJECT"), (-3.1, -0.4, MUTED, "INCONCLUSIVE")]
        cis = [interval(lo, hi, y, color) for (lo, hi, color, _), y in zip(specs, rows_y, strict=True)]
        verdicts = [verdict(name, color, y) for (_, _, color, name), y in zip(specs, rows_y, strict=True)]
        guard = VGroup(tick_mark(GREEN, 0.24), txt("no protected metric regressed", 16, INK)).arrange(RIGHT, buff=0.16)
        guard.move_to([0, rows_y[0] - 0.52, 0]).align_to(lines[0], RIGHT)

        def grow(ci):
            return GrowFromPoint(ci, ci.get_center())

        self.play(
            ReplacementTransform(self.number_line, lines[0]), ReplacementTransform(self.ci, cis[0]),
            Create(axis), FadeIn(zero), FadeIn(below), FadeIn(above), Create(lines[1]), Create(lines[2]),
            run_time=self.t(0.9),
        )
        self.wait(self.t(1.0))
        self.play(FadeIn(guard, shift=UP * 0.1), run_time=self.t(0.5))
        self.wait(self.t(1.6))
        self.play(FadeIn(verdicts[0], shift=LEFT * 0.15), run_time=self.t(0.5))
        self.wait(self.t(0.8))
        self.play(grow(cis[1]), run_time=self.t(0.6))
        self.wait(self.t(0.35))
        self.play(FadeIn(verdicts[1], shift=LEFT * 0.15), run_time=self.t(0.4))
        self.wait(self.t(0.45))
        self.play(grow(cis[2]), run_time=self.t(0.5))
        self.play(FadeIn(verdicts[2], shift=LEFT * 0.15), run_time=self.t(0.4))

    # -- 6: the first real finding ----------------------------------------------

    def beat_6(self):
        self.pace(6)
        self.clear(run_time=self.t(0.4))
        kicker = chip("first real finding", GREEN, 18).move_to(UP * 2.45)
        self.play(FadeIn(kicker, shift=DOWN * 0.1), run_time=self.t(0.5))

        winner = node("Exact identifiers first", "candidate summary prompt", GOLD, w=4.9, h=1.05)
        loser = node("General structured prompt", "baseline", MUTED, w=4.9, h=1.05)
        beat = txt("beat", 24, GREEN, bold=True)
        VGroup(winner, beat, loser).arrange(RIGHT, buff=0.35).move_to(UP * 1.2)
        self.wait(self.t(0.4))
        self.play(FadeIn(winner, shift=UP * 0.15), run_time=self.t(0.6))
        self.wait(self.t(2.3))
        self.play(FadeIn(beat), FadeIn(loser, shift=UP * 0.15), run_time=self.t(0.6))

        x0, per_point = -3.2, 0.2
        heading = txt("recall gain, in points", 16, MUTED).move_to([0, 0.15, 0]).align_to([x0, 0, 0], LEFT)
        axis = Line([x0, -0.1, 0], [x0, -1.75, 0], color=LINE, stroke_width=2)
        rows = [("Nova", 27, -0.5), ("Haiku", 14, -1.35)]
        names = VGroup(*[txt(name, 24, INK, bold=True).move_to([0, y, 0]).align_to([x0 - 0.25, 0, 0], RIGHT) for name, _, y in rows])
        self.wait(self.t(1.2))
        self.play(Create(axis), FadeIn(heading), FadeIn(names), run_time=self.t(0.4))

        def grow_bar(points: int, y: float):
            bar = Rectangle(width=0.01, height=0.5, stroke_width=0, fill_color=GREEN, fill_opacity=1)
            bar.move_to([x0 + 0.005, y, 0])
            value = txt("+0 points", 24, GREEN, bold=True).next_to(bar, RIGHT, buff=0.2)
            group = VGroup(bar, value)

            def update(m, alpha):
                width = max(0.01, alpha * points * per_point)
                m[0].become(Rectangle(width=width, height=0.5, stroke_width=0, fill_color=GREEN, fill_opacity=1).move_to([x0 + width / 2, y, 0]))
                shown = round(alpha * points)
                m[1].become(txt(f"+{shown} point{'' if shown == 1 else 's'}", 24, GREEN, bold=True).next_to(m[0], RIGHT, buff=0.2))

            self.add(group)
            return UpdateFromAlphaFunc(group, update)

        self.play(grow_bar(27, rows[0][2]), run_time=self.t(1.6))
        self.wait(self.t(0.5))
        self.play(grow_bar(14, rows[1][2]), run_time=self.t(1.1))

    # -- 7: ledger, S3, phone -----------------------------------------------------

    def beat_7(self):
        self.pace(7)
        self.clear(run_time=self.t(0.4))
        rows_y = [1.2, -0.1, -1.4]
        ledger = tile(db_icon(MUTED), "Ledger", "every state change", MUTED, w=3.75, h=0.95)
        store = tile(bucket_icon(MUTED), "S3", "logs and report", MUTED, w=3.75, h=0.95)
        phone = tile(phone_icon(INK), "Daniel's phone", "Telegram", GREEN, w=3.75, h=0.95)
        tiles_x = -2.775
        for card, y in zip((ledger, store, phone), rows_y, strict=True):
            card.move_to([tiles_x, y, 0])
        bus_x = ledger.get_left()[0] - 0.38
        in_x = ledger.get_left()[0] - 0.04
        source = node("Experiment", "Step Functions", ACCENT, w=2.5, h=0.85)
        source.move_to([bus_x - 0.45 + source.width / 2, 2.38, 0])
        top_y = source.get_bottom()[1]
        wires = VGroup(
            Line([bus_x, top_y, 0], [bus_x, rows_y[2], 0], color=LINE, stroke_width=3),
            *[short_arrow([bus_x, y, 0], [in_x, y, 0]) for y in rows_y],
        )
        self.play(
            LaggedStart(FadeIn(source, shift=UP * 0.1), Create(wires), FadeIn(ledger, shift=UP * 0.1), FadeIn(store, shift=UP * 0.1), FadeIn(phone, shift=UP * 0.1), lag_ratio=0.15),
            run_time=self.t(0.8),
        )
        content_x = ledger.get_right()[0] + 0.35

        def route(y: float):
            return path_through((bus_x, top_y), (bus_x, y), (in_x, y))

        events = VGroup(*[event_pill(name) for name in ("created", "arm A done", "arm B done", "verdict")])
        events.arrange(RIGHT, buff=0.15).move_to([0, rows_y[0], 0]).align_to([content_x, 0, 0], LEFT)
        self.play(ride(route(rows_y[0])), run_time=self.t(0.6))
        self.play(LaggedStart(*[FadeIn(e, shift=LEFT * 0.1) for e in events], lag_ratio=0.35), run_time=self.t(1.2))

        files = VGroup(
            *[VGroup(doc_icon(INK, 0.5), txt(name, 18, INK)).arrange(RIGHT, buff=0.15) for name in ("log A", "log B", "report")]
        ).arrange(RIGHT, buff=0.55).move_to([0, rows_y[1], 0]).align_to([content_x, 0, 0], LEFT)
        self.wait(self.t(0.2))
        self.play(ride(route(rows_y[1])), run_time=self.t(0.6))
        self.play(LaggedStart(*[FadeIn(f, shift=DOWN * 0.2) for f in files], lag_ratio=0.3), run_time=self.t(0.9))

        card = RoundedRectangle(corner_radius=0.14, width=3.9, height=phone.height, stroke_color=LINE, stroke_width=2, fill_color=PANEL, fill_opacity=1)
        card.move_to([0, rows_y[2], 0]).align_to([content_x, 0, 0], LEFT)
        badge = chip("PROMOTE", GREEN, 18).next_to(card.get_left(), RIGHT, buff=0.3)
        heights = [0.3, 0.5, 0.26, 0.54, 0.36, 0.56]
        chart_bars = VGroup(
            *[
                Rectangle(width=0.16, height=h, stroke_width=0, fill_color=MUTED if i % 2 == 0 else GOLD, fill_opacity=1)
                for i, h in enumerate(heights)
            ]
        ).arrange(RIGHT, buff=0.06, aligned_edge=DOWN)
        chart_bars.next_to(card.get_right(), LEFT, buff=0.35).align_to(card.get_bottom() + UP * 0.2, DOWN)
        self.wait(self.t(1.2))
        self.play(ride(route(rows_y[2]), GREEN), run_time=self.t(0.6))
        self.play(FadeIn(card, shift=LEFT * 0.1), FadeIn(badge, shift=LEFT * 0.1), run_time=self.t(0.6))
        self.wait(self.t(1.3))
        self.play(LaggedStart(*[GrowFromEdge(b, DOWN) for b in chart_bars], lag_ratio=0.12), run_time=self.t(0.8))
