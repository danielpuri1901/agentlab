"""Manim scene template: title+claim -> mechanism build -> numbers -> caveat
-> question, timed to narration. This file is executed standalone by
`uvx --python 3.12 manim` (a separate environment from the main project,
which does not have manim installed), so it imports ONLY manim and the
stdlib, never `agentlab` itself.

It reads a plan+durations JSON path from the SCENE_SPEC_JSON env var
(written by agentlab.video_render.render_scene_video). The JSON is
{"plan": <ScenePlan.model_dump()>, "durations": [<seconds>, ...]}, with one
duration per entry of agentlab.video_render.scene_texts(plan): title+claim,
each mechanism step, numbers (only present if plan.key_numbers is
non-empty), caveat, question. Keep the segment count here in sync with
scene_texts if that ordering ever changes.

House style: dark background, one accent color (#2f6fd6; #F47D30 is
ArcelorMittal's brand and is forbidden here), step-by-step mechanism build
with arrows, every text block scaled to fit inside safe margins. The model
never writes animation code; a bad model day yields a boring video, never a
broken one.

Render directly for a quick look:
    SCENE_SPEC_JSON=/path/to/spec.json \
        uvx --python 3.12 manim render -ql src/agentlab/video_scenes.py PaperScene
"""

import json
import os
import textwrap
from pathlib import Path

from manim import (
    BOLD,
    DOWN,
    LEFT,
    ORIGIN,
    RIGHT,
    UP,
    WHITE,
    Arrow,
    Circle,
    FadeIn,
    FadeOut,
    Rectangle,
    Scene,
    Text,
    VGroup,
    Write,
)
from manim.utils.color import GREY_A, GREY_B, GREY_C

ACCENT = "#2f6fd6"  # keep in sync with agentlab.video_render.ACCENT
BACKGROUND = "#05070c"
SAFE_WIDTH = 12.4
SAFE_HEIGHT = 6.6

BUILD_CAP = 1.3
TRANSITION = 0.35


def _spec() -> dict:
    path = os.environ.get("SCENE_SPEC_JSON")
    if not path:
        raise RuntimeError(
            "SCENE_SPEC_JSON env var not set; video_scenes.py is meant to be "
            "run through agentlab.video_render.render_scene_video()"
        )
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wrap_text(s: str, width: int = 44) -> str:
    return "\n".join(textwrap.wrap(s, width)) or s


def fit(mobject, max_w: float = SAFE_WIDTH, max_h: float = SAFE_HEIGHT):
    """Shrink-to-fit: keep every text block inside the safe frame."""
    if mobject.width > max_w:
        mobject.scale_to_fit_width(max_w)
    if mobject.height > max_h:
        mobject.scale_to_fit_height(max_h)
    return mobject


class PaperScene(Scene):
    def beat(self, anims, duration: float, group, is_last: bool = False) -> None:
        """Reveal `anims`, hold, then (unless this is the final scene) fade
        `group` out. Always consumes exactly `duration` seconds, so the
        rendered video's total length equals sum(durations) exactly,
        matching the narration audio it will later be muxed against."""
        transition = 0.0 if is_last else TRANSITION
        budget = max(duration - transition, 0.05)
        build_time = min(BUILD_CAP, max(budget * 0.5, 0.3))
        build_time = min(build_time, budget)
        if anims:
            self.play(*anims, run_time=build_time)
        hold = duration - build_time - transition
        if hold > 0:
            self.wait(hold)
        if not is_last:
            self.play(FadeOut(group), run_time=transition)

    def construct(self):
        self.camera.background_color = BACKGROUND
        spec = _spec()
        plan = spec["plan"]
        durations = spec["durations"]

        steps = plan["mechanism_steps"]
        key_numbers = plan.get("key_numbers") or []
        has_numbers = bool(key_numbers)
        expected = 1 + len(steps) + (1 if has_numbers else 0) + 2
        if len(durations) != expected:
            raise ValueError(f"durations has {len(durations)} entries, scene needs {expected}")

        idx = 0

        def is_final(count_used: int) -> bool:
            return idx + count_used >= expected

        self._title_claim(plan, durations[idx], is_final(1))
        idx += 1

        self._mechanism(steps, durations[idx : idx + len(steps)], is_final(len(steps)))
        idx += len(steps)

        if has_numbers:
            self._numbers(key_numbers, durations[idx], is_final(1))
            idx += 1

        self._caveat(plan["limits_or_caveats"], durations[idx], is_final(1))
        idx += 1

        # Question is always the video's final segment (nothing to fade
        # out after it); _question hardcodes is_last=True itself.
        self._question(plan["street_test_question"], plan["citation_url"], durations[idx])

    # -- segments ----------------------------------------------------------

    def _title_claim(self, plan: dict, duration: float, is_last: bool) -> None:
        title = fit(Text(plan["title"], font_size=44, color=WHITE, weight=BOLD))
        title.to_edge(UP, buff=1.15)
        bar = Rectangle(
            width=1.7, height=0.05, color=ACCENT, fill_color=ACCENT, fill_opacity=1, stroke_width=0
        )
        bar.next_to(title, DOWN, buff=0.28)
        claim = fit(
            Text(
                wrap_text(plan["one_line_claim"], 50),
                font_size=30,
                color=GREY_A,
                line_spacing=1.3,
            ),
            max_h=3.0,
        )
        claim.next_to(bar, DOWN, buff=0.5)
        group = VGroup(title, bar, claim)
        self.beat([Write(title), FadeIn(bar), Write(claim)], duration, group, is_last)

    def _mechanism(self, steps: list[dict], durations: list[float], is_last: bool) -> None:
        n = len(steps)
        header = fit(Text("The mechanism, step by step", font_size=26, color=GREY_B))
        header.to_edge(UP, buff=0.5)
        self.add(header)

        rows = VGroup()
        for i, step in enumerate(steps):
            bubble = Circle(
                radius=0.26, color=ACCENT, fill_color=ACCENT, fill_opacity=1, stroke_width=0
            )
            num = Text(str(i + 1), font_size=20, color=WHITE)
            num.move_to(bubble)
            marker = VGroup(bubble, num)
            label = fit(Text(step["label"], font_size=24, color=WHITE, weight=BOLD), max_w=9.5)
            detail = fit(
                Text(wrap_text(step["detail"], 58), font_size=18, color=GREY_A, line_spacing=1.15),
                max_w=9.5,
            )
            text_block = VGroup(label, detail).arrange(DOWN, aligned_edge=LEFT, buff=0.08)
            row = VGroup(marker, text_block).arrange(RIGHT, buff=0.35)
            rows.add(row)
        rows.arrange(DOWN, aligned_edge=LEFT, buff=0.3)
        fit(rows, max_w=SAFE_WIDTH - 0.4, max_h=5.6)
        rows.move_to(ORIGIN).shift(DOWN * 0.25)

        arrows = []
        for i in range(n - 1):
            arrow = Arrow(
                start=rows[i][0].get_bottom(),
                end=rows[i + 1][0].get_top(),
                color=GREY_C,
                stroke_width=3,
                buff=0.06,
                max_tip_length_to_length_ratio=0.35,
            )
            arrows.append(arrow)

        # Each row consumes exactly its own duration slice (build + hold),
        # except the last row, which additionally reserves TRANSITION to
        # fade the whole list out -- unless this mechanism block is itself
        # the video's final segment, in which case nothing fades and the
        # list simply holds on screen through the end of its last duration.
        for i, row in enumerate(rows):
            duration = durations[i]
            last_step = i == n - 1
            reserve = TRANSITION if (last_step and not is_last) else 0.0
            budget = max(duration - reserve, 0.05)
            build_time = min(BUILD_CAP, max(budget * 0.55, 0.25))
            build_time = min(build_time, budget)
            anims = [FadeIn(row[0]), Write(row[1])]
            if i > 0:
                anims.insert(0, FadeIn(arrows[i - 1]))
                anims.append(rows[i - 1].animate.set_opacity(0.35))
            self.play(*anims, run_time=build_time)
            hold = duration - build_time - reserve
            if hold > 0:
                self.wait(hold)
            if last_step and reserve > 0:
                self.play(
                    FadeOut(rows), FadeOut(header), *[FadeOut(a) for a in arrows], run_time=reserve
                )

    def _numbers(self, key_numbers: list[dict], duration: float, is_last: bool) -> None:
        header = fit(Text("In numbers", font_size=28, color=GREY_B))
        header.to_edge(UP, buff=0.6)
        cards = VGroup()
        for kn in key_numbers:
            value = fit(Text(kn["value"], font_size=52, color=ACCENT, weight=BOLD), max_w=3.6)
            meaning = fit(
                Text(wrap_text(kn["meaning"], 22), font_size=18, color=GREY_A, line_spacing=1.15),
                max_w=3.6,
                max_h=1.8,
            )
            card = VGroup(value, meaning).arrange(DOWN, buff=0.25)
            cards.add(card)
        cards.arrange(RIGHT, buff=0.9, aligned_edge=UP)
        fit(cards, max_w=SAFE_WIDTH, max_h=4.4)
        cards.move_to(ORIGIN).shift(DOWN * 0.2)
        group = VGroup(header, cards)
        anims = [FadeIn(header)] + [FadeIn(c) for c in cards]
        self.beat(anims, duration, group, is_last)

    def _caveat(self, text: str, duration: float, is_last: bool) -> None:
        header = fit(Text("Limits", font_size=30, color=ACCENT, weight=BOLD))
        header.to_edge(UP, buff=1.3)
        body = fit(
            Text(wrap_text(text, 50), font_size=28, color=WHITE, line_spacing=1.35), max_h=3.5
        )
        body.next_to(header, DOWN, buff=0.6)
        group = VGroup(header, body)
        self.beat([Write(header), Write(body)], duration, group, is_last)

    def _question(self, question: str, citation_url: str, duration: float) -> None:
        header = fit(Text("Street test", font_size=30, color=ACCENT, weight=BOLD))
        header.to_edge(UP, buff=1.2)
        body = fit(
            Text(wrap_text(question, 46), font_size=32, color=WHITE, line_spacing=1.35), max_h=3.2
        )
        body.next_to(header, DOWN, buff=0.7)
        citation = fit(Text(citation_url, font_size=18, color=GREY_C))
        citation.to_edge(DOWN, buff=0.4)
        group = VGroup(header, body, citation)
        # Final segment: nothing after it, so no fade-out reserve.
        self.beat([Write(header), Write(body), FadeIn(citation)], duration, group, is_last=True)
