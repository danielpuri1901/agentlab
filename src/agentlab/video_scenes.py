"""Manim scene template: title+claim -> mechanism pipeline -> numbers ->
caveat -> question, timed to narration, captions burned in. This file is
executed standalone by `uvx --python 3.12 manim` (a separate environment
from the main project, which does not have manim installed), so it imports
ONLY manim and the stdlib, never `agentlab` itself.

It reads a plan+durations+captions JSON path from the SCENE_SPEC_JSON env
var (written by agentlab.video_render.render_scene_video). The JSON is
{"plan": <ScenePlan.model_dump()>, "durations": [<seconds>, ...],
"captions": [<text>, ...]}, both durations and captions with one entry per
agentlab.video_render.scene_texts(plan): title+claim, each mechanism step,
numbers (only present if plan.key_numbers is non-empty), caveat, question.
Keep the segment count here in sync with scene_texts if that ordering ever
changes. `captions` is scene_texts(plan) itself, passed straight through by
video_render.py so this file never has to re-derive narration text from the
plan structure -- it just draws whatever string it is given as a
bottom-of-frame caption for that segment's full duration (this is the
video's subtitle track: ffmpeg on the target machine has no libass, so
`-vf subtitles=...` isn't available and captions are burned in here
instead).

House style: dark background, one accent color (#2f6fd6; #F47D30 is
ArcelorMittal's brand and is forbidden here), every text block scaled to
fit inside safe margins. Mechanism steps render as a persistent
left-to-right pipeline of nodes; each step additionally plays a short
motif animation keyed by its `kind` (transform/gate/loop/split/store/
compare, see agentlab.scene_plan.MechanismStep) showing the mechanism
actually operating -- an item moving, morphing, branching, cycling,
accumulating, or being weighed -- rather than only text appearing (Daniel's
feedback 2026-08-23: show the mechanism operating, not text about it). The
model never writes animation code; a bad model day yields a boring video,
never a broken one.

Pure helper functions (wrap_text, caption_text, leading_number, fit) are
defined before the manim import and have no manim dependency, so they are
directly importable and testable from the main project's venv (which does
not have manim installed) via `from agentlab.video_scenes import ...`; the
import below is wrapped in try/except for exactly this reason. PaperScene
itself only exists when manim is actually importable.

Render directly for a quick look:
    SCENE_SPEC_JSON=/path/to/spec.json \
        uvx --python 3.12 manim render -ql src/agentlab/video_scenes.py PaperScene
"""

import json
import os
import re
import textwrap
from pathlib import Path

ACCENT = "#2f6fd6"  # keep in sync with agentlab.video_render.ACCENT
BACKGROUND = "#05070c"
SAFE_WIDTH = 12.4
SAFE_HEIGHT = 6.6

BUILD_CAP = 1.3
TRANSITION = 0.35

CAPTION_WRAP_WIDTH = 60
CAPTION_FONT_SIZE = 20

_LEADING_NUMBER_RE = re.compile(r"^-?[\d,]+(?:\.\d+)?")


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


def caption_text(s: str, width: int = CAPTION_WRAP_WIDTH) -> str:
    """Wrap a narration sentence for the bottom-of-frame caption bar. Pure
    (no manim dependency), extracted for direct testing without manim
    installed -- see the module docstring."""
    return wrap_text(s, width)


def leading_number(value: str) -> tuple[float, str] | None:
    """Split a key-number value into (magnitude, trailing suffix), e.g.
    "1250" -> (1250.0, ""), "74%" -> (74.0, "%"). None if `value` doesn't
    start with a number. Pure; used to decide whether to count a stat up
    and to size its comparison bar."""
    match = _LEADING_NUMBER_RE.match(value)
    if not match:
        return None
    numeric = match.group()
    return float(numeric.replace(",", "")), value[len(numeric) :]


def fit(mobject, max_w: float = SAFE_WIDTH, max_h: float = SAFE_HEIGHT):
    """Shrink-to-fit: keep every text block inside the safe frame. Works on
    any object with manim's Mobject sizing API (duck-typed, so this needs
    no manim import itself)."""
    if mobject.width > max_w:
        mobject.scale_to_fit_width(max_w)
    if mobject.height > max_h:
        mobject.scale_to_fit_height(max_h)
    return mobject


try:
    from manim import (
        BOLD,
        DOWN,
        LEFT,
        ORIGIN,
        PI,
        RIGHT,
        TAU,
        UP,
        WHITE,
        Arrow,
        Circle,
        Dot,
        FadeIn,
        FadeOut,
        Line,
        Rectangle,
        Rotate,
        RoundedRectangle,
        Scene,
        Square,
        Text,
        Transform,
        Triangle,
        ValueTracker,
        VGroup,
        Write,
        always_redraw,
        linear,
    )
    from manim.utils.color import GREY_A, GREY_B, GREY_C, GREY_D

    _MANIM_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit without manim installed
    _MANIM_AVAILABLE = False


if _MANIM_AVAILABLE:

    class PaperScene(Scene):
        def beat(self, anims, duration: float, group, is_last: bool = False) -> None:
            """Reveal `anims`, hold, then (unless this is the final scene)
            fade `group` out. Always consumes exactly `duration` seconds,
            so the rendered video's total length equals sum(durations)
            exactly, matching the narration audio it is muxed against."""
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

        def caption_mobject(self, text: str):
            cap = fit(
                Text(caption_text(text), font_size=CAPTION_FONT_SIZE, color=WHITE, line_spacing=1.15),
                max_w=SAFE_WIDTH,
                max_h=1.5,
            )
            cap.to_edge(DOWN, buff=0.3)
            return cap

        def construct(self):
            self.camera.background_color = BACKGROUND
            spec = _spec()
            plan = spec["plan"]
            durations = spec["durations"]
            captions = spec["captions"]

            steps = plan["mechanism_steps"]
            key_numbers = plan.get("key_numbers") or []
            has_numbers = bool(key_numbers)
            expected = 1 + len(steps) + (1 if has_numbers else 0) + 2
            if len(durations) != expected:
                raise ValueError(f"durations has {len(durations)} entries, scene needs {expected}")
            if len(captions) != expected:
                raise ValueError(f"captions has {len(captions)} entries, scene needs {expected}")

            idx = 0

            def is_final(count_used: int) -> bool:
                return idx + count_used >= expected

            self._title_claim(plan, durations[idx], captions[idx], is_final(1))
            idx += 1

            self._mechanism(
                steps,
                durations[idx : idx + len(steps)],
                captions[idx : idx + len(steps)],
                is_final(len(steps)),
            )
            idx += len(steps)

            if has_numbers:
                self._numbers(key_numbers, durations[idx], captions[idx], is_final(1))
                idx += 1

            self._caveat(plan["limits_or_caveats"], durations[idx], captions[idx], is_final(1))
            idx += 1

            # Question is always the video's final segment (nothing to fade
            # out after it); _question hardcodes is_last=True itself.
            self._question(
                plan["street_test_question"], plan["citation_url"], durations[idx], captions[idx]
            )

        # -- static segments -------------------------------------------------

        def _title_claim(self, plan: dict, duration: float, caption_str: str, is_last: bool) -> None:
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
                max_h=2.6,
            )
            claim.next_to(bar, DOWN, buff=0.5)
            caption = self.caption_mobject(caption_str)
            group = VGroup(title, bar, claim, caption)
            self.beat([Write(title), FadeIn(bar), Write(claim), FadeIn(caption)], duration, group, is_last)

        def _caveat(self, text: str, duration: float, caption_str: str, is_last: bool) -> None:
            header = fit(Text("Limits", font_size=30, color=ACCENT, weight=BOLD))
            header.to_edge(UP, buff=1.3)
            body = fit(
                Text(wrap_text(text, 50), font_size=28, color=WHITE, line_spacing=1.35), max_h=2.6
            )
            body.next_to(header, DOWN, buff=0.6)
            caption = self.caption_mobject(caption_str)
            group = VGroup(header, body, caption)
            self.beat([Write(header), Write(body), FadeIn(caption)], duration, group, is_last)

        def _question(
            self, question: str, citation_url: str, duration: float, caption_str: str
        ) -> None:
            header = fit(Text("Street test", font_size=30, color=ACCENT, weight=BOLD))
            header.to_edge(UP, buff=1.2)
            body = fit(
                Text(wrap_text(question, 46), font_size=32, color=WHITE, line_spacing=1.35), max_h=2.4
            )
            body.next_to(header, DOWN, buff=0.6)
            caption = self.caption_mobject(caption_str)
            citation = fit(Text(citation_url, font_size=16, color=GREY_C))
            citation.next_to(caption, UP, buff=0.25)
            group = VGroup(header, body, caption, citation)
            # Final segment: nothing after it, so no fade-out reserve.
            self.beat(
                [Write(header), Write(body), FadeIn(caption), FadeIn(citation)],
                duration,
                group,
                is_last=True,
            )

        # -- mechanism: persistent pipeline + per-step motif ------------------

        def _mechanism(
            self, steps: list[dict], durations: list[float], mech_captions: list[str], is_last: bool
        ) -> None:
            n = len(steps)
            header = fit(Text("The mechanism, step by step", font_size=26, color=GREY_B))
            header.to_edge(UP, buff=0.5)

            nodes = VGroup()
            for step in steps:
                box = RoundedRectangle(
                    width=1.95,
                    height=0.8,
                    corner_radius=0.12,
                    stroke_color=GREY_C,
                    stroke_width=2,
                    fill_color=BACKGROUND,
                    fill_opacity=1,
                )
                label = fit(
                    Text(step["label"], font_size=17, color=GREY_C, weight=BOLD), max_w=1.65, max_h=0.55
                )
                label.move_to(box)
                nodes.add(VGroup(box, label))
            nodes.arrange(RIGHT, buff=0.5)
            fit(nodes, max_w=SAFE_WIDTH - 0.3)
            nodes.move_to(ORIGIN).shift(UP * 1.5)

            connectors = VGroup()
            for i in range(n - 1):
                connectors.add(
                    Arrow(
                        nodes[i].get_right(),
                        nodes[i + 1].get_left(),
                        color=GREY_D,
                        stroke_width=2,
                        buff=0.05,
                        max_tip_length_to_length_ratio=0.5,
                    )
                )
            self.add(header, nodes, connectors)

            current_caption = None
            for i, node in enumerate(nodes):
                duration = durations[i]
                last_step = i == n - 1
                reserve = TRANSITION if (last_step and not is_last) else 0.0
                budget = max(duration - reserve, 0.05)

                new_caption = self.caption_mobject(mech_captions[i])
                swap_anims = [FadeIn(new_caption)]
                if current_caption is not None:
                    swap_anims.append(FadeOut(current_caption))

                highlight_anims = [
                    node[0].animate.set_stroke(ACCENT, width=3).set_fill(ACCENT, opacity=0.16),
                    node[1].animate.set_color(WHITE),
                ]
                if i > 0:
                    prev = nodes[i - 1]
                    highlight_anims += [
                        prev[0].animate.set_stroke(GREY_D, width=2).set_fill(BACKGROUND, opacity=1),
                        prev[1].animate.set_color(GREY_D),
                    ]

                highlight_time = min(0.4, budget)
                self.play(*highlight_anims, *swap_anims, run_time=highlight_time)
                current_caption = new_caption

                remaining = budget - highlight_time
                motif_time = min(3.4, max(remaining * 0.75, 0.4))
                motif_time = min(motif_time, remaining)
                self._play_motif(steps[i].get("kind", "transform"), node[0].get_center(), motif_time)

                hold = duration - highlight_time - motif_time - reserve
                if hold > 0:
                    self.wait(hold)

                if last_step and reserve > 0:
                    self.play(
                        FadeOut(nodes),
                        FadeOut(connectors),
                        FadeOut(header),
                        FadeOut(current_caption),
                        run_time=reserve,
                    )

        def _play_motif(self, kind: str, pos, run_time: float) -> None:
            motifs = {
                "transform": self._motif_transform,
                "gate": self._motif_gate,
                "loop": self._motif_loop,
                "split": self._motif_split,
                "store": self._motif_store,
                "compare": self._motif_compare,
            }
            motifs.get(kind, self._motif_transform)(pos, run_time)

        def _motif_transform(self, pos, run_time: float) -> None:
            """Input becomes output: an item enters the node, changes shape
            and color while inside, and exits toward the next node."""
            t1, t2, t3 = run_time * 0.28, run_time * 0.24, run_time * 0.24
            t4 = run_time - t1 - t2 - t3
            item = Square(0.16, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            item.move_to(pos + LEFT * 0.85)
            self.play(FadeIn(item), run_time=t1)
            self.play(item.animate.move_to(pos), run_time=t2)
            morphed = Circle(radius=0.11, fill_color=ACCENT, fill_opacity=1, stroke_width=0).move_to(pos)
            self.play(Transform(item, morphed), run_time=t3)
            self.play(item.animate.move_to(pos + RIGHT * 0.85).set_opacity(0), run_time=t4)

        def _motif_gate(self, pos, run_time: float) -> None:
            """Something is accepted or rejected: an item hits a diamond,
            a pass copy continues on bright, a fail copy drops away dim."""
            t1, t2 = run_time * 0.24, run_time * 0.2
            t3 = run_time - t1 - t2
            item = Square(0.16, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            item.move_to(pos + LEFT * 0.85)
            diamond = Square(0.3, fill_opacity=0, stroke_color=ACCENT, stroke_width=2)
            diamond.rotate(PI / 4).move_to(pos)
            self.play(FadeIn(item), FadeIn(diamond), run_time=t1)
            self.play(item.animate.move_to(pos), run_time=t2)
            passed = item.copy().set_fill(ACCENT, opacity=1)
            failed = item.copy().set_fill(GREY_D, opacity=1)
            self.play(
                FadeOut(item),
                FadeOut(diamond),
                passed.animate.move_to(pos + RIGHT * 0.85),
                failed.animate.move_to(pos + DOWN * 0.7).set_opacity(0.15),
                run_time=t3 * 0.75,
            )
            self.play(FadeOut(passed), FadeOut(failed), run_time=max(t3 * 0.25, 0.05))

        def _motif_loop(self, pos, run_time: float) -> None:
            """A cycle repeats: an item orbits the node twice along a
            drawn ring before clearing."""
            t_setup = run_time * 0.16
            t_cleanup = run_time * 0.14
            t_loop = run_time - t_setup - t_cleanup
            pivot = pos + UP * 0.62
            ring = Circle(radius=0.4, stroke_color=GREY_C, stroke_width=2).move_to(pivot)
            item = Dot(radius=0.09, color=ACCENT).move_to(pivot + DOWN * 0.4)
            self.play(FadeIn(ring), FadeIn(item), run_time=t_setup)
            self.play(Rotate(item, angle=2 * TAU, about_point=pivot), run_time=t_loop, rate_func=linear)
            self.play(FadeOut(ring), FadeOut(item), run_time=t_cleanup)

        def _motif_split(self, pos, run_time: float) -> None:
            """One path becomes several: an item duplicates and the copies
            fan out before clearing."""
            t1 = run_time * 0.22
            t3 = run_time * 0.18
            t2 = run_time - t1 - t3
            item = Square(0.16, fill_color=ACCENT, fill_opacity=1, stroke_width=0).move_to(pos)
            self.play(FadeIn(item), run_time=t1)
            targets = [
                pos + LEFT * 0.7 + DOWN * 0.55,
                pos + DOWN * 0.78,
                pos + RIGHT * 0.7 + DOWN * 0.55,
            ]
            copies = VGroup(*[item.copy().set_opacity(0).move_to(pos) for _ in targets])
            self.play(
                FadeOut(item),
                *[FadeIn(c) for c in copies],
                *[c.animate.move_to(t) for c, t in zip(copies, targets, strict=True)],
                run_time=t2,
            )
            self.play(FadeOut(copies), run_time=t3)

        def _motif_store(self, pos, run_time: float) -> None:
            """Something is saved for later: an item slides down and
            becomes a new layer on a small stack, which persists."""
            t1, t2 = run_time * 0.28, run_time * 0.3
            t3 = run_time - t1 - t2
            base = pos + DOWN * 0.55
            layer_w, layer_h = 0.55, 0.14
            existing = VGroup(
                *[
                    Rectangle(
                        width=layer_w, height=layer_h, fill_color=GREY_D, fill_opacity=1, stroke_width=0
                    ).move_to(base + DOWN * 0.2 * k)
                    for k in range(2)
                ]
            )
            item = Square(0.15, fill_color=ACCENT, fill_opacity=1, stroke_width=0).move_to(pos)
            self.play(FadeIn(existing), FadeIn(item), run_time=t1)
            new_layer = Rectangle(
                width=layer_w, height=layer_h, fill_color=ACCENT, fill_opacity=1, stroke_width=0
            ).move_to(base + UP * 0.2)
            self.play(Transform(item, new_layer), run_time=t2)
            self.play(item.animate.set_fill(GREY_B), run_time=t3)
            # Deliberately not faded: the stack is the visual record that
            # this step accumulates something, and stays on screen.

        def _motif_compare(self, pos, run_time: float) -> None:
            """Two things are measured against each other: two items rise
            onto a small balance, one is highlighted as the outcome."""
            t1 = run_time * 0.28
            t2 = run_time * 0.32
            t3 = run_time - t1 - t2
            left_start = pos + LEFT * 0.55 + DOWN * 0.85
            right_start = pos + RIGHT * 0.55 + DOWN * 0.85
            left_item = Circle(radius=0.13, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            left_item.move_to(left_start)
            right_item = Circle(radius=0.13, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            right_item.move_to(right_start)
            beam = Line(
                pos + LEFT * 0.65 + DOWN * 0.4, pos + RIGHT * 0.65 + DOWN * 0.4, stroke_color=GREY_C, stroke_width=2
            )
            pivot = Triangle(fill_color=GREY_C, fill_opacity=1, stroke_width=0).scale(0.08)
            pivot.move_to(pos + DOWN * 0.55)
            self.play(FadeIn(left_item), FadeIn(right_item), FadeIn(beam), FadeIn(pivot), run_time=t1)
            self.play(
                left_item.animate.move_to(pos + LEFT * 0.65 + DOWN * 0.3),
                right_item.animate.move_to(pos + RIGHT * 0.65 + DOWN * 0.3),
                run_time=t2,
            )
            self.play(
                right_item.animate.set_fill(ACCENT).scale(1.35),
                left_item.animate.set_opacity(0.3),
                run_time=t3 * 0.6,
            )
            self.play(
                FadeOut(left_item), FadeOut(right_item), FadeOut(beam), FadeOut(pivot), run_time=t3 * 0.4
            )

        # -- numbers: bars grow, values count up ------------------------------

        def _numbers(
            self, key_numbers: list[dict], duration: float, caption_str: str, is_last: bool
        ) -> None:
            header = fit(Text("In numbers", font_size=28, color=GREY_B))
            header.to_edge(UP, buff=0.6)
            caption = self.caption_mobject(caption_str)

            parsed = [leading_number(kn["value"]) for kn in key_numbers]
            magnitudes = [p[0] if p else 1.0 for p in parsed]
            max_mag = max(magnitudes) if magnitudes else 1.0
            bar_max_h = 1.5

            stacks = VGroup()
            entries = []
            for kn, mag, p in zip(key_numbers, magnitudes, parsed, strict=True):
                target_h = max(0.15, bar_max_h * (mag / max_mag))
                bar = Rectangle(width=0.62, height=target_h, fill_color=ACCENT, fill_opacity=0.85, stroke_width=0)
                if p is not None:
                    numeric, suffix = p
                    value_mob = Text(f"{int(numeric)}{suffix}", font_size=44, color=ACCENT, weight=BOLD)
                else:
                    value_mob = fit(Text(kn["value"], font_size=44, color=ACCENT, weight=BOLD), max_w=3.0)
                meaning = fit(
                    Text(wrap_text(kn["meaning"], 22), font_size=16, color=GREY_A, line_spacing=1.15),
                    max_w=3.0,
                    max_h=1.4,
                )
                stack = VGroup(bar, value_mob, meaning).arrange(DOWN, buff=0.22)
                stacks.add(stack)
                entries.append(
                    {"stack": stack, "bar": bar, "target_h": target_h, "value_mob": value_mob, "meaning": meaning, "parsed": p}
                )

            stacks.arrange(RIGHT, buff=0.85, aligned_edge=DOWN)
            fit(stacks, max_w=SAFE_WIDTH, max_h=4.2)
            stacks.move_to(ORIGIN).shift(UP * 0.1)

            reveal_anims = [FadeIn(header), FadeIn(caption)]
            tracker_anims = []
            live_mobjects = []
            for e in entries:
                reveal_anims.append(FadeIn(e["meaning"]))
                e["bar"].stretch_to_fit_height(0.04, about_edge=DOWN)
                if e["parsed"] is not None:
                    numeric, suffix = e["parsed"]
                    anchor = e["value_mob"].get_center().copy()
                    tracker = ValueTracker(0.0)
                    live = always_redraw(
                        lambda t=tracker, suf=suffix, a=anchor: Text(
                            f"{int(t.get_value())}{suf}", font_size=44, color=ACCENT, weight=BOLD
                        ).move_to(a)
                    )
                    e["stack"].remove(e["value_mob"])
                    e["stack"].add(live)
                    e["live"] = live
                    e["anchor"] = anchor
                    live_mobjects.append(live)
                    tracker_anims.append(tracker.animate.set_value(numeric))
                else:
                    e["live"] = None
                    reveal_anims.append(FadeIn(e["value_mob"]))

            grow_anims = [e["bar"].animate.stretch_to_fit_height(e["target_h"], about_edge=DOWN) for e in entries]

            group = VGroup(header, stacks, caption)
            transition = 0.0 if is_last else TRANSITION
            budget = max(duration - transition, 0.05)
            build_time = min(2.0, max(budget * 0.65, 0.5))
            build_time = min(build_time, budget)

            # always_redraw mobjects must be added to the scene directly,
            # not through an Animation targeting them (a FadeIn/Transform on
            # a continuously-regenerated mobject hits the same family-size
            # mismatch the freeze step below works around). Adding them here
            # means each starts visible at "0" and the tracker.animate below
            # ticks it up in the same beat as everything else revealing.
            self.add(*live_mobjects)
            self.play(*reveal_anims, *grow_anims, *tracker_anims, run_time=build_time)
            hold = duration - build_time - transition
            if hold > 0:
                self.wait(hold)

            # Freeze every live (always_redraw) counter into a plain static
            # Text before any further group-level animation touches it: an
            # always_redraw mobject mixed into a later FadeOut/Transform of
            # its parent group hits a manim bug (mismatched family sizes
            # between the animation's starting snapshot and the live,
            # continuously-regenerated mobject -- "zip() argument 2 is
            # shorter than argument 1"). Freezing removes the updater so
            # the mobject tree is stable for anything that follows.
            for e in entries:
                if e["live"] is None:
                    continue
                numeric, suffix = e["parsed"]
                e["live"].clear_updaters()
                frozen = Text(f"{int(numeric)}{suffix}", font_size=44, color=ACCENT, weight=BOLD)
                frozen.move_to(e["anchor"])
                e["stack"].remove(e["live"])
                e["stack"].add(frozen)

            if not is_last:
                self.play(FadeOut(group), run_time=transition)
