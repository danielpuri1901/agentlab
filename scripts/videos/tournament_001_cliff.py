"""Manim scene: Tournament 001 - the three-tier cliff and the Haiku twist.

Render:
    uvx --python 3.12 manim render -qm scripts/videos/tournament_001_cliff.py CliffScene
Output lands under media/videos/; the runner copies it to results/tournaments/001/.
"""

from manim import (
    BLUE_C,
    DOWN,
    GREEN_C,
    GREY_B,
    GREY_D,
    LEFT,
    RED_C,
    RIGHT,
    UP,
    YELLOW,
    Create,
    FadeIn,
    FadeOut,
    GrowFromEdge,
    Line,
    Rectangle,
    Scene,
    Text,
    Transform,
    VGroup,
    Write,
)

GOLD = YELLOW


def bar(height: float, color, width: float = 1.0) -> Rectangle:
    return Rectangle(
        width=width, height=max(height, 0.02), fill_color=color,
        fill_opacity=0.9, stroke_width=1, stroke_color=GREY_B,
    )


class CliffScene(Scene):
    def construct(self):
        # ---- Act 1: the setup ----
        title = Text("Compaction: what survives the squeeze?", font_size=40)
        self.play(Write(title))
        self.wait(1.2)
        self.play(title.animate.to_edge(UP).scale(0.7))

        chat = VGroup(*[
            Rectangle(width=3.2, height=0.16, fill_color=GREY_D, fill_opacity=1, stroke_width=0)
            for _ in range(14)
        ]).arrange(DOWN, buff=0.08).shift(LEFT * 4)
        for i in (2, 6, 11):
            chat[i].set_fill(GOLD)
        gate = VGroup(
            Line(UP * 1.2, UP * 0.25), Line(DOWN * 1.2, DOWN * 0.25)
        ).set_stroke(width=6).shift(RIGHT * 0)
        gate_label = Text("150-token budget", font_size=24).next_to(gate, DOWN, buff=0.4)
        summary = Rectangle(width=1.6, height=0.9, fill_color=BLUE_C, fill_opacity=0.6).shift(RIGHT * 4)
        sum_label = Text("summary", font_size=24).next_to(summary, DOWN, buff=0.2)
        caption = Text(
            "A long chat must fit through a small budget.\nGold lines are exact codes. Do they survive?",
            font_size=28, line_spacing=1.1,
        ).to_edge(DOWN)
        self.play(FadeIn(chat), Create(gate), FadeIn(gate_label))
        self.play(FadeIn(summary), FadeIn(sum_label), Write(caption))
        self.wait(2.2)
        self.play(*[FadeOut(m) for m in (chat, gate, gate_label, summary, sum_label, caption)])

        # ---- Act 2: the cliff (Nova) ----
        entrants = [
            ("truncate", 0.000, RED_C),
            ("naive", 0.004, RED_C),
            ("structured", 0.155, GREY_B),
            ("claude-code", 0.410, GREEN_C),
            ("codes-first", 0.435, GOLD),
            ("deepseek", 0.448, GREEN_C),
        ]
        scale = 7.0
        bars = VGroup()
        labels = VGroup()
        for name, recall, color in entrants:
            b = bar(recall * scale, color)
            bars.add(b)
        bars.arrange(RIGHT, buff=0.55, aligned_edge=DOWN).to_edge(DOWN, buff=1.6)
        for b, (name, recall, _) in zip(bars, entrants):
            lab = Text(name, font_size=20).next_to(b, DOWN, buff=0.15)
            val = Text(f"{recall:.0%}", font_size=20).next_to(b, UP, buff=0.1)
            labels.add(VGroup(lab, val))
        model_tag = Text("model: Nova Lite (weak)", font_size=26).to_edge(UP, buff=1.2)
        self.play(FadeIn(model_tag))
        for b, lab in zip(bars, labels):
            self.play(GrowFromEdge(b, DOWN), FadeIn(lab), run_time=0.55)
        finding1 = Text(
            "Three tiers. Intent decides everything.\nThe wording inside a tier decides nothing.",
            font_size=28, line_spacing=1.1,
        ).next_to(model_tag, DOWN, buff=0.35)
        self.play(Write(finding1))
        self.wait(2.5)

        # ---- Act 3: the twist (Haiku) ----
        new_tag = Text("model: Haiku 4.5 (strong)", font_size=26).to_edge(UP, buff=1.2)
        self.play(Transform(model_tag, new_tag), FadeOut(finding1))
        twists = {4: 0.913, 5: 0.800}
        anims = []
        for idx, new_recall in twists.items():
            old = bars[idx]
            taller = bar(new_recall * scale, old.get_fill_color()).move_to(
                old.get_bottom(), aligned_edge=DOWN
            ).align_to(old, LEFT)
            new_val = Text(f"{new_recall:.0%}", font_size=20).next_to(taller, UP, buff=0.1)
            anims.append(Transform(old, taller))
            anims.append(Transform(labels[idx][1], new_val))
        self.play(*anims, run_time=1.4)
        finding2 = Text(
            "A stronger model breaks the tie:\nlist the codes FIRST wins by 11 points.",
            font_size=28, line_spacing=1.1,
        ).next_to(model_tag, DOWN, buff=0.35)
        self.play(Write(finding2))
        self.wait(2.5)

        # ---- Close ----
        self.play(*[FadeOut(m) for m in (bars, labels, model_tag, finding2)])
        close = Text(
            "Measured, not vibes.\nAgentLab tournament 001 - total cost $2.80.",
            font_size=32, line_spacing=1.2,
        )
        self.play(Write(close))
        self.wait(2.5)
