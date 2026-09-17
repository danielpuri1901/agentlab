"""Golden generated scene: the compaction house move, written by hand to
the StoryScene contract. Used by the render test and by scene_code's guard
test as the canonical clean example."""

# Visual direction: A crowded house compresses into one case while the needed item stays visible.

from manim import (
    DOWN,
    LEFT,
    RIGHT,
    UP,
    Create,
    Cross,
    DashedLine,
    FadeIn,
    FadeOut,
    Line,
    Rectangle,
    RoundedRectangle,
    Transform,
    VGroup,
)
from story_scene import ACCENT, GOLD, GREEN, GREY_B, GREY_D, RED, WHITE, StoryScene


class PaperStory(StoryScene):
    def beat_1(self):
        self.house = Rectangle(width=4.6, height=3.2, stroke_color=GREY_B).shift(LEFT * 3.2 + UP * 0.6)
        self.furniture = VGroup(
            *[Rectangle(width=0.9, height=0.5, fill_color=GREY_D, fill_opacity=1, stroke_width=1) for _ in range(9)]
        ).arrange_in_grid(rows=3, buff=0.25).move_to(self.house)
        self.passport = RoundedRectangle(width=0.5, height=0.34, corner_radius=0.05, fill_color=GOLD, fill_opacity=1, stroke_width=1)
        self.passport.move_to(self.furniture[4])
        self.case = Rectangle(width=1.7, height=1.1, stroke_color=GOLD).shift(RIGHT * 3.6 + UP * 0.3)
        house_label = self.label("house = the chat", size=22).next_to(self.house, UP, buff=0.15)
        case_label = self.label("suitcase = the budget", size=22).next_to(self.case, UP, buff=0.15)
        self.play(Create(self.house), FadeIn(self.furniture), FadeIn(self.passport), run_time=1.6)
        self.play(Create(self.case), FadeIn(house_label), FadeIn(case_label), run_time=1.2)
        self.hold(0.5)

    def beat_2(self):
        self.list_label = self.label('packing list: "summarize concisely"', size=24).to_edge(UP, buff=0.5)
        self.play(FadeIn(self.list_label), run_time=0.8)
        self.sofa = self.furniture[0].copy()
        self.play(self.sofa.animate.scale(0.55).move_to(self.case), run_time=1.2)
        self.play(self.passport.animate.set_opacity(0.3), run_time=0.6)
        self.cross = Cross(scale_factor=0.25).next_to(self.case, DOWN, buff=0.35)
        self.play(FadeIn(self.cross), run_time=0.6)
        self.hold(0.4)

    def beat_3(self):
        new_label = self.label('packing list: "list every code first"', size=24).to_edge(UP, buff=0.5)
        self.play(Transform(self.list_label, new_label), FadeOut(self.sofa), run_time=0.9)
        self.play(self.passport.animate.set_opacity(1.0).move_to(self.case), run_time=1.3)
        check = Line(LEFT * 0.15 + DOWN * 0.05, RIGHT * 0.05 + DOWN * 0.25, color=GREEN, stroke_width=6)
        check2 = Line(RIGHT * 0.05 + DOWN * 0.25, RIGHT * 0.35 + UP * 0.2, color=GREEN, stroke_width=6)
        self.check = VGroup(check, check2).next_to(self.case, DOWN, buff=0.35)
        self.play(FadeOut(self.cross), Create(self.check), run_time=0.9)
        self.hold(0.4)

    def beat_4(self):
        base = self.case.get_bottom() + DOWN * 1.9
        bad_bar = Rectangle(width=0.7, height=0.04, fill_color=RED, fill_opacity=1, stroke_width=0)
        good_bar = Rectangle(width=0.7, height=0.04, fill_color=GOLD, fill_opacity=1, stroke_width=0)
        bars = VGroup(bad_bar, good_bar).arrange(RIGHT, buff=0.6).move_to(base, aligned_edge=DOWN)
        bad_label = self.label("0%", size=26, color=RED).next_to(bad_bar, UP, buff=0.1)
        # The counter is placed once, above where the bar will END, and is
        # never moved while it counts: a counter's updater re-becomes the
        # text every frame, so animating its position at the same time is
        # the family-size hazard video_scenes.py documents.
        tall_bar = good_bar.copy().stretch_to_fit_height(1.3, about_edge=DOWN)
        good_value, count_up = self.counter(0, 44, suffix="%", size=26, color=GOLD)
        good_value.next_to(tall_bar, UP, buff=0.1)
        self.play(FadeOut(self.check), FadeIn(bars), FadeIn(bad_label), FadeIn(good_value), run_time=0.7)
        self.play(good_bar.animate.stretch_to_fit_height(1.3, about_edge=DOWN), count_up, run_time=2.0)
        self.freeze(good_value)
        self.hold(0.4)

    def beat_5(self):
        boundary = DashedLine(
            self.house.get_corner(DOWN + RIGHT) + RIGHT * 0.3,
            self.house.get_corner(UP + RIGHT) + RIGHT * 0.3,
            color=GREY_B,
        )
        boundary_label = self.label("50 items tested", size=18, color=GREY_B).next_to(boundary, RIGHT, buff=0.1)
        self.play(Create(boundary), FadeIn(boundary_label), run_time=1.2)
        question = self.label("Does your compaction prompt name the codes first?", size=26, color=WHITE, width=40)
        # Place the question below the house, above the caption band, clear of the result bars.
        question.move_to(LEFT * 3.2 + DOWN * 1.85)
        self.fit(question, max_w=5.8, max_h=0.65)
        self.play(FadeIn(question), self.case.animate.set_stroke(ACCENT, width=4), run_time=1.4)
        self.hold(0.6)

    def beat_6(self):
        self.hold(0.6)

    def beat_7(self):
        self.hold(0.6)

    def beat_8(self):
        self.hold(0.6)
