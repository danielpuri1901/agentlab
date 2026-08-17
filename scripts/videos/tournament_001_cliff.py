"""Manim scene v2: teach compaction from zero, then the tournament result.

v1 feedback: concept was not taught before results; some elements off-screen.
v2: mechanism-first acts, conservative layout (everything width-guarded).

Render:
    uvx --python 3.12 manim render -qm scripts/videos/tournament_001_cliff.py CliffScene
"""

from manim import (
    DOWN,
    GREEN_C,
    GREY_B,
    GREY_D,
    LEFT,
    RED_C,
    RIGHT,
    UP,
    YELLOW,
    Cross,
    FadeIn,
    FadeOut,
    GrowFromEdge,
    Rectangle,
    Scene,
    SurroundingRectangle,
    Text,
    Transform,
    VGroup,
    Write,
)

GOLD = YELLOW
MAXW = 12.5  # safe content width


def fit(m, w=MAXW):
    if m.width > w:
        m.scale_to_fit_width(w)
    return m


def caption(s, size=28):
    return fit(Text(s, font_size=size, line_spacing=1.1)).to_edge(DOWN, buff=0.5)


class CliffScene(Scene):
    def show_caption(self, text_obj, hold=2.4):
        self.play(Write(text_obj))
        self.wait(hold)
        self.play(FadeOut(text_obj))

    def construct(self):
        # ---------- Act 0: the problem ----------
        t = fit(Text("Why agents forget: compaction, explained", font_size=38))
        self.play(Write(t))
        self.wait(1.4)
        self.play(t.animate.to_edge(UP, buff=0.4).scale(0.65))

        window = Rectangle(width=5.2, height=4.6, stroke_color=GREY_B).shift(LEFT * 3.2)
        wlabel = fit(Text("the agent's memory window", font_size=22)).next_to(window, UP, buff=0.15)
        lines = VGroup(*[
            Rectangle(width=4.6, height=0.22, fill_color=GREY_D, fill_opacity=1, stroke_width=0)
            for _ in range(14)
        ]).arrange(DOWN, buff=0.075).move_to(window)
        code_line = fit(Text("note: item-42 resolved with CODE-60494", font_size=18))
        code_line.set_color(GOLD).move_to(lines[5])
        full = fit(Text("FULL", font_size=30)).set_color(RED_C).next_to(window, RIGHT, buff=0.6)
        self.play(FadeIn(window), FadeIn(wlabel))
        for chunk in (lines[:5], VGroup(code_line), lines[6:]):
            self.play(FadeIn(chunk), run_time=0.7)
        self.play(FadeIn(full))
        self.show_caption(caption("A chat grows until the window is full.\nSomething must be thrown away. But what?"))

        # ---------- Act 1: what compaction is ----------
        self.play(FadeOut(full))
        summary_box = Rectangle(width=4.6, height=1.5, stroke_color=GREY_B).shift(RIGHT * 3.4 + UP * 1.4)
        slabel = fit(Text("the summary that replaces it", font_size=20)).next_to(summary_box, UP, buff=0.12)
        self.play(FadeIn(summary_box), FadeIn(slabel))
        self.show_caption(caption(
            'Compaction: a model REWRITES the old chat as a short summary.\nThe instructions it gets decide what survives.'
        ), hold=2.8)

        naive_sum = fit(Text('"We discussed several items.\nCodes were recorded."', font_size=20), 4.2)
        naive_sum.move_to(summary_box)
        ntag = fit(Text('instruction: "summarize concisely"', font_size=18)).next_to(summary_box, DOWN, buff=0.15)
        self.play(Write(naive_sum), FadeIn(ntag))
        probe = fit(Text('Q: what was the code for item-42?', font_size=20)).shift(RIGHT * 3.4 + DOWN * 1.2)
        x = Cross(scale_factor=0.25).next_to(probe, DOWN, buff=0.2)
        self.play(Write(probe), FadeIn(x))
        self.show_caption(caption(
            "A plain summary DESCRIBES the codes.\nIt does not COPY them. The answer is gone forever."
        ), hold=2.8)

        good_sum = fit(Text('"item-42: CODE-60494\n(codes listed first, verbatim)"', font_size=20), 4.2)
        good_sum.move_to(summary_box).set_color(GOLD)
        gtag = fit(Text('instruction: "list every code first"', font_size=18)).next_to(summary_box, DOWN, buff=0.15)
        check = fit(Text("answered", font_size=20)).set_color(GREEN_C).next_to(probe, DOWN, buff=0.2)
        self.play(Transform(naive_sum, good_sum), Transform(ntag, gtag), Transform(x, check))
        self.show_caption(caption(
            "Change one sentence of instructions,\nand the same model saves the code."
        ), hold=2.6)
        self.play(*[FadeOut(m) for m in (window, wlabel, lines, code_line, summary_box, slabel, naive_sum, ntag, probe, x)])

        # ---------- Act 2: what a harness is ----------
        model_chip = Rectangle(width=2.2, height=1.0, fill_color=GREY_D, fill_opacity=1)
        mlabel = fit(Text("the model", font_size=22)).move_to(model_chip)
        wrap = SurroundingRectangle(model_chip, buff=0.7, color=GREY_B)
        wname = fit(Text("a HARNESS (Claude Code, DeepSeek Harness, ...)", font_size=22)).next_to(wrap, UP, buff=0.2)
        scroll = fit(Text("ships its own compaction instructions", font_size=20)).set_color(GOLD).next_to(wrap, DOWN, buff=0.2)
        self.play(FadeIn(model_chip), FadeIn(mlabel))
        self.play(FadeIn(wrap), Write(wname), Write(scroll))
        self.show_caption(caption(
            "A harness is the program wrapped around the model.\nEach harness writes its own shrink instructions.\nWe made those instructions COMPETE on the same test."
        ), hold=3.2)
        self.play(*[FadeOut(m) for m in (model_chip, mlabel, wrap, wname, scroll)])

        # ---------- Act 3: the tournament ----------
        entrants = [
            ("delete old half", 0.000, RED_C),
            ("plain summary", 0.004, RED_C),
            ("keep codes (vague)", 0.155, GREY_B),
            ("Claude Code-style", 0.410, GREEN_C),
            ("codes first (ours)", 0.435, GOLD),
            ("DeepSeek's prompt", 0.448, GREEN_C),
        ]
        scale = 6.0
        bars = VGroup(*[
            Rectangle(width=1.35, height=max(r * scale, 0.03), fill_color=c, fill_opacity=0.9, stroke_width=1)
            for _, r, c in entrants
        ]).arrange(RIGHT, buff=0.45, aligned_edge=DOWN).to_edge(DOWN, buff=1.7)
        fit(bars, 13.0)
        labels = VGroup()
        for b, (name, r, _) in zip(bars, entrants):
            lab = fit(Text(name, font_size=17), 1.9).next_to(b, DOWN, buff=0.12)
            val = Text(f"{r:.0%}", font_size=18).next_to(b, UP, buff=0.08)
            labels.add(VGroup(lab, val))
        mtag = fit(Text("weak model (Nova): only the INSTRUCTIONS' intent matters", font_size=24)).to_edge(UP, buff=1.1)
        self.play(FadeIn(mtag))
        for b, lab in zip(bars, labels):
            self.play(GrowFromEdge(b, DOWN), FadeIn(lab), run_time=0.45)
        self.wait(2.0)

        new_tag = fit(Text("strong model (Haiku): now the FORMAT matters too", font_size=24)).to_edge(UP, buff=1.1)
        self.play(Transform(mtag, new_tag))
        anims = []
        for idx, new_r in ((4, 0.913), (5, 0.800)):
            old = bars[idx]
            taller = Rectangle(
                width=old.width, height=new_r * scale, fill_color=old.get_fill_color(),
                fill_opacity=0.9, stroke_width=1,
            ).move_to(old.get_bottom(), aligned_edge=DOWN)
            anims += [Transform(old, taller),
                      Transform(labels[idx][1], Text(f"{new_r:.0%}", font_size=18).next_to(taller, UP, buff=0.08))]
        self.play(*anims, run_time=1.3)
        self.show_caption(caption('"Codes first" wins by 11 points. Measured, not vibes.'), hold=2.6)

        # ---------- Close ----------
        self.play(*[FadeOut(m) for m in (bars, labels, mtag, t)])
        close = fit(Text(
            "Compaction = rewriting memory under a budget.\nThe instructions decide what survives.\nAgentLab tournament 001 - total cost $2.80.",
            font_size=30, line_spacing=1.2,
        ))
        self.play(Write(close))
        self.wait(3.0)
