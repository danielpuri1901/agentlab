"""Chapter 3: the proposer.

Every morning a scheduled Fargate task gathers fresh sources into one
deduplicated pool, files slot one from the classics list by code, lets
Claude Sonnet pick the other slots with the taste profile, and sends at
most three lessons to Telegram.

The example picks are real. Slot one is the first entry of
docs/classics.json with the proposer's fixed CLASSIC_WHY line. The two
fresh picks are arXiv papers from the week this video was made, and each
why line claims only what that paper's abstract states.
"""

import math

from kit import (
    ACCENT,
    CAPTION_SWAP_SECONDS,
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
    cpu_icon,
    db_icon,
    doc_icon,
    faded,
    link,
    lit,
    node,
    phone_icon,
    tile,
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
    AnimationGroup,
    Arrow,
    Circle,
    Create,
    Dot,
    FadeIn,
    FadeOut,
    LaggedStart,
    Line,
    MoveAlongPath,
    Polygon,
    ReplacementTransform,
    RoundedRectangle,
    Succession,
    VGroup,
    VMobject,
    smooth,
    there_and_back,
)

END_MARGIN = 0.75
"""Every beat's animations end this long before its narration does (0.5 s plus frame rounding)."""

# (lens, title as one line, title wrapped for the slot cards, why, cited URL)
PICKS = [
    (
        "FOUNDATIONAL",
        "Attention Is All You Need",
        "Attention Is All You Need",
        "Why: Foundational paper from 2017. Everyone in the field builds on it.",
        "arxiv.org/abs/1706.03762",
    ),
    (
        "FRONTIER",
        "Agents Are Systems, Not Models: Rethinking Agentic Evaluation",
        "Agents Are Systems, Not Models:\nRethinking Agentic Evaluation",
        "Why: it evaluates agents as configurable systems, not fixed models.",
        "arxiv.org/abs/2610.01618",
    ),
    (
        "IMPLEMENT",
        "MemFit: Efficient Long-Term Agentic Memory",
        "MemFit: Efficient Long-Term\nAgentic Memory",
        "Why: agent memory that stores each turn verbatim, with LLM-free writes.",
        "arxiv.org/abs/2610.00872",
    ),
]

# The first four entries of docs/classics.json, in file order.
CLASSICS_SHOWN = [
    "Attention Is All You Need",
    "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models",
    "Training language models to follow instructions with human feedback",
    "Training Compute-Optimal Large Language Models",
]
CODE_PANEL = "#0a0f18"
LIST_X, LIST_W, LIST_TOP, LIST_H = -3.9, 4.6, 2.8, 2.85

POOL_X, POOL_Y, POOL_W, POOL_H = 4.1, 0.25, 4.2, 4.4
GRID = [(x, y) for y in (1.2, 0.25, -0.7) for x in (3.2, 4.1, 5.0)]
SOURCE_X, SOURCE_W = -3.75, 4.95
SOURCE_Y = [1.5, 0.25, -1.0]
LIFT = 0.625
GITHUB_Y = -1.625

CARD_X, CARD_W, CARD_H = 3.6, 5.2, 1.4
CARD_Y = [2.05, 0.5, -1.05]
ROW_W, ROW_H = 12.4, 1.25
ROW_Y = [2.175, 0.775, -0.625]
BANNER_Y = -1.78

BUBBLE_LEFT, BUBBLE_RIGHT, BUBBLE_TOP = -4.1, 6.2, 2.8
ITEM_Y = [1.95, 1.47, 0.99]
KEY_Y = [0.25, -0.2, -0.65]


def pt(x: float, y: float):
    """A point on the stage."""
    return x * RIGHT + y * UP


def arrow_between(start, end, color: str = LINE) -> VGroup:
    """A kit-style arrow between two points, wrapped like link() so travel() can ride it."""
    return VGroup(
        Arrow(
            start, end, buff=0.12, color=color, stroke_width=3,
            max_tip_length_to_length_ratio=0.18, max_stroke_width_to_length_ratio=8,
        )
    )


def fly(mobject, start, end, run_time: float = 0.55):
    """Animation: the mobject appears at start and glides to end, where it stays."""
    mobject.move_to(start)
    return Succession(
        FadeIn(mobject, run_time=0.12),
        MoveAlongPath(mobject, Line(start, end), run_time=run_time, rate_func=smooth),
    )


def clock_at(hour: int, minute: int, r: float = 0.62, color: str = INK) -> VGroup:
    """A clock face with four ticks and its hands set to hour:minute."""
    face = Circle(radius=r, stroke_color=color, stroke_width=3, fill_color=PANEL, fill_opacity=1)
    ticks = VGroup(
        *[Line(UP * r * 0.74, UP * r * 0.9, color=color, stroke_width=3).rotate(k * PI / 2, about_point=ORIGIN) for k in range(4)]
    )

    def hand(angle: float, length: float, width: float) -> Line:
        return Line(ORIGIN, length * (math.cos(angle) * RIGHT + math.sin(angle) * UP), color=color, stroke_width=width)

    minute_angle = PI / 2 - 2 * PI * minute / 60
    hour_angle = PI / 2 - 2 * PI * (hour % 12 + minute / 60) / 12
    hands = VGroup(hand(hour_angle, r * 0.5, 4.5), hand(minute_angle, r * 0.72, 3))
    clock = VGroup(face, ticks, hands, Dot(ORIGIN, radius=0.055, color=color))
    clock.face = face
    return clock


def papers_icon(color: str = INK) -> VGroup:
    """Two stacked sheets: a daily list of papers."""
    back = doc_icon(color).shift(UP * 0.14 + RIGHT * 0.14)
    return VGroup(back, doc_icon(color))


def chain_icon(color: str = INK, size: float = 1.0) -> VGroup:
    """Two interlocked links: a page of links."""
    first = RoundedRectangle(corner_radius=size * 0.16, width=size * 0.62, height=size * 0.32, stroke_color=color, stroke_width=3)
    second = first.copy()
    pair = VGroup(first, second).arrange(RIGHT, buff=-size * 0.2)
    return pair.rotate(PI / 4)


def tag_icon(color: str = INK, size: float = 1.0) -> VGroup:
    """A release tag."""
    w, h = size, size * 0.62
    shape = Polygon(
        [-w / 2, h / 2, 0], [w * 0.18, h / 2, 0], [w / 2, 0, 0], [w * 0.18, -h / 2, 0], [-w / 2, -h / 2, 0],
        stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1,
    )
    hole = Circle(radius=size * 0.07, stroke_color=color, stroke_width=2).move_to([w * 0.12, 0, 0])
    return VGroup(shape, hole)


def check_icon(color: str = GREEN, size: float = 0.34) -> VMobject:
    """A drawn check mark."""
    mark = VMobject(stroke_color=color, stroke_width=5)
    mark.set_points_as_corners([[-0.5, 0.0, 0], [-0.15, -0.35, 0], [0.5, 0.35, 0]])
    return mark.scale(size)


def clipped(text: str, size: int, max_w: float, color: str = INK):
    """txt() cut at a word boundary with an ellipsis, so it fits max_w."""
    words = text.split()
    line = txt(text, size, color)
    while line.width > max_w and len(words) > 1:
        words.pop()
        line = txt(" ".join(words) + "…", size, color)
    return line


def badge(number: int, color: str = MUTED) -> VGroup:
    """A slot number in a ring."""
    ring = Circle(radius=0.24, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    return VGroup(ring, txt(str(number), 20, color, bold=True).move_to(ring))


def input_card(icon, title: str, sub: str, color: str, w: float = 3.4, h: float = 0.95) -> VGroup:
    """A compact card: bold title and muted subtitle on the left, an icon on the right."""
    box = RoundedRectangle(corner_radius=0.12, width=w, height=h, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
    text = VGroup(txt(title, 20, INK, bold=True), txt(sub, 16, MUTED)).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
    text.move_to(box).align_to(box, LEFT).shift(RIGHT * 0.22)
    icon.scale_to_fit_height(h * 0.42).move_to(box).align_to(box, RIGHT).shift(LEFT * 0.2)
    card = VGroup(box, text, icon)
    card.box = box
    return card


def button(label: str, color: str, w: float) -> VGroup:
    """A Telegram inline button."""
    box = RoundedRectangle(corner_radius=0.1, width=w, height=0.38, stroke_color=color, stroke_width=2, fill_color=PANEL, fill_opacity=1)
    return VGroup(box, txt(label, 16, color, bold=True).move_to(box))


class Chapter(ExplainerScene):
    # -- narration sync -------------------------------------------------------

    def begin(self, beat: int, tail: float = 0.0) -> None:
        """Mark the beat start so until() can follow its narration."""
        self.beat_start = self.time - CAPTION_SWAP_SECONDS
        self.beat_end = self.beat_start + self.d(beat) - END_MARGIN
        self.spoken = self.d(beat) - tail

    def until(self, fraction: float, rest: float) -> None:
        """Hold until about `fraction` of the beat's words have been spoken.

        `rest` is the animation time still to come in this beat. The hold is
        cut short when needed, so a faster voice in the final render can
        never push those animations past the narration.
        """
        target = min(self.beat_start + fraction * self.spoken, self.beat_end - rest)
        if target - self.time > 0.02:
            self.wait(target - self.time)

    # -- beats ----------------------------------------------------------------

    def beat_1(self):
        self.begin(1)
        self.show_header(run_time=0.4)
        clock = clock_at(9, 30)
        sched = node("EventBridge Scheduler", "every morning", ACCENT, w=4.3, h=1.15)
        task = tile(cpu_icon(ACCENT), "Proposer", "small Fargate task", ACCENT, w=3.7, h=1.3)
        VGroup(clock, sched, task).arrange(RIGHT, buff=1.2).move_to(UP * 0.55)
        when = VGroup(txt("9:30", 30, INK, bold=True), txt("Amsterdam time", 18, MUTED)).arrange(DOWN, buff=0.1)
        when.next_to(clock, DOWN, buff=0.3)
        to_sched = link(clock.face, sched)
        to_task = link(sched, task, label="starts")
        self.play(FadeIn(clock, scale=0.9), FadeIn(when, shift=UP * 0.1), run_time=0.5)
        self.until(0.36, rest=2.75)
        self.draw(to_sched[0], sched, run_time=0.6)
        self.play(travel(to_sched, ACCENT, run_time=0.4))
        self.draw(to_task[0], to_task[1], task, run_time=0.6)
        self.play(travel(to_task, ACCENT, run_time=0.4))
        self.play(lit(task, ACCENT), run_time=0.25)

    def _send(self, path, cells, run_time: float = 0.55) -> list:
        """Small docs fly from the start of an arrow into pool cells."""
        start = path[0].get_start()
        docs = [doc_icon(INK, 0.5) for _ in cells]
        self.play(LaggedStart(*[fly(d, start, pt(*GRID[c]), run_time) for d, c in zip(docs, cells)], lag_ratio=0.18))
        return docs

    def beat_2(self):
        self.begin(2)
        self.clear()
        pool = RoundedRectangle(
            corner_radius=0.16, width=POOL_W, height=POOL_H, stroke_color=MUTED, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        ).move_to(pt(POOL_X, POOL_Y))
        title = txt("Today's pool", 24, INK, bold=True).move_to(pool.get_top() + DOWN * 0.42)
        note = txt("deduplicated by arXiv id or title", 16, MUTED).move_to(pool.get_bottom() + UP * 0.36)
        self.play(FadeIn(pool), FadeIn(title), run_time=0.45)
        sources = [
            (doc_icon(INK), "arXiv", "newest 40 in 4 categories"),
            (papers_icon(INK), "Hugging Face", "daily papers"),
            (chain_icon(INK), "Hacker News", "front page links about agents"),
        ]
        tiles, arrows = [], []
        fills = [[0, 1, 2], [3, 4, 1], [5, 6]]
        cues = [(0.19, 4.31), (0.52, 2.95), (0.74, 1.24)]
        for k, ((icon, name, sub), y) in enumerate(zip(sources, SOURCE_Y)):
            card = tile(icon, name, sub, MUTED, w=SOURCE_W, h=1.0).move_to(pt(SOURCE_X, y))
            path = arrow_between(pt(card.get_right()[0], y), pt(pool.get_left()[0], y))
            tiles.append(card)
            arrows.append(path)
            self.until(*cues[k])
            self.play(FadeIn(card, shift=RIGHT * 0.15), Create(path[0]), run_time=0.45)
            sent = self._send(path, fills[k])
            if k == 1:
                # Hugging Face lists an arXiv paper already in the pool: it merges into that one.
                self.play(FadeOut(sent[-1], scale=0.6), FadeIn(note, shift=UP * 0.1), run_time=0.35)
        self.pool, self.tiles, self.arrows = pool, tiles, arrows

    def beat_3(self):
        self.begin(3)
        self.play(*[m.animate.shift(UP * LIFT) for m in [*self.tiles, *self.arrows]], run_time=0.5)
        github = tile(tag_icon(INK), "GitHub release notes", "used to be in this pool", MUTED, w=SOURCE_W, h=1.0)
        github.move_to(pt(SOURCE_X, GITHUB_Y))
        path = arrow_between(pt(github.get_right()[0], GITHUB_Y), pt(self.pool.get_left()[0], GITHUB_Y))
        self.play(FadeIn(github, shift=RIGHT * 0.15), Create(path[0]), run_time=0.5)
        sent = self._send(path, [7, 8])
        self.until(0.41, rest=2.35)
        squares = VGroup(
            *[
                RoundedRectangle(corner_radius=0.03, width=0.15, height=0.15, stroke_width=0, fill_color=RED if i < 10 else MUTED, fill_opacity=1)
                for i in range(12)
            ]
        ).arrange(RIGHT, buff=0.045)
        ledger = VGroup(db_icon(MUTED, w=0.36, h=0.42), squares).arrange(RIGHT, buff=0.16)
        stamp = chip("10 of 12 rejected", RED, 16)
        middle = (github.get_right()[0] + self.pool.get_left()[0]) / 2
        VGroup(stamp, ledger).arrange(DOWN, buff=0.14).move_to(pt(middle, GITHUB_Y + 0.12))
        self.play(FadeOut(path), FadeIn(ledger[0]), run_time=0.3)
        self.play(LaggedStart(*[FadeIn(s, scale=0.5) for s in squares], lag_ratio=0.12), run_time=0.9)
        self.play(FadeIn(stamp, scale=1.3), run_time=0.35)
        self.until(0.8, rest=0.8)
        name_y = github[2][0].get_center()[1]
        strike = Line(pt(github.get_left()[0] + 0.25, name_y), pt(github.get_right()[0] - 0.25, name_y), color=RED, stroke_width=5)
        self.play(Create(strike), lit(github, RED), *[d.animate.set_stroke(RED) for d in sent], run_time=0.35)
        self.play(
            ReplacementTransform(github, faded(github, 0.55)),
            ReplacementTransform(strike, faded(strike, 0.3)),
            *[FadeOut(d, scale=0.6) for d in sent],
            run_time=0.45,
        )

    # -- slots ----------------------------------------------------------------

    def _slot_content(self, i: int, tag: str, tag_color: str):
        """Lens chip, wrapped title and a who-filled-it tag for slot card i."""
        lens, _title, wrapped, _why, _url = PICKS[i]
        lens_chip = chip(lens, PURPLE, 16)
        title = txt(wrapped, 18, INK, bold=True)
        block = VGroup(lens_chip, title).arrange(DOWN, aligned_edge=LEFT, buff=0.12)
        block.move_to(pt(CARD_X - CARD_W / 2 + 0.9 + block.width / 2, CARD_Y[i]))
        note = txt(tag, 16, tag_color)
        note.move_to(pt(CARD_X + CARD_W / 2 - 0.25 - note.width / 2, lens_chip.get_center()[1]))
        return lens_chip, title, note

    def _classics_list(self):
        """The curated list as a panel: header, the first entries, entry 1 lit as next."""
        panel = RoundedRectangle(
            corner_radius=0.14, width=LIST_W, height=LIST_H, stroke_color=LINE, stroke_width=2, fill_color=CODE_PANEL, fill_opacity=1
        ).move_to(pt(LIST_X, LIST_TOP - LIST_H / 2))
        left, right = panel.get_left()[0] + 0.3, panel.get_right()[0] - 0.3
        name = txt("27 classics", 20, INK, bold=True)
        name.move_to(pt(left + name.width / 2, LIST_TOP - 0.34))
        kind = txt("curated list", 16, MUTED)
        kind.move_to(pt(right - kind.width / 2, name.get_center()[1]))
        rows = VGroup()
        for k, title in enumerate(CLASSICS_SHOWN):
            y = CARD_Y[0] - 0.45 * k
            number = txt(str(k + 1), 16, MUTED, mono=True).move_to(pt(left + 0.08, y))
            text = clipped(title, 16, right - left - 0.45, INK if k == 0 else MUTED)
            text.move_to(pt(left + 0.45 + text.width / 2, y))
            rows.add(VGroup(number, text))
        more = txt("…", 16, MUTED)
        more.move_to(pt(left + 0.45 + more.width / 2, CARD_Y[0] - 0.45 * len(CLASSICS_SHOWN)))
        glow = RoundedRectangle(corner_radius=0.08, width=right - left + 0.3, height=0.38, stroke_width=0, fill_color=LINE, fill_opacity=0.55)
        glow.move_to(pt((left + right) / 2, CARD_Y[0]))
        return panel, VGroup(name, kind), glow, rows, more

    def beat_4(self):
        self.begin(4)
        self.clear()
        boxes, marks = [], []
        for i in range(3):
            boxes.append(
                RoundedRectangle(
                    corner_radius=0.14, width=CARD_W, height=CARD_H, stroke_color=LINE, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
                ).move_to(pt(CARD_X, CARD_Y[i]))
            )
            marks.append(badge(i + 1).move_to(pt(CARD_X - CARD_W / 2 + 0.42, CARD_Y[i])))
        self.play(
            LaggedStart(*[AnimationGroup(FadeIn(b, shift=UP * 0.1), FadeIn(m, shift=UP * 0.1)) for b, m in zip(boxes, marks)], lag_ratio=0.2),
            run_time=0.7,
        )
        self.play(boxes[0].animate.set_stroke(color=INK, width=3.5), run_time=0.3)

        panel, head, glow, rows, more = self._classics_list()
        path = arrow_between(pt(panel.get_right()[0], CARD_Y[0]), pt(boxes[0].get_left()[0], CARD_Y[0]))
        call = txt("next_classic()", 18, INK, mono=True).next_to(path, UP, buff=0.16)
        note = txt("code, not a model", 16, MUTED).next_to(path, DOWN, buff=0.16)
        self.play(Create(path[0]), FadeIn(call, shift=UP * 0.1), FadeIn(note, shift=UP * 0.1), run_time=0.5)

        self.until(0.38, rest=2.3)
        self.play(
            FadeIn(panel), FadeIn(glow), LaggedStart(*[FadeIn(r, shift=RIGHT * 0.1) for r in [*rows, more]], lag_ratio=0.15), run_time=0.6
        )
        lens_chip, title, tag = self._slot_content(0, "filled by code", MUTED)
        picked = rows[0][1].copy()
        self.play(ReplacementTransform(picked, title), rows[0][1].animate.set_color(MUTED), run_time=0.9)
        self.play(FadeIn(lens_chip, scale=0.8), FadeIn(tag), boxes[0].animate.set_stroke(color=MUTED, width=2.5), run_time=0.4)
        self.until(0.78, rest=0.4)
        self.play(FadeIn(head, shift=DOWN * 0.1), run_time=0.4)
        self.slots = [
            {"box": boxes[0], "badge": marks[0], "chip": lens_chip, "title": title, "note": tag},
            {"box": boxes[1], "badge": marks[1]},
            {"box": boxes[2], "badge": marks[2]},
        ]

    def _slot_mobjects(self) -> list:
        return [m for slot in self.slots for m in slot.values()]

    def beat_5(self):
        self.begin(5)
        self.clear(keep=self._slot_mobjects())
        claude = node("Claude\nSonnet", None, GOLD, w=2.1, h=1.4)
        claude.move_to(pt(-1.0, (CARD_Y[1] + CARD_Y[2]) / 2))
        inputs = [
            input_card(doc_icon(INK), "Sources", "today's pool", MUTED),
            input_card(doc_icon(PURPLE), "Taste profile", "learned from his taps", PURPLE),
            input_card(db_icon(MUTED), "Last 20 proposals", "with their outcomes", MUTED),
        ]
        VGroup(*inputs).arrange(DOWN, buff=0.32).move_to(pt(-4.55, claude.get_center()[1]))
        feeds = [arrow_between(card.get_right(), claude.get_left()) for card in inputs]
        outs = [arrow_between(claude.get_right(), self.slots[i]["box"].get_left()) for i in (1, 2)]
        self.play(FadeIn(claude, shift=UP * 0.15), *[self.slots[i]["box"].animate.set_stroke(color=GOLD, width=2.5) for i in (1, 2)], run_time=0.5)

        cues = [(0.26, 4.85), (0.36, 3.75), (0.5, 2.65)]
        colors = [MUTED, PURPLE, MUTED]
        for card, feed, cue, color in zip(inputs, feeds, cues, colors):
            self.until(*cue)
            self.play(FadeIn(card, shift=RIGHT * 0.15), Create(feed[0]), run_time=0.45)
            self.play(travel(feed, color, run_time=0.4))

        self.until(0.76, rest=1.55)
        self.play(*[Create(o[0]) for o in outs], run_time=0.35)
        self.play(*[travel(o, GOLD, run_time=0.45) for o in outs])
        fills = []
        for i in (1, 2):
            lens_chip, title, note = self._slot_content(i, "picked by Claude", GOLD)
            self.slots[i].update({"chip": lens_chip, "title": title, "note": note})
            fills += [FadeIn(lens_chip, scale=0.8), FadeIn(title, shift=UP * 0.1), FadeIn(note)]
        self.play(*fills, run_time=0.5)

    def beat_6(self):
        self.begin(6)
        self.clear(keep=self._slot_mobjects())
        rows = []
        moves = []
        for i, slot in enumerate(self.slots):
            _lens, title_text, _wrapped, why_text, url_text = PICKS[i]
            y = ROW_Y[i]
            color = MUTED if i == 0 else GOLD
            box = RoundedRectangle(corner_radius=0.14, width=ROW_W, height=ROW_H, stroke_color=color, stroke_width=2.5, fill_color=PANEL, fill_opacity=1)
            box.move_to(pt(0, y))
            mark = badge(i + 1).move_to(pt(-ROW_W / 2 + 0.45, y))
            title = txt(title_text, 20, INK, bold=True)
            chip_left = -ROW_W / 2 + 0.95
            line1 = y + 0.26
            chip_target = pt(chip_left + slot["chip"].width / 2, line1)
            title.move_to(pt(chip_left + slot["chip"].width + 0.22 + title.width / 2, line1))
            why = txt(why_text, 16, MUTED)
            why.move_to(pt(chip_left + why.width / 2, y - 0.28))
            url = chip(url_text, MUTED, 16, filled=False)
            url.move_to(pt(ROW_W / 2 - 0.25 - url.width / 2, y - 0.28))
            rows.append({"box": box, "badge": mark, "chip": slot["chip"], "title": title, "why": why, "url": url})
            moves += [
                ReplacementTransform(slot["box"], box),
                ReplacementTransform(slot["badge"], mark),
                slot["chip"].animate.move_to(chip_target),
                ReplacementTransform(slot["title"], title),
                FadeOut(slot["note"]),
            ]
        self.play(*moves, run_time=0.9)

        self.until(0.22, rest=3.3)
        self.play(LaggedStart(*[FadeIn(r["why"], shift=RIGHT * 0.15) for r in rows], lag_ratio=0.2), run_time=0.7)
        for cue, row in zip(((0.5, 2.6), (0.56, 2.2), (0.62, 1.8)), rows):
            self.until(*cue)
            self.play(row["chip"].animate(rate_func=there_and_back).scale(1.15), run_time=0.4)

        self.until(0.68, rest=1.4)
        self.play(LaggedStart(*[FadeIn(r["url"], shift=LEFT * 0.15) for r in rows], lag_ratio=0.2), run_time=0.6)
        rule = VGroup(check_icon(GREEN), txt("A pick may only claim what its source states", 20, INK, bold=True)).arrange(RIGHT, buff=0.25)
        frame = RoundedRectangle(
            corner_radius=0.18, width=rule.width + 0.8, height=0.58, stroke_color=GREEN, stroke_width=2.5, fill_color=PANEL, fill_opacity=1
        )
        banner = VGroup(frame, rule.move_to(frame)).move_to(pt(0, BANNER_Y))
        self.play(FadeIn(banner, shift=UP * 0.15), run_time=0.45)
        self.play(*[r["url"][0].animate.set_stroke(color=GREEN) for r in rows], *[r["url"][1].animate.set_color(GREEN) for r in rows], run_time=0.35)
        self.rows, self.banner = rows, banner

    def beat_7(self):
        # The last beat carries the chapter tail (make.py CHAPTER_TAIL_SECONDS) after its words.
        self.begin(7, tail=0.8)
        drop = [m for r in self.rows for m in (r["box"], r["badge"], r["why"], r["url"])]
        self.play(*[FadeOut(m) for m in (*drop, self.banner)], run_time=0.35)

        phone = phone_icon(INK, h=1.4).move_to(pt(-5.4, 2.0))
        label = txt("Telegram", 18, MUTED).next_to(phone, DOWN, buff=0.15)
        bubble = RoundedRectangle(
            corner_radius=0.18, width=BUBBLE_RIGHT - BUBBLE_LEFT, height=BUBBLE_TOP - 0.58, stroke_color=LINE, stroke_width=2,
            fill_color=PANEL, fill_opacity=1,
        )
        bubble.move_to(pt((BUBBLE_LEFT + BUBBLE_RIGHT) / 2, (BUBBLE_TOP + 0.58) / 2)).set_z_index(-1)
        head = txt("Today's lessons. Tap to decide.", 22, INK, bold=True)
        head.move_to(pt(BUBBLE_LEFT + 0.3 + head.width / 2, BUBBLE_TOP - 0.32))
        numbers, moves = [], []
        for i, row in enumerate(self.rows):
            y = ITEM_Y[i]
            numbers.append(txt(f"{i + 1}.", 18, INK, bold=True).move_to(pt(BUBBLE_LEFT + 0.42, y)))
            chip_x = BUBBLE_LEFT + 0.7 + row["chip"].width / 2
            title_x = chip_x + row["chip"].width / 2 + 0.2 + row["title"].width * 0.8 / 2
            moves += [row["chip"].animate.move_to(pt(chip_x, y)), row["title"].animate.scale(0.8).move_to(pt(title_x, y))]
        self.play(
            FadeIn(phone, shift=RIGHT * 0.15), FadeIn(label), FadeIn(bubble), FadeIn(head), *moves, *[FadeIn(n) for n in numbers], run_time=0.8
        )
        cap = chip("max 3 a day", ACCENT, 16).move_to(pt(-5.4, 0.25))
        self.play(FadeIn(cap, scale=0.8), run_time=0.35)

        self.until(0.32, rest=2.45)
        half = (BUBBLE_RIGHT - BUBBLE_LEFT - 0.1) / 2
        keys = VGroup()
        for i, y in enumerate(KEY_Y):
            keys.add(
                button(f"APPROVE {i + 1}", GREEN, half).move_to(pt(BUBBLE_LEFT + half / 2, y)),
                button(f"REJECT {i + 1}", RED, half).move_to(pt(BUBBLE_RIGHT - half / 2, y)),
            )
        self.play(LaggedStart(*[FadeIn(k, shift=UP * 0.1) for k in keys], lag_ratio=0.12), run_time=0.8)

        self.until(0.53, rest=1.65)
        message = [bubble, head, *numbers, *[m for r in self.rows for m in (r["chip"], r["title"])], keys]
        words = VGroup(
            txt("The proposer ran. The daily cap of 3 proposals", 18, INK),
            txt("is already used. Nothing new today.", 18, INK),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.1)
        capped = RoundedRectangle(
            corner_radius=0.18, width=words.width + 0.6, height=words.height + 0.5, stroke_color=LINE, stroke_width=2,
            fill_color=PANEL, fill_opacity=1,
        )
        capped.move_to(pt(BUBBLE_LEFT + capped.width / 2, 1.0)).set_z_index(-1)
        words.move_to(capped)
        pings = chip("capped day still pings", GREEN, 16).next_to(capped, RIGHT, buff=0.35)
        self.play(
            AnimationGroup(
                AnimationGroup(*[FadeOut(m, shift=UP * 0.6) for m in message]),
                AnimationGroup(FadeIn(capped, shift=UP * 0.3), FadeIn(words, shift=UP * 0.3)),
                lag_ratio=0.6,
            ),
            run_time=0.8,
        )
        self.play(FadeIn(pings, scale=0.8), run_time=0.35)

        self.until(0.8, rest=0.5)
        rule = VGroup(txt("silence must mean", 30, INK, bold=True), txt("broken", 30, RED, bold=True)).arrange(RIGHT, buff=0.2)
        rule[1].align_to(rule[0], DOWN)
        rule.move_to(pt((BUBBLE_LEFT + BUBBLE_RIGHT) / 2, -0.85))
        self.play(FadeIn(rule, shift=UP * 0.15), run_time=0.5)
