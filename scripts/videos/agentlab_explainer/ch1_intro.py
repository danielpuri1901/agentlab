"""Chapter 1: what AgentLab is, what it runs on, and its three loops.

The reference chapter for the kit: hero title, a tile grid, three loops.
"""

from kit import (
    ACCENT,
    GOLD,
    GREEN,
    INK,
    MUTED,
    PURPLE,
    ExplainerScene,
    bucket_icon,
    chip,
    clock_icon,
    cpu_icon,
    db_icon,
    faded,
    lambda_icon,
    link,
    lit,
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
    Create,
    FadeIn,
    LaggedStart,
    Line,
    ReplacementTransform,
    Rotate,
    VGroup,
)


class Chapter(ExplainerScene):
    def beat_1(self):
        title = txt("AgentLab", 96, INK, bold=True)
        bar = Line(LEFT * 2.4, RIGHT * 2.4, color=ACCENT, stroke_width=6).next_to(title, DOWN, buff=0.22)
        tagline = txt("reads research   ·   teaches it back   ·   learns your taste", 26, MUTED).next_to(bar, DOWN, buff=0.3)
        hero = VGroup(title, bar, tagline).move_to(UP * 1.25)
        self.play(FadeIn(title, shift=UP * 0.3), Create(bar), run_time=1.0)
        self.play(FadeIn(tagline), run_time=0.5)

        reads = node("Reads", "new papers daily", ACCENT, w=2.9, h=1.05)
        teaches = node("Teaches", "short video lessons", GOLD, w=2.9, h=1.05)
        learns = node("Learns", "one reader's taste", PURPLE, w=2.9, h=1.05)
        VGroup(reads, teaches, learns).arrange(RIGHT, buff=1.0).move_to(DOWN * 1.15)
        a1, a2 = link(reads, teaches), link(teaches, learns)
        self.draw(reads, a1, teaches, a2, learns, run_time=1.6)
        self.play(travel(a1, GOLD))
        self.play(travel(a2, PURPLE))
        self.hero = hero

    def beat_2(self):
        self.clear()
        self.show_header()
        tiles = [
            tile(clock_icon(INK), "Scheduler", "starts every run", ACCENT),
            tile(cpu_icon(ACCENT), "Fargate", "ARM64 workers", ACCENT),
            tile(lambda_icon(GOLD), "Lambda", "takes every tap", ACCENT),
            tile(db_icon(MUTED), "DynamoDB", "one ledger table", MUTED),
            tile(bucket_icon(MUTED), "S3", "every artifact", MUTED),
            tile(phone_icon(INK), "Telegram", "the only interface", GREEN),
        ]
        grid = VGroup(*tiles).arrange_in_grid(rows=2, cols=3, buff=(0.35, 0.4)).move_to(UP * 0.45)
        self.play(LaggedStart(*[FadeIn(t, shift=UP * 0.15) for t in tiles], lag_ratio=0.18), run_time=2.2)
        badge = chip("you are here", GREEN, 16).next_to(tiles[5], DOWN, buff=0.18)
        self.play(lit(tiles[5], GREEN), FadeIn(badge, shift=UP * 0.1), run_time=0.6)
        self.grid = VGroup(grid, badge)

    def _loops(self):
        specs = [
            ("Experiments", "paired statistics", ACCENT),
            ("Lessons", "papers to videos", GOLD),
            ("Taste", "every tap and silence", PURPLE),
        ]
        loops = []
        for name, sub, color in specs:
            ring = loop_arrow(radius=1.0, color=color)
            label = txt(name, 24, INK, bold=True).move_to(ring.get_center())
            caption = txt(sub, 18, MUTED).next_to(ring, DOWN, buff=0.28)
            loops.append(VGroup(ring, label, caption))
        row = VGroup(*loops).arrange(RIGHT, buff=1.4).move_to(UP * 0.35)
        return row, loops

    def beat_3(self):
        self.clear()
        _row, loops = self._loops()
        ghosts = [faded(loop) for loop in loops]
        self.play(*[FadeIn(g) for g in ghosts], run_time=0.6)
        self.play(ReplacementTransform(ghosts[0], loops[0]), run_time=0.5)
        self.play(Rotate(loops[0][0], angle=-2 * PI, about_point=loops[0][0].get_center()), run_time=1.4)
        self.loops, self.ghosts = loops, ghosts

    def beat_4(self):
        second = self.loops[1]
        self.play(ReplacementTransform(self.ghosts[1], second), run_time=0.5)
        self.play(Rotate(second[0], angle=-2 * PI, about_point=second[0].get_center()), run_time=1.4)

    def beat_5(self):
        third = self.loops[2]
        self.play(ReplacementTransform(self.ghosts[2], third), run_time=0.5)
        tap = chip("tap", GREEN, 16).move_to(third[0].get_center() + LEFT * 2.3 + UP * 1.55)
        silence = chip("silence", MUTED, 16).move_to(third[0].get_center() + RIGHT * 0.0 + UP * 1.75)
        self.play(FadeIn(tap, shift=DOWN * 0.1), FadeIn(silence, shift=DOWN * 0.1), run_time=0.5)
        center = third[0].get_center()
        self.play(
            tap.animate.scale(0.4).move_to(center).set_opacity(0),
            silence.animate.scale(0.4).move_to(center).set_opacity(0),
            run_time=0.9,
        )
        self.play(
            *[Rotate(loop[0], angle=-2 * PI, about_point=loop[0].get_center()) for loop in self.loops],
            run_time=1.6,
        )
        self.remove(tap, silence)
