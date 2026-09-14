"""Manim scene template: title+claim -> mechanism diagram, animated step by
step -> numbers -> caveat -> question, timed to narration, captions burned
in. This file is executed standalone by `uvx --python 3.12 manim` (a
separate environment from the main project, which does not have manim
installed), so it imports ONLY manim and the stdlib, never `agentlab`
itself.

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

CONTENT-SPECIFIC VISUALS (Daniel's ruling 2026-08-25, after the first live
videos all looked like the same abstract stage pipeline): plan["diagram"]
is THIS paper's own mechanism as data -- nodes (id, label, optional emoji
icon) and edges (source, target, optional label) -- and each mechanism
step's `activates` names the exact node ids and edge ids (edge ids are
"source->target") that step is about. The mechanism section renders that
diagram once, laid out left-to-right by topological order (grid layout if
the edges are not a DAG), then per step dims everything else and brightens
only the activated elements while that step's motif (kinds transform/gate/
loop/split/store/compare, see agentlab.scene_plan.MechanismStep) plays on
top of them -- a traveling dot along an activated edge, the existing
per-kind motif at an activated node. The model never writes animation
code or layout coordinates, only which of ITS OWN diagram's parts each
step is about; a bad model day yields a boring video, never a broken one.

Emoji icons: verified locally (macOS, this Pango/cairo/manimpango stack)
that an emoji character never raises when handed to Text() -- it silently
comes back as a mobject with zero points instead, tofu-free but also
glyph-free. `_make_icon` below detects that (not an exception) and falls
back to a plain accent-colored dot, so a missing glyph can never break a
render or leave a node looking broken. The container adds
fonts-noto-color-emoji (Dockerfile.video) so the deployed render has a
real chance at the glyph; the fallback exists regardless, because "prints
successfully" and "drew something" turned out not to be the same claim.
Per Daniel's ruling: the icon is always a restrained accent inside a
well-designed node, small and consistently sized, never the node's whole
visual by itself -- so the fallback dot degrades the accent, not the node.

House style: dark background, one accent color (#2f6fd6; #F47D30 is
ArcelorMittal's brand and is forbidden here), every text block scaled to
fit inside safe margins, real Manim vocabulary (RoundedRectangle nodes,
Arrow/CurvedArrow edges with proper tips, Create/FadeTransform for state
changes, Circumscribe for activation, MoveAlongPath for the traveling dot,
explicit rate functions) rather than decorated text boxes with flat color
swaps. Judge every frame against results/tournaments/001/
compaction-mechanism-v2.mp4 (motion-quality bar, see this task's report).

Pure helper functions (wrap_text, caption_text, leading_number, fit,
topological_order, grid_dimensions) are defined before the manim import
and have no manim dependency, so they are directly importable and testable
from the main project's venv (which does not have manim installed) via
`from agentlab.video_scenes import ...`; the import below is wrapped in
try/except for exactly this reason. PaperScene itself only exists when
manim is actually importable.

Render directly for a quick look:
    SCENE_SPEC_JSON=/path/to/spec.json \
        uvx --python 3.12 manim render -ql src/agentlab/video_scenes.py PaperScene
"""

import json
import math
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

# Diagram layout. NODE spacing at 6 nodes (the old pipeline's max width)
# is the baseline the six _motif_* methods below were hand-tuned against;
# _build_diagram computes a scale factor off this so a denser diagram (up
# to 8 nodes, or a grid fallback) never lets a motif's travel distance
# overshoot into a neighboring, now-smaller-gapped node.
DIAGRAM_MAX_W = SAFE_WIDTH - 0.4
DIAGRAM_MAX_H = 3.5
NODE_W = 1.9
NODE_H = 1.05
BASELINE_NODE_GAP = NODE_W + 0.55

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


def topological_order(node_ids: list[str], edges: list[tuple[str, str]]) -> list[str] | None:
    """Kahn's algorithm: a left-to-right node order consistent with every
    edge's direction (each edge's source appears before its target), or
    None if the edges contain a cycle -- the diagram falls back to a grid
    layout in that case. Deterministic: ties break in `node_ids` order.
    Pure (no manim dependency), directly testable."""
    indegree = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    for src, dst in edges:
        if src in adjacency and dst in indegree:
            adjacency[src].append(dst)
            indegree[dst] += 1
    queue = [nid for nid in node_ids if indegree[nid] == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for nxt in adjacency[nid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    return order if len(order) == len(node_ids) else None


def layered_columns(
    node_ids: list[str], edges: list[tuple[str, str]]
) -> list[list[str]] | None:
    """Group a DAG by longest-path depth so branches use vertical space."""
    order = topological_order(node_ids, edges)
    if order is None:
        return None
    known = set(node_ids)
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for source, target in edges:
        if source in known and target in known:
            outgoing[source].append(target)
    depth = {node_id: 0 for node_id in node_ids}
    for source in order:
        for target in outgoing[source]:
            depth[target] = max(depth[target], depth[source] + 1)
    columns = [[] for _ in range(max(depth.values(), default=0) + 1)]
    for node_id in order:
        columns[depth[node_id]].append(node_id)
    return columns


def primary_edge_id(activates: list[str], edge_ids: set[str]) -> str | None:
    """Return at most one active edge label, preserving mechanism order."""
    return next((item for item in activates if item in edge_ids), None)


def grid_dimensions(n: int) -> tuple[int, int]:
    """(columns, rows) for a roughly square grid holding n items -- the
    cyclic-diagram fallback layout. Pure, directly testable."""
    if n <= 0:
        return (0, 0)
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return cols, rows


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
        Circumscribe,
        Create,
        CurvedArrow,
        Dot,
        FadeIn,
        FadeOut,
        FadeTransform,
        Line,
        MoveAlongPath,
        Rectangle,
        Rotate,
        RoundedRectangle,
        Scene,
        Square,
        Text,
        Triangle,
        ValueTracker,
        VGroup,
        Write,
        always_redraw,
        linear,
        smooth,
    )
    from manim.utils.color import GREY_A, GREY_B, GREY_C, GREY_D
    from manim.utils.rate_functions import ease_in_out_sine

    _MANIM_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit without manim installed
    _MANIM_AVAILABLE = False


if _MANIM_AVAILABLE:

    def _make_icon(icon: str | None, color, size: float = 0.24):
        """A small, restrained icon badge for a diagram node: the real
        emoji glyph if this render environment's font stack can actually
        rasterize it, else a plain accent-colored dot -- never the node's
        whole visual (see module docstring). A glyph the current font
        can't draw comes back from Text() as a mobject with zero points
        rather than raising (verified locally), so detection checks the
        rendered result, not just an exception."""
        if icon:
            try:
                glyph = Text(icon, font_size=int(size * 150), color=WHITE)
                if glyph.width > 1e-6 and len(glyph.family_members_with_points()) > 0:
                    return fit(glyph, max_w=size * 1.7, max_h=size * 1.7)
            except Exception:  # noqa: BLE001, S110 - a render must never die on a font glyph
                pass
        return Dot(radius=size * 0.5, color=color, fill_opacity=1, stroke_width=0)

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
                self.play(*anims, run_time=build_time, rate_func=smooth)
            hold = duration - build_time - transition
            if hold > 0:
                self.wait(hold)
            if not is_last:
                self.play(FadeOut(group), run_time=transition, rate_func=smooth)

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
                plan["diagram"],
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

        # -- mechanism: this paper's own diagram + per-step motif ------------

        def _build_diagram(self, diagram: dict) -> None:
            """Build THIS paper's mechanism diagram once (nodes laid out
            left-to-right by topological order, grid fallback on a cycle);
            `_mechanism` then dims/brightens and plays motifs on top of it,
            step by step. Stores node/edge mobjects on self, keyed by id
            (node ids as given, edge ids as "source->target"), plus a
            motif_scale factor so the hand-tuned _motif_* travel distances
            never overshoot a neighboring node once a dense diagram gets
            shrunk to fit."""
            node_specs = diagram["nodes"]
            edge_specs = diagram["edges"]
            node_ids = [n["id"] for n in node_specs]
            edge_pairs = [(e["source"], e["target"]) for e in edge_specs]

            columns = layered_columns(node_ids, edge_pairs)
            acyclic = columns is not None
            columns = columns or []
            order = [nid for column in columns for nid in column] if acyclic else node_ids
            column_of = {
                nid: column_index
                for column_index, column in enumerate(columns)
                for nid in column
            }

            node_groups: dict[str, VGroup] = {}
            boxes: dict[str, RoundedRectangle] = {}
            by_id = {n["id"]: n for n in node_specs}
            for nid in order:
                spec = by_id[nid]
                box = RoundedRectangle(
                    width=NODE_W,
                    height=NODE_H,
                    corner_radius=0.14,
                    stroke_color=GREY_C,
                    stroke_width=2,
                    fill_color=BACKGROUND,
                    fill_opacity=1,
                )
                icon = _make_icon(spec.get("icon"), GREY_B)
                icon.move_to(box.get_top() + DOWN * 0.3)
                label = fit(
                    Text(spec["label"], font_size=15, color=GREY_B, weight=BOLD),
                    max_w=NODE_W - 0.2,
                    max_h=0.34,
                )
                label.move_to(box.get_bottom() + UP * 0.27)
                group = VGroup(box, icon, label)
                node_groups[nid] = group
                boxes[nid] = box

            if acyclic:
                column_groups = VGroup(
                    *[
                        VGroup(*[node_groups[nid] for nid in column]).arrange(
                            DOWN, buff=0.45
                        )
                        for column in columns
                    ]
                )
                column_groups.arrange(RIGHT, buff=0.75)
                placed = column_groups
            else:
                placed = VGroup(*[node_groups[nid] for nid in order])
                cols, _rows = grid_dimensions(len(order))
                placed.arrange_in_grid(cols=cols, buff=0.55)
            fit(placed, max_w=DIAGRAM_MAX_W, max_h=DIAGRAM_MAX_H)
            placed.move_to(ORIGIN).shift(UP * 1.2)

            gaps = [
                abs((boxes[order[i + 1]].get_center() - boxes[order[i]].get_center())[0])
                for i in range(len(order) - 1)
            ]
            min_gap = min((g for g in gaps if g > 1e-6), default=BASELINE_NODE_GAP)
            self._motif_scale = min(1.0, max(0.35, min_gap / BASELINE_NODE_GAP))

            edge_mobs: dict[str, VGroup] = {}
            for spec in edge_specs:
                src_id, dst_id = spec["source"], spec["target"]
                if src_id == dst_id or src_id not in boxes or dst_id not in boxes:
                    continue  # defensive: schema already forbids this, never crash a render on it
                eid = f"{src_id}->{dst_id}"
                src_box, dst_box = boxes[src_id], boxes[dst_id]
                direction = dst_box.get_center() - src_box.get_center()
                start = src_box.get_boundary_point(direction)
                end = dst_box.get_boundary_point(-direction)
                skips = acyclic and abs(column_of[dst_id] - column_of[src_id]) != 1
                if skips:
                    arrow = CurvedArrow(start, end, angle=PI / 3.5, color=GREY_D, stroke_width=2)
                else:
                    arrow = Arrow(
                        start,
                        end,
                        color=GREY_D,
                        stroke_width=2,
                        buff=0.0,
                        max_tip_length_to_length_ratio=0.4,
                    )
                parts = [arrow]
                if spec.get("label"):
                    tag = fit(Text(spec["label"], font_size=13, color=GREY_D), max_w=1.7, max_h=0.3)
                    tag.move_to(arrow.point_from_proportion(0.5) + UP * 0.16)
                    tag.set_opacity(0)
                    parts.append(tag)
                edge_mobs[eid] = VGroup(*parts)

            self._diagram_nodes = node_groups
            self._diagram_edges = edge_mobs
            self._diagram_group = VGroup(*edge_mobs.values(), placed)

        def _mechanism(
            self,
            steps: list[dict],
            durations: list[float],
            mech_captions: list[str],
            is_last: bool,
            diagram: dict,
        ) -> None:
            n = len(steps)
            header = fit(Text("The mechanism, step by step", font_size=26, color=GREY_B))
            header.to_edge(UP, buff=0.5)

            self._build_diagram(diagram)
            self.add(header, self._diagram_group)
            known_ids = set(self._diagram_nodes) | set(self._diagram_edges)
            active: set[str] = set()

            current_caption = None
            for i, step in enumerate(steps):
                duration = durations[i]
                last_step = i == n - 1
                reserve = TRANSITION if (last_step and not is_last) else 0.0
                budget = max(duration - reserve, 0.05)

                new_caption = self.caption_mobject(mech_captions[i])
                highlight_anims = [FadeIn(new_caption)]
                if current_caption is not None:
                    highlight_anims.append(FadeOut(current_caption))

                activated = step.get("activates") or []
                wanted = set(activated) & known_ids
                newly = wanted - active
                released = active - wanted
                flourishes = []

                for nid in newly & set(self._diagram_nodes):
                    box, _icon, label = self._diagram_nodes[nid]
                    highlight_anims.append(box.animate.set_stroke(ACCENT, width=3).set_fill(ACCENT, opacity=0.14))
                    highlight_anims.append(label.animate.set_color(WHITE))
                    flourishes.append(Circumscribe(box, color=ACCENT, buff=0.08, stroke_width=3, time_width=0.4))
                for eid in newly & set(self._diagram_edges):
                    edge = self._diagram_edges[eid]
                    arrow = edge[0]
                    highlight_anims.append(arrow.animate.set_stroke(ACCENT, width=3))
                for nid in released & set(self._diagram_nodes):
                    box, _icon, label = self._diagram_nodes[nid]
                    highlight_anims.append(box.animate.set_stroke(GREY_C, width=2).set_fill(BACKGROUND, opacity=1))
                    highlight_anims.append(label.animate.set_color(GREY_B))
                for eid in released & set(self._diagram_edges):
                    edge = self._diagram_edges[eid]
                    arrow = edge[0]
                    highlight_anims.append(arrow.animate.set_stroke(GREY_D, width=2))
                label_edge = primary_edge_id(activated, set(self._diagram_edges))
                for eid, edge in self._diagram_edges.items():
                    if len(edge) > 1:
                        opacity = 1 if eid == label_edge else 0
                        highlight_anims.append(
                            edge[1].animate.set_color(WHITE).set_opacity(opacity)
                        )

                highlight_time = min(0.4, budget)
                self.play(*highlight_anims, run_time=highlight_time, rate_func=smooth)
                current_caption = new_caption
                active = wanted

                flourish_time = 0.0
                if flourishes:
                    flourish_time = min(0.35, max(budget - highlight_time, 0.0))
                    if flourish_time > 0.05:
                        self.play(*flourishes, run_time=flourish_time)
                    else:
                        flourish_time = 0.0

                used = highlight_time + flourish_time
                remaining = budget - used
                motif_time = min(3.4, max(remaining * 0.75, 0.4)) if remaining > 0 else 0.0
                motif_time = min(motif_time, remaining)
                if motif_time > 0.05:
                    self._play_step_motif(step, wanted, motif_time)

                hold = duration - used - motif_time - reserve
                if hold > 0:
                    self.wait(hold)

                if last_step and reserve > 0:
                    self.play(
                        FadeOut(header),
                        FadeOut(self._diagram_group),
                        FadeOut(current_caption),
                        run_time=reserve,
                    )

        def _play_step_motif(self, step: dict, active_ids: set[str], run_time: float) -> None:
            """Run this step's kind-motif ON the diagram elements it
            activates: a dot travels along an activated edge if there is
            one, landing at an activated node (or the edge's own
            destination) where the existing per-kind motif then plays --
            gate splits at a gate node, a stack grows at a store node, and
            so on. Falls back to the diagram's own center when a step
            activates nothing (never crashes on a sparse plan)."""
            edge_ids = [eid for eid in step.get("activates") or [] if eid in self._diagram_edges]
            node_ids = [nid for nid in step.get("activates") or [] if nid in self._diagram_nodes]
            kind = step.get("kind", "transform")

            if edge_ids:
                travel_time = min(max(run_time * 0.45, 0.25), max(run_time - 0.2, 0.15))
                self._travel_dot(edge_ids[0], travel_time)
                remain = max(run_time - travel_time, 0.15)
                if node_ids:
                    pos = self._diagram_nodes[node_ids[0]].get_center()
                else:
                    pos = self._diagram_edges[edge_ids[0]][0].get_end()
                self._play_motif(kind, pos, remain)
            elif node_ids:
                pos = self._diagram_nodes[node_ids[0]].get_center()
                self._play_motif(kind, pos, run_time)
            else:
                pos = self._diagram_group.get_center()
                self._play_motif(kind, pos, run_time)

        def _travel_dot(self, edge_id: str, run_time: float) -> None:
            path = self._diagram_edges[edge_id][0]
            dot = Dot(radius=0.07, color=ACCENT)
            dot.move_to(path.get_start())
            self.add(dot)
            self.play(MoveAlongPath(dot, path), run_time=run_time, rate_func=ease_in_out_sine)
            self.remove(dot)

        def _m(self, x: float) -> float:
            """Scale a hand-tuned motif travel distance by how cramped
            this diagram's actual node spacing is (see _build_diagram);
            item sizes (Square/Circle/Dot radii) are left alone since a
            fixed on-screen size stays legible regardless of layout
            density, only the distance items travel needs to shrink."""
            return x * self._motif_scale

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
            """Input becomes output: an item enters the node, then
            FadeTransforms into a new shape and color, and exits toward
            the next node."""
            t1, t2, t3 = run_time * 0.28, run_time * 0.24, run_time * 0.24
            t4 = run_time - t1 - t2 - t3
            item = Square(0.16, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            item.move_to(pos + LEFT * self._m(0.85))
            self.play(FadeIn(item), run_time=t1)
            self.play(item.animate.move_to(pos), run_time=t2, rate_func=smooth)
            morphed = Circle(radius=0.11, fill_color=ACCENT, fill_opacity=1, stroke_width=0).move_to(pos)
            self.play(FadeTransform(item, morphed), run_time=t3)
            item = morphed
            self.play(
                item.animate.move_to(pos + RIGHT * self._m(0.85)).set_opacity(0),
                run_time=t4,
                rate_func=smooth,
            )

        def _motif_gate(self, pos, run_time: float) -> None:
            """Something is accepted or rejected: an item hits a diamond
            (drawn with Create), a pass copy continues on bright, a fail
            copy drops away dim."""
            t1, t2 = run_time * 0.24, run_time * 0.2
            t3 = run_time - t1 - t2
            item = Square(0.16, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            item.move_to(pos + LEFT * self._m(0.85))
            diamond = Square(0.3, fill_opacity=0, stroke_color=ACCENT, stroke_width=2)
            diamond.rotate(PI / 4).move_to(pos)
            self.play(FadeIn(item), Create(diamond), run_time=t1)
            self.play(item.animate.move_to(pos), run_time=t2, rate_func=smooth)
            passed = item.copy().set_fill(ACCENT, opacity=1)
            failed = item.copy().set_fill(GREY_D, opacity=1)
            self.play(
                FadeOut(item),
                FadeOut(diamond),
                passed.animate.move_to(pos + RIGHT * self._m(0.85)),
                failed.animate.move_to(pos + DOWN * self._m(0.7)).set_opacity(0.15),
                run_time=t3 * 0.75,
                rate_func=smooth,
            )
            self.play(FadeOut(passed), FadeOut(failed), run_time=max(t3 * 0.25, 0.05))

        def _motif_loop(self, pos, run_time: float) -> None:
            """A cycle repeats: Create draws a ring at the node, an item
            orbits it twice at constant speed, then both clear."""
            t_setup = run_time * 0.16
            t_cleanup = run_time * 0.14
            t_loop = run_time - t_setup - t_cleanup
            pivot = pos + UP * self._m(0.62)
            ring = Circle(radius=0.4, stroke_color=GREY_C, stroke_width=2).move_to(pivot)
            item = Dot(radius=0.09, color=ACCENT).move_to(pivot + DOWN * 0.4)
            self.play(Create(ring), FadeIn(item), run_time=t_setup)
            # linear is deliberate here, not an oversight: constant angular
            # velocity is what "a cycle repeats" looks like.
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
                pos + LEFT * self._m(0.7) + DOWN * self._m(0.55),
                pos + DOWN * self._m(0.78),
                pos + RIGHT * self._m(0.7) + DOWN * self._m(0.55),
            ]
            copies = VGroup(*[item.copy().set_opacity(0).move_to(pos) for _ in targets])
            self.play(
                FadeOut(item),
                *[FadeIn(c) for c in copies],
                *[c.animate.move_to(t) for c, t in zip(copies, targets, strict=True)],
                run_time=t2,
                rate_func=smooth,
            )
            self.play(FadeOut(copies), run_time=t3)

        def _motif_store(self, pos, run_time: float) -> None:
            """Something is saved for later: an item slides down and
            becomes a new layer on a small stack, which persists."""
            t1, t2 = run_time * 0.28, run_time * 0.3
            t3 = run_time - t1 - t2
            base = pos + DOWN * self._m(0.55)
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
            self.play(FadeTransform(item, new_layer), run_time=t2)
            item = new_layer
            self.play(item.animate.set_fill(GREY_B), run_time=t3, rate_func=smooth)
            # Deliberately not faded: the stack is the visual record that
            # this step accumulates something, and stays on screen.

        def _motif_compare(self, pos, run_time: float) -> None:
            """Two things are measured against each other: two items rise
            onto a small balance, one is highlighted as the outcome."""
            t1 = run_time * 0.28
            t2 = run_time * 0.32
            t3 = run_time - t1 - t2
            left_start = pos + LEFT * self._m(0.55) + DOWN * self._m(0.85)
            right_start = pos + RIGHT * self._m(0.55) + DOWN * self._m(0.85)
            left_item = Circle(radius=0.13, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            left_item.move_to(left_start)
            right_item = Circle(radius=0.13, fill_color=GREY_A, fill_opacity=1, stroke_width=0)
            right_item.move_to(right_start)
            beam = Line(
                pos + LEFT * self._m(0.65) + DOWN * self._m(0.4),
                pos + RIGHT * self._m(0.65) + DOWN * self._m(0.4),
                stroke_color=GREY_C,
                stroke_width=2,
            )
            pivot = Triangle(fill_color=GREY_C, fill_opacity=1, stroke_width=0).scale(0.08)
            pivot.move_to(pos + DOWN * self._m(0.55))
            self.play(FadeIn(left_item), FadeIn(right_item), Create(beam), FadeIn(pivot), run_time=t1)
            self.play(
                left_item.animate.move_to(pos + LEFT * self._m(0.65) + DOWN * self._m(0.3)),
                right_item.animate.move_to(pos + RIGHT * self._m(0.65) + DOWN * self._m(0.3)),
                run_time=t2,
                rate_func=smooth,
            )
            self.play(
                right_item.animate.set_fill(ACCENT).scale(1.35),
                left_item.animate.set_opacity(0.3),
                run_time=t3 * 0.6,
                rate_func=smooth,
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
            self.play(*reveal_anims, *grow_anims, *tracker_anims, run_time=build_time, rate_func=smooth)
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
            #
            # Pre-existing bug fixed here (found while eyeballing this
            # task's own render, not part of the diagram work but too
            # visible to leave in): `self.add(*live_mobjects)` above put
            # each counter directly into the scene's own top-level mobject
            # list, separate from `group`. Swapping it for `frozen` inside
            # `e["stack"]` only updates the group hierarchy; the original
            # `live` reference was still sitting in the scene's top-level
            # list and outlived every later FadeOut(group), so the last
            # numbers a plan showed (1250, 74%, ...) stayed on screen,
            # ghosted through the caveat and question segments, for every
            # plan with key_numbers. `self.remove` below clears that
            # top-level reference too.
            for e in entries:
                if e["live"] is None:
                    continue
                numeric, suffix = e["parsed"]
                e["live"].clear_updaters()
                frozen = Text(f"{int(numeric)}{suffix}", font_size=44, color=ACCENT, weight=BOLD)
                frozen.move_to(e["anchor"])
                e["stack"].remove(e["live"])
                e["stack"].add(frozen)
                self.remove(e["live"])

            if not is_last:
                self.play(FadeOut(group), run_time=transition, rate_func=smooth)
