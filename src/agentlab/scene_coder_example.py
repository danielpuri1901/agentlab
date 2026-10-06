# Visual direction: The frozen weight matrix W0 anchors one board while a thin orange pair A and B grows beside it, feeds the sum h, merges into a green W, and swaps for a new task; results, the shared base, and the limit follow on the same calm board.

import numpy as np
from manim import (
    DOWN,
    LEFT,
    PI,
    RIGHT,
    UP,
    Arc,
    Arrow,
    Brace,
    Circumscribe,
    Create,
    DashedVMobject,
    DecimalNumber,
    FadeIn,
    FadeOut,
    FadeTransform,
    GrowArrow,
    LaggedStart,
    Line,
    MathTex,
    Rectangle,
    ReplacementTransform,
    RoundedRectangle,
    ShowPassingFlash,
    SurroundingRectangle,
    Text,
    TransformFromCopy,
    TransformMatchingTex,
    ValueTracker,
    VGroup,
    Write,
    always_redraw,
)
from story_scene import (
    BLUE,
    GOLD,
    GREEN,
    GREY_B,
    GREY_D,
    ORANGE,
    RED,
    YELLOW,
    StoryScene,
)

# The colour key, one colour per concept for the whole video.
FROZEN = BLUE  # the pretrained weight W0
ADAPTER = ORANGE  # the low-rank pair A and B
MERGED = GREEN  # the output h and the merged weight W
NEW_TASK = GOLD  # a second adapter pair


def matrix_block(width, height, color, tex, font_size=40, opacity=0.18):
    """A matrix as a rounded box with its symbol inside."""
    box = RoundedRectangle(
        width=width,
        height=height,
        corner_radius=0.08,
        stroke_color=color,
        stroke_width=3,
        fill_color=color,
        fill_opacity=opacity,
    )
    return VGroup(box, MathTex(tex, font_size=font_size, color=color).move_to(box))


def lock_icon():
    body = RoundedRectangle(
        width=0.36,
        height=0.28,
        corner_radius=0.05,
        stroke_color=YELLOW,
        fill_color=YELLOW,
        fill_opacity=0.7,
    )
    shackle = Arc(radius=0.12, start_angle=0, angle=PI, color=YELLOW, stroke_width=3)
    shackle.next_to(body, UP, buff=0)
    return VGroup(body, shackle)


class PaperStory(StoryScene):
    def beat_1(self):
        # The title, then the one picture the whole video keeps: W0 and the
        # small pair beside it.
        self.title = Text("LoRA: Low-Rank Adaptation\nof Large Language Models", font_size=44)
        self.title.to_edge(UP)
        self.W0 = matrix_block(2.6, 2.0, FROZEN, "W_0").move_to(1.0 * LEFT + 0.5 * DOWN)
        self.A = matrix_block(0.45, 1.4, ADAPTER, "A", 30)
        self.B = matrix_block(1.4, 0.45, ADAPTER, "B", 30)
        pair = VGroup(self.A, self.B).arrange(RIGHT, buff=0.3)
        pair.next_to(self.W0, RIGHT, buff=0.8)
        self.play(Write(self.title), run_time=1.5)
        self.play(FadeIn(self.W0, shift=0.2 * UP))
        self.play(
            LaggedStart(
                FadeIn(self.A, shift=0.2 * LEFT),
                FadeIn(self.B, shift=0.2 * LEFT),
                lag_ratio=0.4,
            ),
            run_time=1.2,
        )
        self.hold(0.6)

    def beat_2(self):
        # The problem, concrete: every task copies all 175B parameters.
        self.play(
            FadeOut(self.title, shift=0.3 * UP),
            FadeOut(VGroup(self.A, self.B)),
            self.W0.animate.scale(1.2).move_to(1.0 * UP),
            run_time=1.2,
        )
        self.params = Text("175B params", font_size=34).next_to(self.W0, UP, buff=0.25)
        self.play(FadeIn(self.params, shift=0.2 * DOWN), run_time=0.6)
        self.checkpoints = VGroup(
            *(
                RoundedRectangle(
                    width=1.6,
                    height=0.9,
                    corner_radius=0.06,
                    stroke_color=FROZEN,
                    stroke_width=2,
                    fill_color=FROZEN,
                    fill_opacity=0.12,
                )
                for _ in range(3)
            )
        ).arrange(RIGHT, buff=0.4)
        self.checkpoints.move_to(1.55 * DOWN)
        # Each checkpoint grows out of a copy of W0: the viewer sees where it
        # comes from.
        self.play(
            LaggedStart(
                *(TransformFromCopy(self.W0[0], box) for box in self.checkpoints),
                lag_ratio=0.3,
            ),
            run_time=2.0,
        )
        sizes = VGroup(
            *(Text("350 GB", font_size=24).move_to(box) for box in self.checkpoints)
        )
        self.play(FadeIn(sizes, lag_ratio=0.2), run_time=0.8)
        self.per_task = Text("350 GB per task", font_size=30, color=RED)
        self.per_task.next_to(self.checkpoints, DOWN, buff=0.3)
        self.play(FadeIn(self.per_task, shift=0.2 * UP), run_time=0.6)
        self.checkpoints.add(*sizes)
        self.hold(0.6)

    def beat_3(self):
        # Freeze W0: a lock snaps on, and a gradient stops at its edge.
        self.play(
            FadeOut(VGroup(self.params, self.checkpoints, self.per_task)),
            self.W0.animate.scale(1 / 1.2).move_to(1.0 * LEFT + 0.9 * UP),
            run_time=1.0,
        )
        self.lock = lock_icon().move_to(self.W0[0].get_corner(UP + RIGHT) + 0.2 * (UP + RIGHT))
        self.play(FadeIn(self.lock, scale=1.5), run_time=0.6)
        self.frozen = Text("frozen", font_size=30, color=YELLOW)
        self.frozen.next_to(self.W0, UP, buff=0.25)
        self.play(Write(self.frozen), run_time=0.7)
        gradient = Arrow(
            self.W0[0].get_bottom() + 1.3 * DOWN,
            self.W0[0].get_bottom() + 0.1 * DOWN,
            color=RED,
            buff=0,
        )
        self.play(GrowArrow(gradient), run_time=0.7)
        self.play(FadeOut(gradient, shift=0.3 * DOWN), run_time=0.6)

    def beat_4(self):
        # A parallel path: x feeds both W0 and the small pair A then B.
        self.x = MathTex(r"\mathbf{x}", font_size=48).move_to(5.0 * LEFT + 0.9 * UP)
        self.A.move_to(1.9 * LEFT + 1.4 * DOWN)
        self.B.move_to(0.25 * RIGHT + 1.4 * DOWN)
        self.to_W0 = Arrow(self.x.get_right(), self.W0[0].get_left(), buff=0.15)
        self.to_A = Arrow(self.x.get_right(), self.A[0].get_left(), buff=0.15)
        self.A_to_B = Arrow(self.A[0].get_right(), self.B[0].get_left(), buff=0.1, color=ADAPTER)
        self.play(FadeOut(self.frozen), FadeIn(self.x, shift=0.3 * RIGHT), run_time=0.6)
        self.play(GrowArrow(self.to_W0), run_time=0.6)
        self.play(
            LaggedStart(
                GrowArrow(self.to_A),
                FadeIn(self.A, shift=0.2 * RIGHT),
                GrowArrow(self.A_to_B),
                FadeIn(self.B, shift=0.2 * RIGHT),
                lag_ratio=0.35,
            ),
            run_time=1.8,
        )
        self.rank = MathTex("r = 1", font_size=32, color=ADAPTER)
        self.rank.next_to(self.A_to_B, DOWN, buff=0.45)
        self.width_brace = Brace(self.W0[0], UP, color=GREY_B)
        self.width_label = self.width_brace.get_tex(r"d = 12{,}288")
        self.width_label.scale(0.8).set_color(FROZEN)
        self.play(FadeIn(self.rank), run_time=0.5)
        self.play(
            FadeOut(self.lock),
            FadeIn(self.width_brace),
            Write(self.width_label),
            run_time=1.0,
        )
        self.hold(1.0)

    def beat_5(self):
        # The two outputs add up to h; B starts at zero, so h starts as W0 x.
        self.plus = MathTex("+", font_size=48).move_to(2.2 * RIGHT + 0.9 * UP)
        self.h = MathTex(r"\mathbf{h}", font_size=48, color=MERGED).move_to(3.5 * RIGHT + 0.9 * UP)
        self.W0_to_plus = Arrow(self.W0[0].get_right(), self.plus.get_left(), buff=0.15, color=FROZEN)
        self.B_to_plus = Arrow(self.B[0].get_right(), self.plus.get_bottom(), buff=0.15, color=ADAPTER)
        self.plus_to_h = Arrow(self.plus.get_right(), self.h.get_left(), buff=0.15, color=MERGED)
        self.play(FadeOut(VGroup(self.width_brace, self.width_label)), run_time=0.4)
        self.play(
            LaggedStart(
                GrowArrow(self.W0_to_plus),
                GrowArrow(self.B_to_plus),
                FadeIn(self.plus),
                lag_ratio=0.3,
            ),
            run_time=1.0,
        )
        self.play(GrowArrow(self.plus_to_h), FadeIn(self.h, shift=0.2 * RIGHT), run_time=0.6)
        self.equation = MathTex("h", "=", "W_0", "x", "+", "B", "A", "x", font_size=48)
        for index, color in ((0, MERGED), (2, FROZEN), (5, ADAPTER), (6, ADAPTER)):
            self.equation[index].set_color(color)
        self.equation.to_edge(UP)
        self.play(Write(self.equation), run_time=1.2)
        self.play(Circumscribe(self.B, color=ADAPTER), run_time=1.0)
        # A signal runs along both paths.
        paths = [self.to_W0, self.W0_to_plus, self.to_A, self.A_to_B, self.B_to_plus, self.plus_to_h]
        self.play(
            *(ShowPassingFlash(path.copy().set_color(YELLOW), time_width=0.5) for path in paths),
            run_time=1.2,
        )
        self.hold(0.8)

    def beat_6(self):
        # Deploy: B times A becomes one matrix and merges into W0.
        merged_equation = MathTex("W", "=", "W_0", "+", "B", "A", font_size=48)
        for index, color in ((0, MERGED), (2, FROZEN), (4, ADAPTER), (5, ADAPTER)):
            merged_equation[index].set_color(color)
        merged_equation.to_edge(UP)
        wiring = VGroup(
            self.x, self.to_W0, self.to_A, self.A_to_B, self.rank,
            self.plus, self.h, self.W0_to_plus, self.B_to_plus, self.plus_to_h,
        )
        self.play(
            FadeOut(wiring),
            TransformMatchingTex(self.equation, merged_equation),
            run_time=1.0,
        )
        self.equation = merged_equation
        product = matrix_block(2.6, 2.0, ADAPTER, "BA", 36, 0.1).move_to(2.6 * RIGHT + 0.9 * UP)
        self.play(ReplacementTransform(VGroup(self.A, self.B), product), run_time=1.0)
        self.play(product.animate.move_to(self.W0), run_time=0.8)
        self.W = matrix_block(2.6, 2.0, MERGED, "W", 40, 0.25).move_to(self.W0)
        self.play(FadeOut(product), ReplacementTransform(self.W0, self.W), run_time=0.8)
        self.cost = Text("same shape, no extra cost", font_size=28, color=MERGED)
        self.cost.next_to(self.W, DOWN, buff=0.35)
        self.play(FadeIn(self.cost, shift=0.2 * UP), run_time=0.6)
        self.hold(0.8)

    def beat_7(self):
        # Switch tasks: subtract the old BA, merge a new pair.
        self.swap_title = Text("Swap adapters", font_size=40, color=ADAPTER).to_edge(UP)
        base = matrix_block(2.6, 2.0, FROZEN, "W_0").move_to(self.W)
        old = matrix_block(1.2, 1.0, ADAPTER, "BA", 28, 0.15).move_to(self.W)
        self.play(
            FadeOut(self.cost),
            FadeTransform(self.equation, self.swap_title),
            ReplacementTransform(self.W, base),
            FadeIn(old),
            run_time=0.9,
        )
        self.play(old.animate.shift(3.5 * RIGHT + 0.6 * UP).set_opacity(0), run_time=0.8)
        self.remove(old)
        new_pair = VGroup(
            matrix_block(0.4, 1.1, NEW_TASK, "A'", 24),
            matrix_block(1.1, 0.4, NEW_TASK, "B'", 24),
        ).arrange(RIGHT, buff=0.15)
        new_pair.move_to(3.2 * RIGHT + 0.9 * UP)
        self.play(FadeIn(new_pair, shift=0.3 * LEFT), run_time=0.6)
        self.play(new_pair.animate.move_to(base), run_time=0.8)
        self.W = matrix_block(2.6, 2.0, MERGED, "W'", 40, 0.25).move_to(base)
        self.play(FadeOut(new_pair), ReplacementTransform(base, self.W), run_time=0.8)
        self.stays = Text("175B base stays in memory", font_size=28, color=FROZEN)
        self.stays.next_to(self.W, DOWN, buff=0.35)
        self.play(FadeIn(self.stays, shift=0.2 * UP), run_time=0.6)
        self.hold(0.8)

    def beat_8(self):
        # Results: storage, trainable parameters, and speed.
        title = Text("350 GB vs 35 MB", font_size=44).to_edge(UP)
        self.play(
            FadeOut(VGroup(self.W, self.stays)),
            FadeTransform(self.swap_title, title),
            run_time=0.8,
        )
        self.result_title = title
        floor = Line(4.8 * LEFT + 1.9 * DOWN, 0.4 * LEFT + 1.9 * DOWN, color=GREY_D)
        full = Rectangle(width=1.2, height=3.8, stroke_color=FROZEN, fill_color=FROZEN, fill_opacity=0.35)
        full.move_to(floor.get_start() + 1.2 * RIGHT, aligned_edge=DOWN)
        lora = Rectangle(width=1.2, height=0.04, stroke_color=ADAPTER, fill_color=ADAPTER, fill_opacity=0.8)
        lora.move_to(floor.get_start() + 3.4 * RIGHT, aligned_edge=DOWN)
        full_size = Text("350 GB", font_size=26, color=FROZEN).next_to(full, UP, buff=0.15)
        lora_size = Text("35 MB", font_size=26, color=ADAPTER).next_to(lora, UP, buff=0.15)
        full_name = Text("full fine-tuning", font_size=22, color=GREY_B).next_to(full, DOWN, buff=0.15)
        lora_name = Text("LoRA", font_size=22, color=GREY_B).next_to(lora, DOWN, buff=0.15)
        self.play(Create(floor), run_time=0.4)
        self.play(FadeIn(full, shift=0.4 * UP), FadeIn(full_name), run_time=1.0)
        self.play(FadeIn(full_size), FadeIn(lora), FadeIn(lora_name), FadeIn(lora_size), run_time=0.8)
        # Trainable parameters fall by 10,000 times, as a number that counts.
        factor = ValueTracker(1)
        count = always_redraw(
            lambda: DecimalNumber(factor.get_value(), num_decimal_places=0, font_size=48, color=MERGED)
            .move_to(3.2 * RIGHT + 1.2 * UP)
        )
        fewer = Text("times fewer trainable parameters", font_size=24).move_to(3.2 * RIGHT + 0.55 * UP)
        self.add(count)
        self.play(FadeIn(fewer), factor.animate.set_value(10000), run_time=2.0)
        speed = MathTex("32.5", r"\rightarrow", "43.1", font_size=40).move_to(3.2 * RIGHT + 0.8 * DOWN)
        speed[2].set_color(MERGED)
        unit = Text("tokens/s per V100, 25% faster", font_size=22, color=GREY_B)
        unit.next_to(speed, DOWN, buff=0.2)
        self.play(Write(speed), FadeIn(unit), run_time=1.0)
        self.results = VGroup(floor, full, lora, full_size, lora_size, full_name, lora_name, count, fewer, speed, unit)
        self.hold(1.0)

    def beat_9(self):
        # The implication: one shared base, many small adapters around it.
        self.base = matrix_block(2.0, 1.6, FROZEN, "W_0", 34).move_to(0.2 * DOWN)
        self.play(FadeOut(VGroup(self.results, self.result_title)), FadeIn(self.base, scale=0.8), run_time=0.8)
        center = self.base.get_center()
        directions = [np.array([np.cos(a), np.sin(a), 0.0]) for a in np.linspace(0, 2 * PI, 100, endpoint=False)]
        spokes = VGroup(
            *(Line(center + 1.15 * d, center + 1.9 * d, stroke_width=0.6, color=GREY_D) for d in directions)
        )
        adapters = VGroup(
            *(
                Rectangle(width=0.06, height=0.26, stroke_width=0, fill_color=ADAPTER, fill_opacity=0.8)
                .move_to(center + 2.05 * d)
                .rotate(np.arctan2(d[1], d[0]))
                for d in directions
            )
        )
        self.play(Create(spokes, lag_ratio=0.01), FadeIn(adapters, lag_ratio=0.01), run_time=2.0)
        self.shared = Text("1 base + 100 adapters", font_size=36).to_edge(UP)
        self.play(FadeIn(self.shared, shift=0.2 * DOWN), run_time=0.6)
        self.ring = VGroup(spokes, adapters)
        self.hold(1.0)

    def beat_10(self):
        # The limit: only the attention matrices were adapted.
        title = Text("Attention only", font_size=40, color=ADAPTER).to_edge(UP)
        self.play(
            FadeOut(VGroup(self.base, self.ring)),
            FadeTransform(self.shared, title),
            run_time=0.8,
        )
        self.limit_title = title
        block = RoundedRectangle(width=6.4, height=4.2, corner_radius=0.12, stroke_color=GREY_D, stroke_width=2)
        block.move_to(0.55 * DOWN)
        block_name = Text("Transformer block", font_size=24, color=GREY_B).next_to(block, UP, buff=0.15)
        attention = matrix_block(5.4, 0.9, ADAPTER, r"\text{Attention: } W_q,\ W_k,\ W_v,\ W_o", 30)
        attention.move_to(block.get_center() + 1.25 * UP)
        untested = VGroup(
            *(
                matrix_block(5.4, 0.6, GREY_B, rf"\text{{{name}}}", 28, 0.08)
                for name in ("MLP", "LayerNorm", "biases")
            )
        ).arrange(DOWN, buff=0.18)
        untested.move_to(block.get_center() + 0.6 * DOWN)
        self.play(Create(block), FadeIn(block_name), run_time=0.8)
        self.play(
            LaggedStart(FadeIn(attention), *(FadeIn(part) for part in untested), lag_ratio=0.25),
            run_time=1.2,
        )
        # Dim what the paper did not test, then point at what it did.
        self.play(untested.animate.set_opacity(0.35), run_time=0.6)
        outline = DashedVMobject(SurroundingRectangle(untested, color=RED, buff=0.12), num_dashes=40)
        later = Text("future work", font_size=24, color=RED).next_to(block, RIGHT, buff=0.25)
        later.match_y(untested)
        self.play(Create(outline), FadeIn(later), run_time=0.8)
        self.play(Circumscribe(attention, color=ADAPTER), run_time=1.2)
        self.limit_parts = VGroup(block, block_name, attention, untested, outline, later)
        self.hold(1.0)

    def beat_11(self):
        # The street-test question: 100 full copies, or one base and 100 pairs.
        question = Text("Which would you choose?", font_size=40).to_edge(UP)
        self.play(
            FadeOut(self.limit_parts),
            FadeTransform(self.limit_title, question),
            run_time=0.8,
        )
        upper = Line(0.7 * UP, 2.4 * UP, color=GREY_D)
        lower = Line(0.7 * DOWN, 2.6 * DOWN, color=GREY_D)
        mark = MathTex("?", font_size=72, color=YELLOW)
        copies = VGroup(
            *(
                Rectangle(width=2.2, height=0.16, stroke_color=FROZEN, stroke_width=1, fill_color=FROZEN, fill_opacity=0.3)
                for _ in range(10)
            )
        ).arrange(DOWN, buff=0.08)
        copies.move_to(3.3 * LEFT + 0.3 * UP)
        copies_name = Text("100 full copies", font_size=26, color=FROZEN).next_to(copies, DOWN, buff=0.3)
        copies_size = MathTex(r"100 \times 350\ \text{GB}", font_size=34).next_to(copies_name, DOWN, buff=0.2)
        base = matrix_block(2.0, 1.2, FROZEN, "W_0", 30).move_to(3.3 * RIGHT + 0.9 * UP)
        shelf = VGroup(
            *(
                Rectangle(width=0.06, height=0.4, stroke_width=0, fill_color=ADAPTER, fill_opacity=0.8)
                for _ in range(25)
            )
        ).arrange(RIGHT, buff=0.04)
        shelf.next_to(base, DOWN, buff=0.3)
        shared_name = Text("1 base + 100 adapters", font_size=26, color=ADAPTER).next_to(shelf, DOWN, buff=0.3)
        shared_size = MathTex(r"350\ \text{GB} + 100 \times 35\ \text{MB}", font_size=34)
        shared_size.next_to(shared_name, DOWN, buff=0.2)
        self.play(Create(upper), Create(lower), run_time=0.4)
        self.play(
            LaggedStart(FadeIn(copies, lag_ratio=0.1), FadeIn(copies_name), Write(copies_size), lag_ratio=0.3),
            run_time=1.4,
        )
        self.play(
            LaggedStart(FadeIn(base), FadeIn(shelf, lag_ratio=0.05), FadeIn(shared_name), Write(shared_size), lag_ratio=0.3),
            run_time=1.4,
        )
        self.play(FadeIn(mark, scale=1.5), run_time=0.6)
        self.hold(1.5)
