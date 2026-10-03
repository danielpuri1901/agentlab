"""Chapter 4: one tap, from a Telegram button to a running explain task.

Beat 1 shows the Lambda's two checks, beat 2 the conditional update that
lets the first tap win, beat 3 the RunTask call and the trace from the
finished video back to the approved proposal.
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
    RED,
    ExplainerScene,
    chip,
    cpu_icon,
    db_icon,
    faded,
    lambda_icon,
    lit,
    load_spec,
    phone_icon,
    tile,
    travel,
    txt,
)
from manim import (
    DOWN,
    LEFT,
    ORIGIN,
    RIGHT,
    UP,
    Arrow,
    Circle,
    Create,
    DashedLine,
    Dot,
    FadeIn,
    FadeOut,
    Line,
    MoveAlongPath,
    Polygon,
    ReplacementTransform,
    RoundedRectangle,
    Succession,
    VGroup,
    VMobject,
    smooth,
)

CAPTION_SWAP = 0.25
"""The base class fades each caption in for this long before a beat method runs."""

LAST_BEAT_TAIL = 0.8
"""make.py holds the chapter's last beat this much longer than its narration."""


def hop(start, end, color: str = LINE, buff: float = 0.12) -> VGroup:
    """An arrow between two points, wrapped like link() so travel() can ride it."""
    arrow = Arrow(
        start, end, buff=buff, color=color, stroke_width=3, max_tip_length_to_length_ratio=0.18, max_stroke_width_to_length_ratio=8
    )
    return VGroup(arrow)


def bounce(arrow: VGroup, color: str = RED, back: float = 0.5, run_time: float = 0.45):
    """A dot that reaches the end of an arrow and is pushed back along it."""
    line = arrow[0]
    start = line.get_end()
    path = Line(start, start + (line.get_start() - start) * back)
    pair = VGroup(Dot(radius=0.165, color=color, fill_opacity=0.25), Dot(radius=0.075, color=color)).move_to(start)
    return Succession(
        FadeIn(pair, run_time=0.08),
        MoveAlongPath(pair, path, run_time=run_time, rate_func=smooth),
        FadeOut(pair, run_time=0.15),
    )


def mark(kind: str, at=ORIGIN) -> VGroup:
    """A round status mark: "open" (an empty ring), "pass" (a green tick) or "fail" (a red cross)."""
    if kind == "open":
        return VGroup(Circle(radius=0.2, stroke_color=MUTED, stroke_width=2.5)).move_to(at)
    color = GREEN if kind == "pass" else RED
    disc = Circle(radius=0.23, stroke_width=0, fill_color=color, fill_opacity=1)
    if kind == "pass":
        glyph = VMobject(stroke_color=BACKGROUND, stroke_width=5).set_points_as_corners(
            [[-0.1, 0.0, 0], [-0.03, -0.08, 0], [0.11, 0.09, 0]]
        )
    else:
        glyph = VGroup(Line([-0.08, -0.08, 0], [0.08, 0.08, 0]), Line([-0.08, 0.08, 0], [0.08, -0.08, 0])).set_stroke(BACKGROUND, 5)
    return VGroup(disc, glyph).move_to(at)


def card(w: float, h: float, color: str, lines) -> VGroup:
    """A plain rounded card with centred lines of text; `card.box` is the border."""
    box = RoundedRectangle(corner_radius=0.14, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    label = VGroup(*lines).arrange(DOWN, buff=0.1).move_to(box)
    group = VGroup(box, label)
    group.box = box
    return group


def ledger_item() -> tuple[VGroup, VGroup]:
    """The proposal's DynamoDB item, and its status chip as a separate mobject so it can be swapped."""
    box = RoundedRectangle(corner_radius=0.14, width=4.1, height=1.45, stroke_color=MUTED, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    icon = db_icon(MUTED, w=0.5, h=0.62).move_to(box.get_left() + RIGHT * 0.55)
    key = txt("proposal#prop-...", 18, INK, mono=True)
    word = txt("status", 18, MUTED)
    status = chip("PROPOSED", MUTED, 18)
    row = VGroup(word, status).arrange(RIGHT, buff=0.22)
    VGroup(key, row).arrange(DOWN, aligned_edge=LEFT, buff=0.16).next_to(icon, RIGHT, buff=0.35)
    caption = txt("DynamoDB ledger", 16, MUTED).next_to(box, UP, buff=0.12).align_to(box, RIGHT)
    group = VGroup(box, icon, key, word, caption)
    group.box = box
    return group, status


def explain_card() -> tuple[VGroup, list[VGroup]]:
    """The explain task's card, and its four environment rows (shown one by one)."""
    head = VGroup(
        cpu_icon(ACCENT, 0.5),
        VGroup(txt("explain task", 24, INK, bold=True), txt("Fargate", 16, MUTED)).arrange(DOWN, aligned_edge=LEFT, buff=0.06),
    ).arrange(RIGHT, buff=0.3)
    rows = []
    for key, value, literal in (
        ("TRACK", "core", True),
        ("EXPLAIN_URL", "cited URL", False),
        ("EXPLAIN_TITLE", "real title", False),
        ("PID", "proposal id", False),
    ):
        rows.append(VGroup(txt(key, 18, INK, mono=True), txt(value, 18, INK if literal else MUTED, mono=literal)))
    for i, (key, value) in enumerate(rows):
        key.move_to(ORIGIN, aligned_edge=LEFT).shift(DOWN * 0.38 * i)
        value.move_to([2.25, key.get_y(), 0], aligned_edge=LEFT)
    env = VGroup(*rows)
    body = VGroup(head, env).arrange(DOWN, aligned_edge=LEFT, buff=0.3)
    box = RoundedRectangle(
        corner_radius=0.14, width=body.width + 0.7, height=body.height + 0.55, stroke_color=ACCENT, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
    ).move_to(body)
    group = VGroup(box, head)
    group.box = box
    return group, rows


def video_card() -> VGroup:
    box = RoundedRectangle(corner_radius=0.14, width=3.7, height=1.15, stroke_color=GOLD, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    screen = RoundedRectangle(corner_radius=0.06, width=0.72, height=0.52, stroke_color=GOLD, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    play = Polygon([-0.09, 0.12, 0], [-0.09, -0.12, 0], [0.13, 0, 0], stroke_width=0, fill_color=GOLD, fill_opacity=1).move_to(screen)
    icon = VGroup(screen, play).move_to(box.get_left() + RIGHT * 0.66)
    texts = VGroup(txt("video", 24, INK, bold=True), txt("pid = prop-...", 16, MUTED, mono=True)).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
    texts.next_to(icon, RIGHT, buff=0.3)
    group = VGroup(box, icon, texts)
    group.box = box
    return group


class Chapter(ExplainerScene):
    def begin(self, beat: int):
        """Start a beat's clock so until() can line the picture up with the words."""
        self.beat = beat
        self.beat_start = self.time - CAPTION_SWAP
        self.words = [w.strip(".,:;") for w in load_spec()["captions"][beat - 1].split()]

    def until(self, phrase: str):
        """Wait until the narration reaches `phrase`, assuming an even speaking pace."""
        target = [w.strip(".,:;") for w in phrase.split()]
        hits = [i for i in range(len(self.words)) if self.words[i : i + len(target)] == target]
        if not hits:
            raise ValueError(f"{phrase!r} is not in beat {self.beat}'s narration")
        speech = self.d(self.beat) - (LAST_BEAT_TAIL if self.beat == len(self.durations) else 0.0)
        gap = self.beat_start + speech * hits[0] / len(self.words) - self.time
        if gap > 0.02:
            self.wait(gap)

    def beat_1(self):
        self.begin(1)
        self.show_header(run_time=0.4)
        top, low = 1.15, -0.95
        mid, tall = (top + low) / 2, 3.4
        door = card(1.25, tall, ACCENT, [txt("function", 18, INK, bold=True), txt("URL", 18, INK, bold=True)]).move_to([-3.65, mid, 0])
        gates = [
            card(2.3, tall, ACCENT, [txt("check 1", 16, MUTED), txt("secret header", 20, INK, bold=True)]).move_to([-1.0, mid, 0]),
            card(2.3, tall, ACCENT, [txt("check 2", 16, MUTED), txt("Daniel's user id", 20, INK, bold=True)]).move_to([2.15, mid, 0]),
        ]
        left, right, bottom, ceiling = -3.65, 5.95, -1.9, 2.8
        frame = RoundedRectangle(
            corner_radius=0.2, width=right - left, height=ceiling - bottom, stroke_color=ACCENT, stroke_width=2, fill_opacity=0
        ).move_to([(left + right) / 2, (bottom + ceiling) / 2, 0])
        title = VGroup(lambda_icon(GOLD, 0.5), txt("Lambda", 24, INK, bold=True)).arrange(RIGHT, buff=0.2)
        title.move_to([door.get_right()[0] + 0.3 + title.width / 2, ceiling - 0.5, 0])

        mine = phone_icon(INK).move_to([-5.45, top, 0])
        theirs = phone_icon(MUTED).move_to([-5.45, low, 0])
        names = [txt("Daniel", 18, INK).next_to(mine, DOWN, buff=0.14), txt("someone else", 18, MUTED).next_to(theirs, DOWN, buff=0.14)]

        def lane(y):
            stops = [mine.get_right()[0], door.get_left()[0], door.get_right()[0], gates[0].get_left()[0], gates[0].get_right()[0], gates[1].get_left()[0]]
            return [hop([stops[i], y, 0], [stops[i + 1], y, 0]) for i in (0, 2, 4)]

        a, b = lane(top), lane(low)
        taps = [txt("tap", 16, MUTED).next_to(x[0], UP, buff=0.06) for x in (a, b)]
        rings = [[mark("open", [g.get_x(), y, 0]) for y in (top, low)] for g in gates]
        ghosts = [faded(g) for g in gates]

        self.draw(mine, names[0], theirs, names[1], frame, title, door, *ghosts, run_time=1.1, lag=0.08)
        self.play(*[Create(x[k][0]) for x in (a, b) for k in range(3)], *[FadeIn(t) for t in taps], run_time=0.4)
        self.play(travel(a[0], GREEN), travel(b[0], MUTED))

        self.until("It checks a secret header")
        self.play(ReplacementTransform(ghosts[0], gates[0]), *[FadeIn(r) for r in rings[0]], run_time=0.4)
        self.play(travel(a[1], GREEN), travel(b[1], MUTED))
        self.play(*[FadeIn(mark("pass", r.get_center()), scale=0.6) for r in rings[0]], run_time=0.3)

        self.until("then checks that the tap")
        self.play(ReplacementTransform(ghosts[1], gates[1]), *[FadeIn(r) for r in rings[1]], run_time=0.4)
        self.play(travel(a[2], GREEN), travel(b[2], MUTED))
        self.play(
            FadeIn(mark("pass", rings[1][0].get_center()), scale=0.6),
            FadeIn(mark("fail", rings[1][1].get_center()), scale=0.6),
            run_time=0.3,
        )
        accepted = chip("accepted", GREEN, 18).move_to([4.85, top, 0])
        ignored = chip("ignored", RED, 18).move_to([4.85, low, 0])
        out = hop([gates[1].get_right()[0], top, 0], [accepted.get_left()[0], top, 0])
        self.play(Create(out[0]), FadeIn(accepted, shift=RIGHT * 0.15), FadeIn(ignored, shift=RIGHT * 0.15), run_time=0.5)

    def beat_2(self):
        self.begin(2)
        self.clear()
        y = 1.15
        lam = tile(lambda_icon(GOLD), "Lambda", "tap handler", ACCENT, w=2.9, h=1.1).move_to([-4.4, y, 0])
        cond = card(
            3.0, 1.45, ACCENT, [txt("conditional update", 20, INK, bold=True), txt("only if", 16, MUTED), txt("status = PROPOSED", 18, INK, mono=True)]
        ).move_to([-0.6, y, 0])
        item, status = ledger_item()
        delta = [3.8, y, 0] - item.box.get_center()
        item.shift(delta)
        status.shift(delta)
        a1 = hop(lam.get_right(), cond.get_left())
        a2 = hop(cond.get_right(), item.box.get_left())
        badge = cond.box.get_corner(UP + RIGHT)
        self.draw(lam, a1, cond, a2, item, status, run_time=1.0, lag=0.1)

        self.play(travel(a1, GREEN, run_time=0.55))
        ok = mark("pass", badge)
        self.play(FadeIn(ok, scale=0.6), run_time=0.25)
        self.play(travel(a2, GREEN, run_time=0.55))
        approved = chip("APPROVED", GREEN, 18).move_to(status, aligned_edge=LEFT)
        rows = [
            VGroup(mark("pass"), txt("tap 1", 20, INK, bold=True), txt("wins", 20, GREEN)).arrange(RIGHT, buff=0.25),
            VGroup(mark("fail"), txt("tap 2", 20, INK, bold=True), txt("already decided", 20, RED)).arrange(RIGHT, buff=0.25),
        ]
        VGroup(*rows).arrange(DOWN, aligned_edge=LEFT, buff=0.3).move_to([cond.get_x(), -0.6, 0])
        self.play(FadeOut(status, shift=UP * 0.12), FadeIn(approved, shift=UP * 0.12), FadeIn(rows[0], shift=UP * 0.1), run_time=0.4)

        self.until("and a double tap")
        self.play(travel(a1, GREEN, run_time=0.5))
        no = mark("fail", badge)
        self.play(FadeOut(ok, run_time=0.25), FadeIn(no, scale=0.6, run_time=0.25), bounce(a1), FadeIn(rows[1], shift=UP * 0.1, run_time=0.4))
        self.lam, self.item, self.status = lam, item, approved

    def beat_3(self):
        self.begin(3)
        lam, item, status = self.lam, self.item, self.status
        left_edge, right_x, top_y, low_y = -6.05, 3.55, 1.95, -1.3
        self.clear(keep=[lam, item, status], run_time=0.3)
        delta = [left_edge + item.box.width / 2, low_y, 0] - item.box.get_center()
        lam_to = [left_edge + lam.box.width / 2, top_y, 0]
        self.play(lam.animate.move_to(lam_to), item.animate.shift(delta), status.animate.shift(delta), run_time=0.6)
        verdict = hop(lam.get_bottom(), [lam.get_x(), item.box.get_top()[1], 0])
        verdict_label = txt("verdict", 16, MUTED).next_to(verdict, RIGHT, buff=0.12)
        explain, rows = explain_card()
        delta = [right_x, 1.5, 0] - explain.box.get_center()
        for part in (explain, *rows):
            part.shift(delta)
        ghosts = [faded(row) for row in rows]
        run = hop([lam.get_right()[0], top_y, 0], [explain.box.get_left()[0], top_y, 0])
        run_label = txt("ECS RunTask", 18, MUTED).next_to(run, UP, buff=0.1)
        self.draw(verdict[0], verdict_label, run[0], run_label, explain, *ghosts, run_time=0.9, lag=0.1)
        self.play(travel(run, ACCENT))

        for phrase, ghost, row in zip(("with the cited URL", "cited URL", "real title", "proposal id"), ghosts, rows, strict=True):
            self.until(phrase)
            self.play(ReplacementTransform(ghost, row), run_time=0.3)

        self.until("so the video")
        video = video_card().move_to([right_x, low_y, 0])
        down = hop(explain.box.get_bottom(), video.box.get_top())
        self.play(Create(down[0]), FadeIn(video, shift=DOWN * 0.1), run_time=0.6)
        self.play(travel(down, GOLD, run_time=0.6))

        self.until("traced back")
        start, end = video.box.get_left() + LEFT * 0.12, item.box.get_right() + RIGHT * 0.3
        trace = DashedLine(start, end, dash_length=0.14, color=INK, stroke_width=2.5)
        tip = Polygon(end + LEFT * 0.2, end + UP * 0.1, end + DOWN * 0.1, stroke_width=0, fill_color=INK, fill_opacity=1)
        label = txt("traced back to its tap", 20, INK).next_to(trace, UP, buff=0.16)
        self.play(Create(trace), run_time=0.7)
        self.play(FadeIn(tip, run_time=0.15), travel(trace, GREEN, run_time=0.7), FadeIn(label, shift=UP * 0.1, run_time=0.4))
        self.play(lit(item, GREEN), run_time=0.4)
