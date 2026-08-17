"""Manim scene v3: compaction as a house move - one metaphor, start to finish.

Metaphor map: house = the chat; one suitcase = the token budget;
passports = the exact codes; packing list = the compaction prompt;
moving company = the harness; the mover = the model.

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
    Text,
    Transform,
    VGroup,
    Write,
)

GOLD = YELLOW
MAXW = 12.5


def fit(m, w=MAXW):
    if m.width > w:
        m.scale_to_fit_width(w)
    return m


def caption(s, size=28):
    return fit(Text(s, font_size=size, line_spacing=1.1)).to_edge(DOWN, buff=0.45)


def furniture(n=9):
    items = VGroup(*[
        Rectangle(width=0.9, height=0.55, fill_color=GREY_D, fill_opacity=1, stroke_width=1)
        for _ in range(n)
    ])
    items.arrange_in_grid(rows=3, buff=0.25)
    return items


def passport():
    p = Rectangle(width=0.42, height=0.3, fill_color=GOLD, fill_opacity=1, stroke_width=1)
    label = Text("CODE", font_size=12).move_to(p)
    return VGroup(p, label)


class CliffScene(Scene):
    def show_caption(self, text_obj, hold=2.6):
        self.play(Write(text_obj))
        self.wait(hold)
        self.play(FadeOut(text_obj))

    def construct(self):
        # ---------- Act 1: the move ----------
        title = fit(Text("Compaction, explained with a house move", font_size=38))
        self.play(Write(title))
        self.wait(1.4)
        self.play(title.animate.to_edge(UP, buff=0.35).scale(0.6))

        house = Rectangle(width=4.6, height=3.4, stroke_color=GREY_B).shift(LEFT * 3.4)
        hlabel = fit(Text("your house = the whole chat", font_size=22)).next_to(house, UP, buff=0.15)
        stuff = furniture().move_to(house)
        pp = passport().move_to(stuff[4]).shift(UP * 0.02)
        case = Rectangle(width=1.7, height=1.1, stroke_color=GOLD).shift(RIGHT * 3.6 + DOWN * 0.2)
        clabel = fit(Text("one small suitcase = the budget", font_size=22)).next_to(case, UP, buff=0.15)
        self.play(FadeIn(house), FadeIn(hlabel), FadeIn(stuff), FadeIn(pp))
        self.play(FadeIn(case), FadeIn(clabel))
        self.show_caption(caption(
            "You must move TODAY, and only one suitcase comes with you.\nSomewhere in the house: your passport (a gold code you cannot replace)."
        ), hold=3.0)

        # ---------- Act 2: four packing lists ----------
        # 2a: burn it down (truncate)
        list1 = fit(Text('packing list 1: "take nothing"', font_size=24)).to_edge(UP, buff=1.05)
        self.play(Write(list1))
        self.play(FadeOut(stuff), FadeOut(pp), run_time=0.8)
        probe = fit(Text('At the border: "passport, please?"', font_size=22)).next_to(case, DOWN, buff=0.5)
        x1 = Cross(scale_factor=0.22).next_to(probe, DOWN, buff=0.15)
        self.play(Write(probe), FadeIn(x1))
        self.show_caption(caption("Deleting the old chat keeps nothing. Score: zero."), hold=2.2)
        self.play(FadeOut(x1))

        # 2b: pack whatever fits (naive)
        stuff2 = furniture().move_to(house)
        pp2 = passport().move_to(stuff2[4])
        self.play(FadeIn(stuff2), FadeIn(pp2), run_time=0.6)
        list2 = fit(Text('packing list 2: "pack whatever seems useful"', font_size=24)).to_edge(UP, buff=1.05)
        self.play(Transform(list1, list2))
        sofa = stuff2[0].copy()
        self.play(sofa.animate.scale(0.55).move_to(case), run_time=0.9)
        x2 = Cross(scale_factor=0.22).next_to(probe, DOWN, buff=0.15)
        self.play(FadeIn(x2))
        self.show_caption(caption(
            "A plain summary is this mover: it packs the sofa\nand leaves the passport on the shelf. Score: ~0%."
        ), hold=2.8)
        self.play(FadeOut(x2), FadeOut(sofa))

        # 2c: passports first (codes_first)
        list3 = fit(Text('packing list 3: "PASSPORTS FIRST, then whatever fits"', font_size=24)).to_edge(UP, buff=1.05)
        self.play(Transform(list1, list3))
        pp_moved = pp2.copy()
        self.play(pp_moved.animate.move_to(case), run_time=0.9)
        check = fit(Text("passport shown - welcome through", font_size=20)).set_color(GREEN_C).next_to(probe, DOWN, buff=0.15)
        self.play(FadeIn(check))
        self.show_caption(caption(
            "Same mover, same suitcase. Only the LIST changed.\nThe passport made it. That is the whole secret of compaction."
        ), hold=3.0)
        self.play(*[FadeOut(m) for m in (house, hlabel, stuff2, pp2, pp_moved, case, clabel, probe, check, list1)])

        # ---------- Act 3: moving companies compete ----------
        intro = fit(Text(
            "Every AI harness (Claude Code, DeepSeek...) is a MOVING COMPANY\nwith its own standard packing list. We made the lists compete.",
            font_size=26, line_spacing=1.15,
        ))
        self.play(Write(intro))
        self.wait(2.6)
        self.play(FadeOut(intro))

        entrants = [
            ("take nothing", 0.000, RED_C),
            ("whatever fits", 0.004, RED_C),
            ("keep papers (vague)", 0.155, GREY_B),
            ("Claude Code list", 0.410, GREEN_C),
            ("passports first (ours)", 0.435, GOLD),
            ("DeepSeek list", 0.448, GREEN_C),
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
        mtag = fit(Text("with an intern mover (weak model): only the list's INTENT matters", font_size=23)).to_edge(UP, buff=1.0)
        self.play(FadeIn(mtag))
        for b, lab in zip(bars, labels):
            self.play(GrowFromEdge(b, DOWN), FadeIn(lab), run_time=0.4)
        self.wait(2.0)

        new_tag = fit(Text("with a PRO mover (strong model): the list's ORDER matters too", font_size=23)).to_edge(UP, buff=1.0)
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
        self.show_caption(caption('"Passports first" wins by 11 points. Measured, not vibes.'), hold=2.6)
        self.play(*[FadeOut(m) for m in (bars, labels, mtag, title)])

        # ---------- Close: the map ----------
        mapping = fit(Text(
            "The map:\n"
            "house = the chat        suitcase = the token budget\n"
            "passport = an exact code        packing list = the compaction prompt\n"
            "moving company = the harness        mover = the model\n\n"
            "AgentLab tournament 001 - measured for $2.80",
            font_size=26, line_spacing=1.25,
        ))
        self.play(Write(mapping))
        self.wait(4.0)
