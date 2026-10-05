# Visual direction: One row of number tiles stays on the board while a yellow comparator weighs pairs, the tiles swap into a green sorted row, and the camera pans to the results and pulls back to the limit.

from manim import (
    DOWN,
    LEFT,
    ORIGIN,
    RIGHT,
    UP,
    Brace,
    Circumscribe,
    Create,
    DecimalNumber,
    FadeIn,
    FadeOut,
    Line,
    MathTex,
    NumberLine,
    ReplacementTransform,
    RoundedRectangle,
    Square,
    SurroundingRectangle,
    Swap,
    Text,
    TransformMatchingTex,
    ValueTracker,
    VGroup,
    Write,
    always_redraw,
)
from story_scene import BLUE, GREEN, GREY_B, RED, YELLOW, StoryScene

# The colour key: one colour per concept for the whole video.
ITEM = BLUE
COMPARATOR = YELLOW
SORTED = GREEN
UNTESTED = RED

# Illustrative tile values: the paper reports results, not its lists.
VALUES = [7, 3, 9, 1, 5]


def make_tile(value):
    box = RoundedRectangle(
        width=0.9,
        height=0.9,
        corner_radius=0.12,
        stroke_color=ITEM,
        stroke_width=3,
        fill_color=ITEM,
        fill_opacity=0.15,
    )
    return VGroup(box, MathTex(str(value), font_size=44).move_to(box))


class PaperStory(StoryScene):
    def beat_1(self):
        # The title, then the one concrete example the video keeps on screen.
        self.title = Text("SortNet sorts by comparing", font_size=60).to_edge(UP)
        self.values = list(VALUES)
        self.tiles = [make_tile(value) for value in self.values]
        self.row = VGroup(*self.tiles).arrange(RIGHT, buff=0.3).shift(0.8 * UP)
        self.play(Write(self.title))
        self.play(FadeIn(self.row, shift=0.3 * UP, lag_ratio=0.15))
        self.hold(0.6)

    def beat_2(self):
        # The problem: an order stored for one list does not fit a new list.
        stored = MathTex("2", "8", "4", "6", "0", font_size=44, color=GREY_B)
        stored.arrange(RIGHT, buff=0.62)
        label = Text("stored list", font_size=30, color=GREY_B)
        memory = VGroup(label, stored).arrange(RIGHT, buff=0.5).shift(1.7 * DOWN)
        mismatch = SurroundingRectangle(stored, color=UNTESTED, buff=0.15)
        self.play(FadeIn(memory, shift=0.2 * UP))
        self.play(Create(mismatch))
        self.play(FadeOut(memory), FadeOut(mismatch))

    def beat_3(self):
        # The real part: a comparator that reads two items at a time.
        box = RoundedRectangle(
            width=3.2,
            height=1.4,
            corner_radius=0.2,
            stroke_color=COMPARATOR,
            stroke_width=3,
        ).shift(1.7 * DOWN)
        name = Text("comparator", font_size=30, color=COMPARATOR)
        name.next_to(box, DOWN, buff=0.2)
        self.comparator = VGroup(box, name)
        self.rule = MathTex("x_i", "<", "x_j", font_size=48).move_to(box)
        self.rule[0].set_color(ITEM)
        self.rule[2].set_color(ITEM)
        self.play(FadeIn(self.comparator, shift=0.2 * UP))
        # The two symbols grow out of copies of the first two numbers, so the
        # viewer sees where they come from.
        self.play(
            ReplacementTransform(self.tiles[0][1].copy(), self.rule[0]),
            ReplacementTransform(self.tiles[1][1].copy(), self.rule[2]),
            FadeIn(self.rule[1]),
            run_time=1.5,
        )
        # Keep the formula as one object for the transforms that follow.
        self.remove(*self.rule)
        self.add(self.rule)

    def beat_4(self):
        # Compare: 7 is not less than 3, so the rule flips and the tiles swap.
        flipped = MathTex("x_j", "<", "x_i", font_size=48).move_to(self.rule)
        flipped[0].set_color(ITEM)
        flipped[2].set_color(ITEM)
        self.play(TransformMatchingTex(self.rule, flipped))
        self.rule = flipped
        self.play(Swap(self.tiles[0], self.tiles[1]))
        self.tiles[0], self.tiles[1] = self.tiles[1], self.tiles[0]
        self.values[0], self.values[1] = self.values[1], self.values[0]

    def beat_5(self):
        # The same comparison repeats until no pair is out of order.
        slots = [tile.get_center() for tile in self.tiles]
        ranked = [
            tile
            for _, tile in sorted(
                zip(self.values, self.tiles), key=lambda pair: pair[0]
            )
        ]
        self.play(
            *(tile.animate.move_to(slot) for tile, slot in zip(ranked, slots)),
            run_time=2,
        )
        self.play(
            *(
                tile[0].animate.set_stroke(SORTED).set_fill(SORTED, opacity=0.15)
                for tile in ranked
            ),
            Circumscribe(self.comparator[0], color=COMPARATOR),
        )
        self.tiles = ranked

    def beat_6(self):
        # The result gets its own part of the board: 200 lists, all sorted.
        board = 14 * RIGHT
        self.lists = VGroup(
            *(
                Square(side_length=0.22, stroke_width=1, stroke_color=GREY_B)
                for _ in range(200)
            )
        )
        self.lists.arrange_in_grid(rows=10, cols=20, buff=0.06)
        self.lists.move_to(board + 0.3 * UP)
        self.move_camera(
            frame_center=board,
            added_anims=[FadeIn(self.lists, lag_ratio=0.01)],
            run_time=1.5,
        )
        percent = MathTex(r"100\%", font_size=60, color=SORTED)
        percent.next_to(self.lists, UP, buff=0.3)
        brace = Brace(self.lists, DOWN, color=GREY_B)
        held_out = brace.get_text("200 held-out lists")
        self.play(
            self.lists.animate.set_fill(SORTED, opacity=0.8),
            FadeIn(percent, shift=0.2 * UP),
            run_time=1.2,
        )
        self.play(FadeIn(brace), Write(held_out))
        # 12 comparisons on average, as a number that counts up.
        caption = Text("comparisons on average", font_size=26, color=COMPARATOR)
        caption.next_to(held_out, DOWN, buff=0.35).shift(0.4 * RIGHT)
        self.count = ValueTracker(0)
        number = always_redraw(
            lambda: DecimalNumber(
                self.count.get_value(),
                num_decimal_places=0,
                font_size=40,
                color=COMPARATOR,
            ).next_to(caption, LEFT, buff=0.2)
        )
        self.add(number)
        self.play(FadeIn(caption), self.count.animate.set_value(12), run_time=1.3)

    def beat_7(self):
        # The limit: list lengths past 50 were never tested.
        self.move_camera(frame_center=7 * RIGHT + 1.2 * DOWN, zoom=0.5, run_time=1.5)
        line = NumberLine(
            x_range=[0, 80, 10], length=24, include_numbers=True, font_size=60
        ).move_to(7 * RIGHT + 5.2 * DOWN)
        tested = Line(line.n2p(0), line.n2p(50), color=SORTED, stroke_width=10)
        untested = Line(line.n2p(50), line.n2p(80), color=UNTESTED, stroke_width=10)
        name = Text("list length", font_size=56, color=GREY_B)
        name.next_to(line, DOWN, buff=0.4)
        note = Text("not tested", font_size=56, color=UNTESTED)
        note.next_to(untested, UP, buff=0.4)
        self.play(Create(line), FadeIn(name), run_time=1)
        self.play(Create(tested), run_time=0.8)
        self.play(Create(untested), FadeIn(note, shift=0.2 * UP))

    def beat_8(self):
        # The street-test question, back on the first board.
        self.move_camera(frame_center=ORIGIN, zoom=1, run_time=1.5)
        question = Text(
            "Would a pairwise comparator beat your sort step?", font_size=32
        ).to_edge(DOWN)
        self.play(self.row.animate.set_opacity(0.3), FadeIn(question, shift=0.2 * UP))
        self.play(Circumscribe(self.comparator, color=COMPARATOR), run_time=1.2)
