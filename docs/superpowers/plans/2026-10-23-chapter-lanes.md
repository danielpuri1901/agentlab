# Chapter Lanes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The scene stage builds each chapter of a video in its own lane, in parallel, with its own retries, checkpoint and text-card fallback, joins the chapters, logs an independent stills review, and reads stage settings from one TOML file per stage.

**Architecture:** `story_video._compose` keeps today's attempt loop but runs it per chapter through a thread pool and joins the chapter videos with one ffmpeg re-encode. Chapters come from the beat roles already on the storyboard. New pure helpers (chapter grouping, chapter prompt note, card source, contact sheet, reviewer verdict, stage loader) each live in the module that owns that concern, and the worker wires flags, stills, the reviewer in logging mode and the stage files.

**Tech Stack:** Python 3.12, Pydantic, Manim CE (subprocess), ffmpeg, Anthropic Messages API through `agentlab.anthropic_api`, `tomllib`, `concurrent.futures`, pytest, Terraform for the scheduler flags.

**Spec:** `docs/superpowers/specs/2026-10-23-chapter-lanes-design.md`

## Global Constraints

- No new dependencies. TOML through `tomllib`; images through ffmpeg; threads through `concurrent.futures`.
- Python 3.12 (`requires-python = ">=3.12"`); run tests with `uv run pytest`, lint with `uv run ruff check src tests`.
- Render-marked tests (`@pytest.mark.render`) are excluded by default (`addopts = "-m 'not render'"`); run one with `uv run pytest -m render tests/test_story_scene.py -k card`.
- Budgets from the spec: 4 attempts per lane, 3 parallel lanes, 600 s render cap, 2400 s shared deadline, reviewer 1 call of 300 output tokens and 60 s.
- Chapter grouping by role: title+problem, mechanism (split in two above 4 beats), result+application, limit+question; fall back to groups of three when roles are out of order.
- The AST guard, the filtered render environment, the number grounding and the cost ledger stay untouched.
- Prompts stay in Python. The TOML files hold settings only.
- Prose rules of the repo: no em dash, normal English in comments and docs, one sentence per line in long Markdown.
- Subagents edit only the files their task lists and do not run git commands; the orchestrator commits after each wave.

## Review Focus

Inputs the spec implies but no single task's happy path exercises, with the task that pins each:

1. A storyboard whose roles are out of order (a repaired board can still carry a stray role) must still split into non-empty contiguous chapters. Task 2 test `test_chapters_fall_back_to_groups_of_three`.
2. A lane whose last attempt is a render with layout warnings when the deadline is gone must ship that render, not a card. Task 7 test `test_lane_ships_layout_fallback_when_deadline_ends_the_round`.
3. A beat shorter than 2.5 s must not break the card: the card holds at least 0.5 s and the base class pads or records an overrun. Task 4 test `test_card_scene_source_uses_each_beat_duration` and the render test with a 1.0 s beat.
4. A reviewer that names chapter 0, a chapter beyond the last, or two chapters must count as ship. Task 6 test `test_review_rejects_invalid_chapter_as_ship`.
5. A chapter list where every chapter is a card must still join, send, and say so in the caption. Task 7 test `test_all_lanes_card_when_deadline_is_gone` and Task 8 test `test_caption_names_card_chapters`.

---

## Wave 1 (Tasks 1 to 6 are independent; run them in parallel)

### Task 1: Stage spec files

**Files:**
- Create: `src/agentlab/stages.py`
- Create: `src/agentlab/stage_specs/deep_read.toml`, `src/agentlab/stage_specs/storyboard.toml`, `src/agentlab/stage_specs/scene.toml`, `src/agentlab/stage_specs/reviewer.toml`
- Test: `tests/test_stages.py`

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `agentlab.stages.stage(name: str) -> Stage` with fields `name`, `model`, `max_tokens`, `timeout_seconds`, `attempts`, `effort: str | None`, `thinking: str | None`, `fingerprint: str` (12 hex chars of the SHA-256 of the file bytes). `agentlab.stages.setting(name, field, env)` returns the environment variable when set, else the file value. `agentlab.stages.SPEC_DIR`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stages.py
import pytest

from agentlab import stages


def test_four_stage_files_load_with_models():
    for name in ("deep_read", "storyboard", "scene", "reviewer"):
        spec = stages.stage(name)
        assert spec.name == name
        assert spec.model.startswith("anthropic/")
        assert spec.max_tokens > 0 and spec.timeout_seconds > 0 and spec.attempts >= 1
        assert len(spec.fingerprint) == 12


def test_fingerprint_changes_when_a_byte_changes(tmp_path, monkeypatch):
    (tmp_path / "scene.toml").write_text(
        'model = "anthropic/claude-opus-4-6"\nmax_tokens = 32000\ntimeout_seconds = 600\nattempts = 4\n'
    )
    monkeypatch.setattr(stages, "SPEC_DIR", tmp_path)
    stages.stage.cache_clear()
    before = stages.stage("scene").fingerprint
    (tmp_path / "scene.toml").write_text(
        'model = "anthropic/claude-opus-4-6"\nmax_tokens = 32000\ntimeout_seconds = 600\nattempts = 3\n'
    )
    stages.stage.cache_clear()
    assert stages.stage("scene").fingerprint != before
    assert stages.stage("scene").attempts == 3


def test_environment_overrides_the_file(monkeypatch):
    stages.stage.cache_clear()
    monkeypatch.setenv("SCENE_MODEL", "anthropic/claude-sonnet-4-6")
    assert stages.setting("scene", "model", "SCENE_MODEL") == "anthropic/claude-sonnet-4-6"
    monkeypatch.delenv("SCENE_MODEL")
    assert stages.setting("scene", "model", "SCENE_MODEL") == stages.stage("scene").model


def test_unknown_stage_raises():
    stages.stage.cache_clear()
    with pytest.raises(FileNotFoundError):
        stages.stage("nope")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_stages.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentlab.stages'`

- [ ] **Step 3: Write the stage files**

`src/agentlab/stage_specs/deep_read.toml`:

```toml
# Deep read: one source becomes a digest and a validated scene plan.
model = "anthropic/claude-sonnet-5-5"
max_tokens = 8000
timeout_seconds = 180
attempts = 2
# Sonnet 5.5 thinks by default and rejects thinking type "disabled".
thinking = "between_tools"
```

`src/agentlab/stage_specs/storyboard.toml`:

```toml
# Storyboard: the 3Blue1Brown-style plan, 6 to 12 beats.
model = "anthropic/claude-opus-5-5"
max_tokens = 32000
timeout_seconds = 600
attempts = 3
effort = "high"
```

`src/agentlab/stage_specs/scene.toml`:

```toml
# Scene code: one Manim file per chapter. attempts is the per-lane cap.
model = "anthropic/claude-opus-4-6"
max_tokens = 32000
timeout_seconds = 600
attempts = 4
effort = "high"
```

`src/agentlab/stage_specs/reviewer.toml`:

```toml
# Reviewer: stills plus storyboard text, never code. One call per video.
model = "anthropic/claude-haiku-4-5"
max_tokens = 300
timeout_seconds = 60
attempts = 1
```

- [ ] **Step 4: Write the loader**

```python
# src/agentlab/stages.py
"""Stage settings, one TOML file per model stage (docs/superpowers/specs/
2026-10-23-chapter-lanes-design.md, section 8).

A stage file holds the model, its effort or thinking setting and the budgets.
Prompts stay in Python. The environment overrides a field so Terraform can
swap a model without an image build; `setting()` applies that precedence.
The fingerprint of the file bytes goes into checkpoint fingerprints, so a
settings change invalidates a saved stage the way a prompt change does.
"""
import hashlib
import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field

SPEC_DIR = Path(__file__).with_name("stage_specs")


class Stage(BaseModel):
    name: str
    model: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    timeout_seconds: int = Field(gt=0)
    attempts: int = Field(ge=1)
    effort: str | None = None
    thinking: str | None = None
    fingerprint: str = Field(min_length=12, max_length=12)


@lru_cache(maxsize=None)
def stage(name: str) -> Stage:
    """The settings of one stage, read once per process."""
    raw = (SPEC_DIR / f"{name}.toml").read_bytes()
    data = tomllib.loads(raw.decode("utf-8"))
    return Stage(name=name, fingerprint=hashlib.sha256(raw).hexdigest()[:12], **data)


def setting(name: str, field: str, env: str):
    """The environment variable when it is set, else the stage file's value."""
    value = os.environ.get(env)
    if value:
        return value
    return getattr(stage(name), field)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_stages.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/stages.py src/agentlab/stage_specs tests/test_stages.py
git commit -m "feat: one TOML file per model stage with env override and fingerprint"
```

---

### Task 2: Chapter grouping

**Files:**
- Modify: `src/agentlab/storyboard.py` (append after the `Storyboard` model, before `StoryboardInvalid`)
- Test: `tests/test_storyboard.py` (append)

**Interfaces:**
- Consumes: `Storyboard`, `Beat` from the same module.
- Produces: `chapters(storyboard: Storyboard) -> list[list[int]]` (0-based beat indexes, contiguous, in order, no empty chapter). `chapter_board(storyboard: Storyboard, indexes: list[int]) -> Storyboard` (a `model_copy` with that beat subset, not re-validated). Constants `CHAPTER_ROLES`, `MAX_MECHANISM_PER_CHAPTER = 4`, `FALLBACK_CHAPTER_SIZE = 3`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_storyboard.py (append)
import json
from pathlib import Path

from agentlab.storyboard import Storyboard, chapter_board, chapters

_FIXTURE_BOARD = Storyboard(
    **json.loads(
        (Path(__file__).parent / "fixtures" / "storyboard_example.json").read_text(encoding="utf-8")
    )
)


def _board_with_roles(roles):
    beats = [
        {"role": role, "narration": f"Beat {i} narration.", "visual": f"Visual {i}.", "on_screen_text": []}
        for i, role in enumerate(roles, start=1)
    ]
    data = _FIXTURE_BOARD.model_dump()
    data["beats"] = beats
    return Storyboard.model_validate(data)


def test_chapters_group_by_role_and_split_long_mechanism():
    # The fixture: title, problem, 5 mechanism, result, application, limit, question.
    assert chapters(_FIXTURE_BOARD) == [[0, 1], [2, 3, 4], [5, 6], [7, 8], [9, 10]]


def test_chapters_keep_four_mechanism_beats_together():
    board = _board_with_roles(
        ["title", "problem", "mechanism", "mechanism", "mechanism", "mechanism", "limit", "question"]
    )
    assert chapters(board) == [[0, 1], [2, 3, 4, 5], [6, 7]]


def test_chapters_skip_missing_roles():
    board = _board_with_roles(["title", "mechanism", "mechanism", "mechanism", "limit", "question"])
    assert chapters(board) == [[0], [1, 2, 3], [4, 5]]


def test_chapters_fall_back_to_groups_of_three():
    board = _board_with_roles(
        ["title", "mechanism", "problem", "mechanism", "result", "limit", "question"]
    )
    assert chapters(board) == [[0, 1, 2], [3, 4, 5], [6]]


def test_chapters_cover_every_beat_once():
    for board in (_FIXTURE_BOARD, _board_with_roles(["title", "mechanism", "problem", "limit", "question", "result"])):
        flat = [i for group in chapters(board) for i in group]
        assert flat == list(range(len(board.beats)))
        assert all(group for group in chapters(board))


def test_chapter_board_keeps_title_and_colour_key():
    sub = chapter_board(_FIXTURE_BOARD, [2, 3, 4])
    assert sub.title == _FIXTURE_BOARD.title
    assert sub.mapping == _FIXTURE_BOARD.mapping
    assert [b.narration for b in sub.beats] == [_FIXTURE_BOARD.beats[i].narration for i in (2, 3, 4)]
    assert len(_FIXTURE_BOARD.beats) == 11  # the original is untouched
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_storyboard.py -k "chapter" -v`
Expected: FAIL with `ImportError: cannot import name 'chapter_board'`

- [ ] **Step 3: Write the implementation**

```python
# src/agentlab/storyboard.py, after class Storyboard

CHAPTER_ROLES = (
    ("title", "problem"),
    ("mechanism",),
    ("result", "application"),
    ("limit", "question"),
)
"""Chapter boundaries follow the lesson's sections (docs/superpowers/specs/
2026-10-23-chapter-lanes-design.md, section 1)."""
MAX_MECHANISM_PER_CHAPTER = 4
FALLBACK_CHAPTER_SIZE = 3


def chapters(storyboard: Storyboard) -> list[list[int]]:
    """Contiguous groups of beat indexes, one group per chapter.

    Roles in the canonical order give role chapters; a mechanism run longer
    than MAX_MECHANISM_PER_CHAPTER splits in two. Any other order falls back
    to fixed groups of FALLBACK_CHAPTER_SIZE beats, so every beat lands in
    exactly one non-empty chapter either way."""
    roles = [beat.role for beat in storyboard.beats]
    groups: list[list[int]] = []
    cursor = 0
    for wanted in CHAPTER_ROLES:
        group = []
        while cursor < len(roles) and roles[cursor] in wanted:
            group.append(cursor)
            cursor += 1
        if group:
            groups.append(group)
    if cursor != len(roles) or not groups:
        return [
            list(range(start, min(start + FALLBACK_CHAPTER_SIZE, len(roles))))
            for start in range(0, len(roles), FALLBACK_CHAPTER_SIZE)
        ]
    split: list[list[int]] = []
    for group in groups:
        if roles[group[0]] == "mechanism" and len(group) > MAX_MECHANISM_PER_CHAPTER:
            half = (len(group) + 1) // 2
            split += [group[:half], group[half:]]
        else:
            split.append(group)
    return split


def chapter_board(storyboard: Storyboard, indexes: list[int]) -> Storyboard:
    """The storyboard with only these beats: same title, definition, focus
    and colour key. A copy for prompting and rendering, never re-validated,
    because the full model requires at least MIN_BEATS beats."""
    return storyboard.model_copy(update={"beats": [storyboard.beats[i] for i in indexes]})
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_storyboard.py -v`
Expected: all passed, including the existing storyboard tests

- [ ] **Step 5: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/storyboard.py tests/test_storyboard.py
git commit -m "feat: group storyboard beats into chapters by role"
```

---

### Task 3: Chapter note in the scene prompt

**Files:**
- Modify: `src/agentlab/scene_code.py` (`_storyboard_prompt`, `build_scene_code_prompt`, `build_edit_prompt`, `write_scene_code`, `edit_scene_code`; add `ChapterNote` above `_storyboard_prompt`)
- Test: `tests/test_scene_code.py` (append)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: `ChapterNote(index: int, count: int, first_beat: int, last_beat: int, previous_visual: str | None)` with `.text() -> str`; keyword argument `chapter: ChapterNote | None = None` on `build_scene_code_prompt`, `build_edit_prompt`, `write_scene_code`, `edit_scene_code`. Existing positional signatures are unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scene_code.py (append)
from agentlab.scene_code import (
    ChapterNote,
    build_edit_prompt,
    build_scene_code_prompt,
    edit_scene_code,
    write_scene_code,
)


def _three_beat_board():
    # Reuse the module's fixture board; the first three beats are enough.
    from agentlab.storyboard import chapter_board
    return chapter_board(BOARD, [0, 1, 2])


def test_chapter_note_names_position_and_previous_visual():
    note = ChapterNote(index=2, count=4, first_beat=3, last_beat=5, previous_visual="a grid of cells")
    text = note.text()
    assert "chapter 2 of 4" in text
    assert "beat_1 to beat_3" in text and "beats 3 to 5" in text
    assert "a grid of cells" in text
    assert "empty stage" in text


def test_first_chapter_note_has_no_previous_visual():
    text = ChapterNote(index=1, count=3, first_beat=1, last_beat=2, previous_visual=None).text()
    assert "first chapter" in text and "previous chapter" not in text


def test_prompts_carry_the_chapter_note_before_the_storyboard():
    board = _three_beat_board()
    note = ChapterNote(index=1, count=3, first_beat=1, last_beat=3, previous_visual=None)
    prompt = build_scene_code_prompt(board, [5.0, 5.0, 5.0], None, None, chapter=note)
    assert prompt.index("<chapter>") < prompt.index("<storyboard_json>")
    edit = build_edit_prompt(board, [5.0, 5.0, 5.0], "source", "boom", chapter=note)
    assert "<chapter>" in edit
    assert "<chapter>" not in build_scene_code_prompt(board, [5.0, 5.0, 5.0], None, None)


def test_write_and_edit_pass_the_note_through():
    board = _three_beat_board()
    seen = []

    def complete(model, messages, **kwargs):
        seen.append(messages[1]["content"])
        return "```python\nx = 1\n```"

    note = ChapterNote(index=3, count=3, first_beat=7, last_beat=9, previous_visual="a brace")
    write_scene_code(board, [5.0, 5.0, 5.0], complete, "m", chapter=note)
    edit_scene_code(board, [5.0, 5.0, 5.0], "x = 1\n", "boom", complete, "m", chapter=note)
    assert all("chapter 3 of 3" in content for content in seen)
```

The existing test module already defines `BOARD`; if it does not, load `tests/fixtures/storyboard_example.json` into a `Storyboard` at the top of the appended block under that name.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scene_code.py -k "chapter or note" -v`
Expected: FAIL with `ImportError: cannot import name 'ChapterNote'`

- [ ] **Step 3: Write the implementation**

```python
# src/agentlab/scene_code.py, above _storyboard_prompt
from dataclasses import dataclass


@dataclass(frozen=True)
class ChapterNote:
    """Where this file sits in the video, for a chapter lane (docs/superpowers/
    specs/2026-10-23-chapter-lanes-design.md, section 2)."""

    index: int
    count: int
    first_beat: int
    last_beat: int
    previous_visual: str | None

    def text(self) -> str:
        local_last = self.last_beat - self.first_beat + 1
        before = (
            f"The previous chapter ended with: {self.previous_visual}"
            if self.previous_visual
            else "This is the first chapter"
        )
        return (
            "<chapter>\n"
            f"This file is chapter {self.index} of {self.count}. Its beat_1 to beat_{local_last} "
            f"are beats {self.first_beat} to {self.last_beat} of the whole storyboard. {before}. "
            "Start on an empty stage and reference nothing from other chapters. "
            "The colour key applies to the whole video.\n"
            "</chapter>\n\n"
        )
```

Then thread it through:

```python
def _storyboard_prompt(
    storyboard: Storyboard, durations: list[float], chapter: ChapterNote | None = None
) -> str:
    """The part of every scene prompt that stays the same across fix rounds."""
    _validate_durations(storyboard, durations)
    # ... existing body unchanged ...
    return (
        (chapter.text() if chapter else "")
        + "<storyboard_json>\n"
        # ... rest of the existing return expression unchanged ...
    )


def build_scene_code_prompt(
    storyboard, durations, feedback, previous_source, chapter: ChapterNote | None = None
) -> str:
    prompt = _storyboard_prompt(storyboard, durations, chapter)
    # ... unchanged ...


def build_edit_prompt(storyboard, durations, source, feedback, chapter: ChapterNote | None = None) -> str:
    return (
        _storyboard_prompt(storyboard, durations, chapter)
        # ... unchanged ...
    )


def write_scene_code(storyboard, durations, complete, model, feedback=None, previous_source=None,
                     chapter: ChapterNote | None = None) -> str:
    # pass chapter=chapter into build_scene_code_prompt; otherwise unchanged


def edit_scene_code(storyboard, durations, source, feedback, complete, model,
                    chapter: ChapterNote | None = None) -> str | None:
    # pass chapter=chapter into build_edit_prompt; otherwise unchanged
```

Keep the full existing bodies; only the `chapter` keyword is added and forwarded.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_scene_code.py -v`
Expected: all passed

- [ ] **Step 5: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/scene_code.py tests/test_scene_code.py
git commit -m "feat: scene prompt carries a chapter note for chapter lanes"
```

---

### Task 4: Fallback card

**Files:**
- Modify: `src/agentlab/story_scene.py` (pure helper `card_scene_source` above the manim import, next to `subtitle_chunks`; method `card` on `StoryScene` under "what the generated code may call")
- Test: `tests/test_story_scene.py` (append; one render-marked test)

**Interfaces:**
- Consumes: `Storyboard` (duck-typed: `.title`, `.beats[i].on_screen_text`, `.beats[i].narration`), no import from `agentlab.storyboard` (the module runs standalone under manim).
- Produces: `card_scene_source(storyboard, durations: list[float]) -> str` (a complete `PaperStory` file whose `beat_i` methods call `self.card`). `StoryScene.card(self, title: str, lines: list[str], seconds: float)`. Constant `CARD_VISUAL_DIRECTION = "text card, the fallback for a chapter that did not render"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_story_scene.py (append)
import json
from types import SimpleNamespace

from agentlab import story_scene
from agentlab.scene_code import check_scene_code, visual_direction


def _board(beats):
    return SimpleNamespace(
        title="Low-Rank Adaptation",
        beats=[SimpleNamespace(narration=n, on_screen_text=labels) for n, labels in beats],
    )


def test_card_scene_source_uses_each_beat_duration():
    board = _board([("The weight matrix is frozen.", ["W0 frozen"]), ("Two small matrices learn the update.", [])])
    source = story_scene.card_scene_source(board, [6.0, 1.0])
    assert "def beat_1(self)" in source and "def beat_2(self)" in source
    assert "self.card('Low-Rank Adaptation', ['W0 frozen'], 6.000)" in source
    # No label: the first words of the narration stand in.
    assert "['Two small matrices learn the update.']" in source and "1.000)" in source
    assert visual_direction(source) == story_scene.CARD_VISUAL_DIRECTION
    assert check_scene_code(source, 2) == []


def test_card_scene_source_escapes_quotes():
    board = _board([("It's a \"test\".", ["O'Brien's \"law\""])])
    source = story_scene.card_scene_source(board, [3.0])
    compile(source, "card.py", "exec")


@pytest.mark.render
def test_card_scene_renders_two_beats_and_writes_timing(tmp_path):
    # Mirror test_golden_scene_renders_and_writes_timing: copy story_scene.py
    # beside the generated file, render through render_scene_video with a
    # SCENE_TIMING_OUT path, then read the timing back.
    from agentlab.story_scene import SCENE_CLASS, TIMING_ENV
    from agentlab.video_render import render_scene_video

    board = _board([("The weight matrix is frozen.", ["W0 frozen"]), ("Short beat.", [])])
    durations = [3.0, 1.0]
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "story_scene.py").write_text(Path(story_scene.__file__).read_text(encoding="utf-8"), encoding="utf-8")
    scene_file = scene_dir / "paper_story.py"
    scene_file.write_text(story_scene.card_scene_source(board, durations), encoding="utf-8")
    spec = {
        "storyboard": {"beats": [{"narration": b.narration} for b in board.beats]},
        "durations": durations,
        "subtitles": [b.narration for b in board.beats],
    }
    timing_path = scene_dir / "beat_times.json"
    video = render_scene_video(scene_file, SCENE_CLASS, spec, scene_dir / "media", quality="l",
                               timeout_seconds=300, extra_env={TIMING_ENV: str(timing_path)})
    assert video.exists()
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert [b["beat"] for b in timing["beats"]] == [1, 2]
    assert timing["beats"][0]["end"] - timing["beats"][0]["start"] >= 3.0
    assert timing["layout_warnings"] == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_story_scene.py -k card -v`
Expected: FAIL with `AttributeError: module 'agentlab.story_scene' has no attribute 'card_scene_source'`

- [ ] **Step 3: Write the pure helper (above the manim import)**

```python
CARD_VISUAL_DIRECTION = "text card, the fallback for a chapter that did not render"
CARD_LABEL_WORDS = 8

_CARD_SCENE_TEMPLATE = '''# Visual direction: {direction}
from story_scene import StoryScene


class PaperStory(StoryScene):
{methods}
'''


def card_scene_source(storyboard, durations: list[float]) -> str:
    """The source of a PaperStory that shows each beat as a text card.

    The fallback for a chapter lane that ran out of attempts (docs/superpowers/
    specs/2026-10-23-chapter-lanes-design.md, section 4). Our code, so the
    guard does not run on it; it only calls StoryScene.card."""
    methods = []
    for i, (beat, seconds) in enumerate(zip(storyboard.beats, durations, strict=True), start=1):
        lines = list(beat.on_screen_text) or [" ".join(beat.narration.split()[:CARD_LABEL_WORDS])]
        methods.append(
            f"    def beat_{i}(self):\n"
            f"        self.card({storyboard.title!r}, {lines!r}, {float(seconds):.3f})\n"
        )
    return _CARD_SCENE_TEMPLATE.format(direction=CARD_VISUAL_DIRECTION, methods="\n".join(methods))
```

- [ ] **Step 4: Write the method (inside `StoryScene`, after `hold`)**

```python
        def card(self, title: str, lines: list[str], seconds: float):
            """A text card: the title at the top edge, up to two lines centred
            on the stage, fade in, hold, fade out. The fallback for a chapter
            that would not render; a model scene should never need it."""
            heading = self.fit(
                Text(wrap_text(title, 36), font_size=60, color=WHITE, line_spacing=1.1)
            )
            heading.to_edge(UP, buff=EDGE_MARGIN)
            body = VGroup(*[self.label(line, size=36, width=40) for line in lines[:2]])
            body.arrange(DOWN, buff=0.5)
            body.move_to([0.0, (STAGE_TOP + STAGE_BOTTOM) / 2 - 0.3, 0.0])
            hold = max(float(seconds) - 2.0, 0.5)
            self.play(FadeIn(heading, shift=DOWN * 0.2), FadeIn(body, shift=UP * 0.2), run_time=1.0)
            self.wait(hold)
            self.play(FadeOut(heading), FadeOut(body), run_time=1.0)
```

Use the names the module already imports from manim (`Text`, `VGroup`, `FadeIn`, `FadeOut`, `UP`, `DOWN`); add any missing one to the existing manim import line, not a new import.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_story_scene.py -v` then `uv run pytest -m render tests/test_story_scene.py -k card -v`
Expected: both passed (the render test needs the local manim; if `uvx --python 3.12 manim` is not available on this machine, report that the render test ran in CI only)

- [ ] **Step 6: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/story_scene.py tests/test_story_scene.py
git commit -m "feat: text card scene as the fallback for a chapter that did not render"
```

---

### Task 5: Join and contact sheet

**Files:**
- Modify: `src/agentlab/video_render.py` (`mux_final`; add `sheet_times`, `contact_sheet`, `stack_images` after `mux_final`)
- Test: `tests/test_video_render.py` (append)

**Interfaces:**
- Consumes: `run_subprocess` seam, `beat_lengths` shape of a timing dict (`timing["beats"][i]["start"|"end"]`).
- Produces: `mux_final(video_path: Path | list[Path], audio_path, out_path, timeout_seconds=..., saturation=None)`: a list joins through the ffmpeg concat demuxer and always encodes once with libx264. `sheet_times(timing: dict, margin: float = 0.2) -> list[float]` (three per beat). `contact_sheet(video_path, timing, out_png, timeout_seconds=DEFAULT_RENDER_TIMEOUT_SECONDS, width=1280) -> Path`. `stack_images(paths: list[Path], out_png, timeout_seconds=...) -> Path`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_video_render.py (append)
from agentlab import video_render


def _recording_run(calls):
    def fake_run(cmd, **kwargs):
        calls.append([str(c) for c in cmd])
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
    return fake_run


def test_mux_final_joins_a_list_through_the_concat_demuxer(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_render, "run_subprocess", _recording_run(calls))
    a, b = tmp_path / "c1.mp4", tmp_path / "c2.mp4"
    a.write_bytes(b"v"); b.write_bytes(b"v")
    out = video_render.mux_final([a, b], tmp_path / "n.mp3", tmp_path / "final.mp4")
    cmd = calls[-1]
    assert cmd[cmd.index("-f") + 1] == "concat" and "-safe" in cmd
    assert "libx264" in cmd and "copy" not in cmd
    listing = tmp_path / "final.concat.txt"
    assert listing.read_text(encoding="utf-8") == f"file '{a}'\nfile '{b}'\n"
    assert out == tmp_path / "final.mp4"


def test_mux_final_single_video_without_saturation_still_copies(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_render, "run_subprocess", _recording_run(calls))
    video_render.mux_final(tmp_path / "one.mp4", tmp_path / "n.mp3", tmp_path / "final.mp4")
    assert "copy" in calls[-1]


def test_sheet_times_three_per_beat_inside_the_beat():
    timing = {"beats": [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 4.4}]}
    times = video_render.sheet_times(timing)
    assert times == [0.2, 2.0, 3.8, 4.1, 4.2, 4.3]


def test_contact_sheet_grabs_each_time_then_tiles(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_render, "run_subprocess", _recording_run(calls))
    timing = {"beats": [{"start": 0.0, "end": 4.0}, {"start": 4.0, "end": 8.0}]}
    out = video_render.contact_sheet(tmp_path / "chapter.mp4", timing, tmp_path / "sheet.png")
    grabs, tile = calls[:-1], calls[-1]
    assert len(grabs) == 6 and all("-ss" in c and "-frames:v" in c for c in grabs)
    assert any("tile=3x2" in part for part in tile)
    assert out == tmp_path / "sheet.png"


def test_stack_images_vstacks_in_order(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(video_render, "run_subprocess", _recording_run(calls))
    paths = [tmp_path / "a.png", tmp_path / "b.png", tmp_path / "c.png"]
    video_render.stack_images(paths, tmp_path / "all.png")
    cmd = calls[-1]
    assert [cmd[i + 1] for i, c in enumerate(cmd) if c == "-i"] == [str(p) for p in paths]
    assert any("vstack=inputs=3" in part for part in cmd)
```

Add `import subprocess` at the top of the test module if it is not there.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_video_render.py -k "concat or sheet or stack" -v`
Expected: FAIL (`mux_final` builds no concat list; `sheet_times` missing)

- [ ] **Step 3: Write the implementation**

```python
def mux_final(
    video_path,
    audio_path: Path,
    out_path: Path,
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS,
    saturation: float | None = None,
) -> Path:
    """Video+audio mux. One video with no saturation: `-c:v copy`. A list of
    chapter videos joins through the concat demuxer and is encoded once:
    stream copy across a concat left unclean timestamps at the seams
    (explainer-video worktree, 2026-10-22). With a saturation, ffmpeg's eq
    filter re-encodes it: 3Blue1Brown renders at saturation 1.5."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    videos = list(video_path) if isinstance(video_path, (list, tuple)) else None
    if videos:
        listing = out_path.with_suffix(".concat.txt")
        listing.write_text("".join(f"file '{Path(p)}'\n" for p in videos), encoding="utf-8")
        video_input = ["-f", "concat", "-safe", "0", "-i", str(listing)]
    else:
        video_input = ["-i", str(video_path)]
    encode = saturation is not None or videos is not None
    video_args = ["-c:v", "copy"]
    if encode:
        video_args = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
        if saturation is not None:
            video_args = ["-vf", f"eq=saturation={saturation}", *video_args]
    cmd = ["ffmpeg", "-y", *video_input, "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
           *video_args, "-c:a", "aac", "-shortest", str(out_path)]
    run_subprocess(cmd, timeout=timeout_seconds)
    return out_path


def sheet_times(timing: dict, margin: float = 0.2) -> list[float]:
    """Three timestamps per beat: just after its start, its middle, just
    before its end. The inset shrinks on a beat shorter than four margins."""
    times = []
    for beat in timing["beats"]:
        start, end = float(beat["start"]), float(beat["end"])
        inset = min(margin, (end - start) / 4)
        times += [round(start + inset, 3), round((start + end) / 2, 3), round(end - inset, 3)]
    return times


def contact_sheet(
    video_path, timing: dict, out_png, timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS, width: int = 1280
) -> Path:
    """Stills of every beat tiled three per row (docs/superpowers/specs/
    2026-10-23-chapter-lanes-design.md, section 6). One ffmpeg grab per
    timestamp, then one tile pass."""
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    times = sheet_times(timing)
    frame_dir = out_png.with_suffix("")
    frame_dir.mkdir(parents=True, exist_ok=True)
    for i, at in enumerate(times):
        run_subprocess(
            ["ffmpeg", "-y", "-ss", f"{at:.3f}", "-i", str(video_path), "-frames:v", "1",
             "-vf", f"scale={width // 3}:-2", str(frame_dir / f"frame_{i:03d}.png")],
            timeout=timeout_seconds,
        )
    rows = max(1, len(times) // 3)
    run_subprocess(
        ["ffmpeg", "-y", "-framerate", "1", "-i", str(frame_dir / "frame_%03d.png"),
         "-vf", f"tile=3x{rows}", "-frames:v", "1", str(out_png)],
        timeout=timeout_seconds,
    )
    return out_png


def stack_images(paths: list, out_png, timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS) -> Path:
    """Chapter sheets stacked top to bottom into one image for Telegram."""
    out_png = Path(out_png)
    if len(paths) == 1:
        shutil.copy(paths[0], out_png)
        return out_png
    inputs = [arg for p in paths for arg in ("-i", str(p))]
    run_subprocess(
        ["ffmpeg", "-y", *inputs, "-filter_complex", f"vstack=inputs={len(paths)}", "-frames:v", "1", str(out_png)],
        timeout=timeout_seconds,
    )
    return out_png
```

Add `import shutil` to the module imports if missing.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_video_render.py -v`
Expected: all passed (existing mux tests still pass because a single path without saturation still copies)

- [ ] **Step 5: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/video_render.py tests/test_video_render.py
git commit -m "feat: join chapter videos with one re-encode and build contact sheets"
```

---

### Task 6: Reviewer

**Files:**
- Create: `src/agentlab/scene_review.py`
- Test: `tests/test_scene_review.py`

**Interfaces:**
- Consumes: `Storyboard` beats (`narration`, `on_screen_text`); a completion callable with the worker's keyword shape `complete(model, messages, *, max_tokens, timeout, extra_fields) -> str`; the Messages adapter's `image_url` content blocks with base64 data URLs (`anthropic_api._content_blocks`).
- Produces: `Verdict(verdict: Literal["ship", "fix"], chapter: int | None, problem: str)`, `VERDICT_SCHEMA`, `REVIEW_SYSTEM`, `review(sheets: list[bytes], chapter_boards: list, complete, model: str, *, max_tokens: int = 300, timeout: int = 60) -> Verdict`. Any failure returns `Verdict(verdict="ship", ...)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scene_review.py
import json
from types import SimpleNamespace

from agentlab import scene_review
from agentlab.scene_review import Verdict, review


def _boards(n):
    return [
        SimpleNamespace(beats=[SimpleNamespace(narration=f"Chapter {k} beat.", on_screen_text=["label"])])
        for k in range(1, n + 1)
    ]


def test_review_sends_sheets_and_text_without_code_and_parses_fix():
    seen = {}

    def complete(model, messages, **kwargs):
        seen["model"], seen["messages"], seen["kwargs"] = model, messages, kwargs
        return json.dumps({"verdict": "fix", "chapter": 2, "problem": "text overlaps the axis"})

    verdict = review([b"png1", b"png2"], _boards(2), complete, "anthropic/claude-haiku-4-5")
    assert verdict == Verdict(verdict="fix", chapter=2, problem="text overlaps the axis")
    content = seen["messages"][1]["content"]
    images = [c for c in content if c.get("type") == "image_url"]
    assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    texts = " ".join(c["text"] for c in content if c.get("type") == "text")
    assert "Chapter 2 beat." in texts and "def beat_" not in texts
    assert seen["kwargs"]["extra_fields"]["output_config"]["format"]["schema"] == scene_review.VERDICT_SCHEMA
    assert seen["kwargs"]["max_tokens"] == 300 and seen["kwargs"]["timeout"] == 60


def test_review_rejects_invalid_chapter_as_ship():
    for chapter in (0, 3, None):
        verdict = review([b"a", b"b"], _boards(2), lambda *a, **k: json.dumps({"verdict": "fix", "chapter": chapter, "problem": "x"}), "m")
        assert verdict.verdict == "ship"


def test_review_failure_counts_as_ship():
    def boom(*args, **kwargs):
        raise TimeoutError("slow")

    verdict = review([b"a"], _boards(1), boom, "m")
    assert verdict.verdict == "ship" and "TimeoutError" in verdict.problem
    verdict = review([b"a"], _boards(1), lambda *a, **k: "not json", "m")
    assert verdict.verdict == "ship"


def test_review_schema_forbids_extra_keys():
    assert scene_review.VERDICT_SCHEMA["additionalProperties"] is False
    assert set(scene_review.VERDICT_SCHEMA["required"]) == {"verdict", "chapter", "problem"}
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_scene_review.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'agentlab.scene_review'`

- [ ] **Step 3: Write the implementation**

```python
# src/agentlab/scene_review.py
"""Independent review of a finished video's stills (docs/superpowers/specs/
2026-10-23-chapter-lanes-design.md, section 7).

The reviewer sees one contact sheet per chapter and the storyboard text of
that chapter, never the code. It names at most one chapter with a problem a
viewer would notice, as a structured verdict the worker can act on. Every
failure of the reviewer itself counts as "ship": it can delay a video, never
block it.
"""
import base64
import logging
from typing import Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

REVIEW_SYSTEM = (
    "You review stills from a short explainer video before it is sent. Each chapter gives you "
    "one contact sheet (three frames per beat, left to right, top to bottom) and the words the "
    "viewer hears and reads in that chapter. Judge what a viewer would notice: unreadable or "
    "overlapping text, an empty or near-empty stage for a whole beat, a drawing that shows "
    "something other than what the narration describes, text or shapes cut off at the frame "
    "edge. Ignore style and taste. Reply with the verdict object only: verdict \"ship\" when "
    "every chapter is acceptable; verdict \"fix\" with the single worst chapter number and one "
    "sentence naming the problem a viewer would say, when one chapter needs a fix. Never name "
    "more than one chapter."
)

VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["ship", "fix"]},
        "chapter": {"type": ["integer", "null"]},
        "problem": {"type": "string"},
    },
    "required": ["verdict", "chapter", "problem"],
    "additionalProperties": False,
}


class Verdict(BaseModel):
    verdict: Literal["ship", "fix"]
    chapter: int | None = None
    problem: str = ""


def _chapter_text(k: int, board) -> str:
    lines = [f"Chapter {k}:"]
    for beat in board.beats:
        labels = ", ".join(beat.on_screen_text) or "none"
        lines.append(f"- hears: {beat.narration} | reads: {labels}")
    return "\n".join(lines)


def review(sheets: list[bytes], chapter_boards: list, complete, model: str, *,
           max_tokens: int = 300, timeout: int = 60) -> Verdict:
    content: list[dict] = []
    for k, (png, board) in enumerate(zip(sheets, chapter_boards, strict=True), start=1):
        content.append({"type": "text", "text": _chapter_text(k, board)})
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        content.append({"type": "image_url", "image_url": {"url": url}})
    content.append({"type": "text", "text": "Reply with the verdict object."})
    messages = [{"role": "system", "content": REVIEW_SYSTEM}, {"role": "user", "content": content}]
    try:
        raw = complete(
            model, messages, max_tokens=max_tokens, timeout=timeout,
            extra_fields={"output_config": {"format": {"type": "json_schema", "schema": VERDICT_SCHEMA}}},
        )
        verdict = Verdict.model_validate_json(raw)
    except (ValidationError, ValueError, TypeError, OSError, TimeoutError, RuntimeError) as exc:
        logger.warning("reviewer failed, shipping without a verdict: %s", exc)
        return Verdict(verdict="ship", problem=f"reviewer failed: {type(exc).__name__}")
    except Exception as exc:  # noqa: BLE001 - the reviewer never blocks a video
        logger.warning("reviewer failed, shipping without a verdict: %s", exc)
        return Verdict(verdict="ship", problem=f"reviewer failed: {type(exc).__name__}")
    if verdict.verdict == "fix" and not (verdict.chapter and 1 <= verdict.chapter <= len(sheets)):
        return Verdict(verdict="ship", problem="reviewer named no valid chapter")
    return verdict
```

Check `anthropic_api._content_blocks` accepts a list mixing `{"type": "text", "text": ...}` and `{"type": "image_url", ...}` dicts; if it only accepts strings and image dicts, add the text case there in the same style as the image case (that file is otherwise not yours; keep the change to that one branch).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_review.py tests/test_anthropic_api.py -v`
Expected: all passed

- [ ] **Step 5: Commit (orchestrator, after the wave)**

```bash
git add src/agentlab/scene_review.py tests/test_scene_review.py src/agentlab/anthropic_api.py
git commit -m "feat: stills reviewer with a structured one-chapter verdict"
```

---

## Wave 2 (after Wave 1 is merged into the branch)

### Task 7: Chapter lanes in the compose loop

**Files:**
- Modify: `src/agentlab/story_video.py` (`_compose`, `StoryResult`, new `_Chapter`, `_build_lane`, `_merge_timing`, `_ship`)
- Test: `tests/test_story_video.py` (the `seams` fixture and new tests)

**Interfaces:**
- Consumes: `storyboard.chapters`, `storyboard.chapter_board` (Task 2); `scene_code.ChapterNote` and the `chapter=` keyword on `write_scene_code`/`edit_scene_code` (Task 3); `story_scene.card_scene_source` (Task 4); `video_render.mux_final` list form (Task 5); `stages.stage("scene")` (Task 1).
- Produces: `compose_story_video(..., chapter_lanes: bool = False) -> StoryResult`; `StoryResult.chapters: list[dict]` (each `{"index", "beats", "status", "attempts", "source_path", "video_path", "timing"}`), empty on the monolithic path; constants `LANE_ATTEMPTS = stage("scene").attempts`, `LANE_WORKERS = 3`, `CARD_RENDER_TIMEOUT = 180`, `CARD_RESERVE_SECONDS = 240`; lane attempt records carry `"attempt": f"c{k}-{n}"` (string) and `"chapter": k`.

- [ ] **Step 1: Update the seams fixture**

In `tests/test_story_video.py`:
- `fake_coder` and `fake_edit` take `chapter=None` as a keyword and record it in `state["chapters"]`.
- `fake_render` derives the beat count from `len(spec["durations"])` and builds the timing with `_timing_n(n)`:

```python
def _timing_n(n, overruns=None):
    overruns = overruns or [0.0] * n
    beats, cursor = [], 0.0
    for index, overrun in enumerate(overruns, start=1):
        length = 5.0 + overrun
        beats.append({"beat": index, "start": cursor, "end": cursor + length, "narration": 5.0, "overrun": overrun})
        cursor += length
    return {"beats": beats, "total": cursor, "layout_warnings": []}
```

- the fakes for `mux_final` and `concat_audio` accept a list of videos (record `state["mux_inputs"]`).
- a helper that returns a guard-clean per-chapter source: `def chapter_source(storyboard, durations): return story_scene.card_scene_source(storyboard, durations)`, used by `fake_coder` when `state["codes"]` is empty and `chapter is not None`.

- [ ] **Step 2: Write the failing tests**

```python
def test_chapter_lanes_ship_a_card_for_a_lane_that_never_renders(seams, tmp_path):
    bad = "import os\n"  # the guard rejects it every time
    seams["coder_for_chapter"] = {2: bad}
    result = story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c", chapter_lanes=True,
    )
    statuses = [c["status"] for c in result.chapters]
    assert len(result.chapters) == 5 and statuses[1] == "card"
    assert statuses.count("shipped") == 4
    assert len(seams["mux_inputs"]) == 5
    assert all(rec["attempt"].startswith("c2-") for rec in result.attempt_records if rec.get("chapter") == 2)


def test_chapter_lanes_merge_timing_in_chapter_order(seams, tmp_path):
    result = story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c", chapter_lanes=True,
    )
    beats = result.timing["beats"]
    assert [b["beat"] for b in beats] == list(range(1, N + 1))
    assert beats[0]["start"] == 0.0 and all(beats[i]["start"] == beats[i - 1]["end"] for i in range(1, N))
    assert result.timing["total"] == beats[-1]["end"] == 5.0 * N
    assert result.scene_source.startswith("# chapter 1")


def test_all_lanes_card_when_deadline_is_gone(seams, tmp_path):
    result = story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c", chapter_lanes=True, deadline_seconds=1,
    )
    assert all(c["status"] == "card" for c in result.chapters)
    assert result.video_path.exists()


def test_lane_ships_layout_fallback_when_deadline_ends_the_round(seams, tmp_path, monkeypatch):
    warned = _timing_n(2)
    warned["layout_warnings"] = ["beat 1: text overlaps"]
    seams["timings_for_chapter"] = {1: [warned]}
    monkeypatch.setattr(story_video, "LAYOUT_ROUND_MIN_SECONDS", 10**9)  # no time for a layout round
    result = story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c", chapter_lanes=True,
    )
    assert result.chapters[0]["status"] == "shipped"
    assert result.timing["layout_warnings"] == ["chapter 1: beat 1: text overlaps"]


def test_chapter_lanes_pass_a_note_with_the_previous_visual(seams, tmp_path):
    story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c", chapter_lanes=True,
    )
    notes = sorted(seams["chapters"], key=lambda n: n.index)
    assert notes[0].previous_visual is None and notes[0].first_beat == 1
    assert notes[1].previous_visual == BOARD.beats[1].visual
    assert notes[-1].last_beat == N


def test_monolithic_path_is_unchanged_by_the_flag_default(seams, tmp_path):
    result = story_video.compose_story_video(
        DIGEST, PLAN, None, "Joanna", lambda *a, **k: "", tmp_path / "w", tmp_path / "out.mp4",
        story_model="s", scene_model="c",
    )
    assert result.chapters == [] and isinstance(result.selected_attempt, int)
```

Expose `state` from the `seams` fixture (return it) so tests can set `coder_for_chapter` and `timings_for_chapter`; the fakes consult these by `chapter.index`.

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/test_story_video.py -k "chapter or lane or monolithic" -v`
Expected: FAIL with `TypeError: compose_story_video() got an unexpected keyword argument 'chapter_lanes'`

- [ ] **Step 4: Write the implementation**

Shape (keep every existing rule of the loop; the monolithic path is one lane):

```python
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from agentlab.scene_code import ChapterNote
from agentlab.stages import stage
from agentlab.story_scene import card_scene_source
from agentlab.storyboard import chapter_board, chapters

LANE_ATTEMPTS = stage("scene").attempts
LANE_WORKERS = 3
CARD_RENDER_TIMEOUT = 180
CARD_RESERVE_SECONDS = 240
"""Time a lane leaves for its card: the model and render attempts stop this
long before the video deadline, so the card still renders inside it."""


@dataclass
class _Chapter:
    index: int
    beats: list[int]
    board: Storyboard
    durations: list[float]
    note: ChapterNote | None
    rendered: _Rendered | None = None
    status: str = "pending"
    records: list[dict] = field(default_factory=list)


@dataclass
class StoryResult:
    # ... existing fields ...
    chapters: list[dict] = field(default_factory=list)
```

`_build_lane(chapter, *, work_dir, bounded_complete, scene_model, attempts, started, deadline_seconds, checkpoints, scene_fingerprint, fallback_card) -> _Chapter` is today's loop body with these substitutions:
- `storyboard` becomes `chapter.board`, `durations` becomes `chapter.durations`, `spec` is built from them, `scene_dir = work_dir / f"chapter_{chapter.index}" / "scene"` for lanes and `work_dir / "scene"` for the monolithic lane.
- `write_scene_code(..., chapter=chapter.note)` and `edit_scene_code(..., chapter=chapter.note)`.
- the checkpoint stage name is `"scene"` for the monolithic lane and `f"scene-{chapter.index}"` for a lane; the lane fingerprint is `fingerprint("scene", scene_model, board_fingerprint, SCENE_CODE_SYSTEM, STORY_SCENE_API, str(chapter.index), str(len(chapter.beats)), stage("scene").fingerprint)`.
- attempt records get `"chapter": chapter.index` and, for lanes, `"attempt": f"c{chapter.index}-{attempt}"`; they are appended to `chapter.records`.
- the deadline used for attempts is `deadline_seconds - CARD_RESERVE_SECONDS` when `fallback_card` else `deadline_seconds`.
- on exhaustion with no fallback render: if `fallback_card`, write `card_scene_source(chapter.board, chapter.durations)` as attempt `"card"`, render it with `CARD_RENDER_TIMEOUT` (not the remaining time), set `chapter.status = "card"`; a card render failure raises `StoryFailed(f"chapter {k}: the card did not render: ...")`. Without `fallback_card`, raise `StoryFailed` exactly as today.
- a shipped render sets `chapter.status` to `"shipped"`, a layout fallback that ships sets `"layout_fallback"`.
- `layout_warnings` of a lane are prefixed `f"chapter {k}: "` when merged.

`_merge_timing(chapters) -> dict` renumbers beats with cumulative offsets:

```python
def _merge_timing(chapters: list[_Chapter]) -> dict:
    beats, warnings, offset, n = [], [], 0.0, 0
    for chapter in chapters:
        timing = chapter.rendered.timing
        for beat in timing["beats"]:
            n += 1
            beats.append({**beat, "beat": n, "start": offset + beat["start"], "end": offset + beat["end"]})
        warnings += [f"chapter {chapter.index}: {w}" for w in timing.get("layout_warnings") or []]
        offset += float(timing["total"])
    return {"beats": beats, "total": offset, "layout_warnings": warnings}
```

`_compose` after narration:

```python
    if chapter_lanes:
        groups = chapters(storyboard)
        lanes = []
        for k, beats in enumerate(groups, start=1):
            previous = storyboard.beats[beats[0] - 1].visual if beats[0] > 0 else None
            lanes.append(_Chapter(
                index=k, beats=beats, board=chapter_board(storyboard, beats),
                durations=[durations[i] for i in beats],
                note=ChapterNote(index=k, count=len(groups), first_beat=beats[0] + 1,
                                 last_beat=beats[-1] + 1, previous_visual=previous),
            ))
        with ThreadPoolExecutor(max_workers=min(len(lanes), LANE_WORKERS)) as pool:
            lanes = list(pool.map(lambda c: _build_lane(c, ..., attempts=LANE_ATTEMPTS, fallback_card=True), lanes))
    else:
        lanes = [_build_lane(_Chapter(index=1, beats=list(range(len(durations))), board=storyboard,
                                      durations=durations, note=None), ..., attempts=attempt_limit, fallback_card=False)]
    return _ship(lanes, ...)
```

`_ship(lanes, ...)`: `lengths = [l for c in lanes for l in beat_lengths(c.rendered.timing)]`; `concat_audio(clips, ..., target_seconds=lengths)`; srt with `lengths`; `mux_final([c.rendered.video for c in lanes] if chapter_lanes else lanes[0].rendered.video, ...)`; `timing = _merge_timing(lanes)` for lanes, the single timing otherwise; `scene_source = "\n\n".join(f"# chapter {c.index}\n{c.rendered.source}" for c in lanes)` for lanes, the single source otherwise; `visual_direction = visual_direction(lanes[0].rendered.source)`; `attempts = sum(len(c.records) for c in lanes)`; `selected_attempt = lanes[0].rendered.attempt`; `attempt_records = [r for c in lanes for r in c.records]`; `chapters = [{"index": c.index, "beats": c.beats, "status": c.status, "attempts": len(c.records), "source_path": ..., "video_path": str(c.rendered.video), "timing": c.rendered.timing} for c in lanes]` for lanes, `[]` otherwise.

`_Rendered` gets a `source_path: Path | None = None` field set by the lane so `_ship` can report it.

- [ ] **Step 5: Run the whole module's tests**

Run: `uv run pytest tests/test_story_video.py -v`
Expected: all passed, old and new

- [ ] **Step 6: Commit (orchestrator)**

```bash
git add src/agentlab/story_video.py tests/test_story_video.py
git commit -m "feat: chapter lanes in the compose loop, parallel, with card fallback and merged timing"
```

---

### Task 8: Worker wiring, Telegram sheet, flags

**Files:**
- Modify: `src/agentlab/worker.py` (`_deep_read_extra_fields`, `_complete_long`, new `_complete_review`, `_run_explain_track` story block, `explain_command` model defaults, `_upload_attempt_artifacts`)
- Modify: `src/agentlab/notify.py` (`notify`, `_deliver`, the quiet-hours queue record, `flush`)
- Modify: `infra/scheduler.tf` (built-lane environment), `infra/variables.tf` and `infra/ecs.tf` (two new variables and environment entries: `chapter_lanes`, `reviewer_mode`)
- Test: `tests/test_worker.py`, `tests/test_notify.py` (append)

**Interfaces:**
- Consumes: `compose_story_video(..., chapter_lanes=...)` and `StoryResult.chapters` (Task 7); `video_render.contact_sheet`, `stack_images` (Task 5); `scene_review.review` (Task 6); `stages.stage`, `stages.setting` (Task 1).
- Produces: environment flags `CHAPTER_LANES` ("1" on, default off) and `REVIEWER` (`off` default, `log`, `fix` reserved and treated as `log` until the calibration gate); ledger fields `chapter_lanes`, `chapters`, `reviewer_verdict`, `reviewer_chapter`, `reviewer_problem`; story record fields `chapters`, `reviewer`; S3 keys `stories/<key>/stills/chapter_<k>.png` and `stories/<key>/stills/all.png`; `notify(..., photo_png=..., photo_s3_key=...)` sends the sheet before the video, through the same quiet-hours queue.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_worker.py (append)
def test_caption_names_card_chapters():
    from agentlab.worker import _chapter_caption_lines
    chapters = [{"index": 1, "status": "shipped"}, {"index": 2, "status": "card"}, {"index": 3, "status": "layout_fallback"}]
    assert _chapter_caption_lines(chapters) == ["Chapter 2 of 3 is a text card."]
    assert _chapter_caption_lines([]) == []


def test_flags_read_the_environment(monkeypatch):
    from agentlab.worker import _chapter_lanes_on, _reviewer_mode
    monkeypatch.delenv("CHAPTER_LANES", raising=False)
    monkeypatch.delenv("REVIEWER", raising=False)
    assert _chapter_lanes_on() is False and _reviewer_mode() == "off"
    monkeypatch.setenv("CHAPTER_LANES", "1")
    monkeypatch.setenv("REVIEWER", "fix")
    assert _chapter_lanes_on() is True and _reviewer_mode() == "log"  # fix waits for calibration
    monkeypatch.setenv("REVIEWER", "log")
    assert _reviewer_mode() == "log"


def test_story_models_fall_back_to_stage_files(monkeypatch):
    from agentlab import stages, worker
    for name in ("STORY_MODEL", "SCENE_MODEL", "DEEP_READ_MODEL", "STORY_EFFORT", "DEEP_READ_THINKING"):
        monkeypatch.delenv(name, raising=False)
    assert worker._story_models() == (stages.stage("storyboard").model, stages.stage("scene").model)
    assert worker._deep_read_extra_fields() == {"thinking": {"type": stages.stage("deep_read").thinking}}
    monkeypatch.setenv("SCENE_MODEL", "anthropic/claude-sonnet-4-6")
    assert worker._story_models()[1] == "anthropic/claude-sonnet-4-6"


def test_upload_attempt_artifacts_accepts_string_attempt_labels(tmp_path):
    from agentlab.worker import _upload_attempt_artifacts
    source = tmp_path / "paper_story.py"
    source.write_text("x = 1\n", encoding="utf-8")
    puts = []
    s3 = SimpleNamespace(put_object=lambda **kw: puts.append(kw["Key"]))
    records = _upload_attempt_artifacts(s3, "bucket", "vid", [{"attempt": "c2-1", "chapter": 2, "source_path": str(source)}])
    assert puts == ["stories/vid/attempts/c2-1/scene.py"]
    assert records[0]["source_key"].endswith("attempts/c2-1/scene.py")
```

```python
# tests/test_notify.py (append)
def test_notify_sends_the_sheet_before_the_video(monkeypatch, tmp_path):
    from agentlab import notify as n
    sent = []
    monkeypatch.setattr(n, "send_photo", lambda config, caption, png: sent.append(("photo", caption)))
    monkeypatch.setattr(n, "send_video", lambda config, caption, path, buttons=None: sent.append(("video", caption)))
    config = n.TelegramConfig(token="t", chat_id="c")
    n._deliver(config, "caption", [[("COOL", "x")]], b"png", video_path=tmp_path / "v.mp4")
    assert [kind for kind, _ in sent] == ["photo", "video"]
```

Adapt the `TelegramConfig` construction and the `_deliver` signature to the module's real shapes (read `notify.py` first); the assertion that the photo goes first is the point.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_worker.py tests/test_notify.py -k "caption or flags or stage_files or labels or sheet" -v`
Expected: FAIL with `ImportError` on the new names

- [ ] **Step 3: Write the worker changes**

```python
from agentlab.scene_review import review
from agentlab.stages import setting, stage
from agentlab.video_render import contact_sheet, stack_images


def _chapter_lanes_on() -> bool:
    return os.environ.get("CHAPTER_LANES", "").strip().lower() in {"1", "true", "on", "yes"}


def _reviewer_mode() -> str:
    """off, or log. "fix" is accepted and runs as log until the calibration
    gate in the spec (20 videos, precision 0.7) is passed and the fix round
    is implemented."""
    mode = os.environ.get("REVIEWER", "off").strip().lower()
    return "log" if mode in {"log", "fix"} else "off"


def _story_models() -> tuple[str, str]:
    return setting("storyboard", "model", "STORY_MODEL"), setting("scene", "model", "SCENE_MODEL")


def _chapter_caption_lines(chapters: list[dict]) -> list[str]:
    total = len(chapters)
    return [f"Chapter {c['index']} of {total} is a text card." for c in chapters if c.get("status") == "card"]


def _complete_review(model, messages, *, usage_sink=None, pricing_model=None, max_tokens=300, timeout=60,
                     extra_fields=None) -> str:
    response = _run_completion(model, messages, max_tokens=max_tokens, timeout=timeout, stage="review",
                               usage_sink=usage_sink, pricing_model=pricing_model, extra_fields=extra_fields)
    return response.choices[0].message.content
```

- `_deep_read_extra_fields`: `thinking = setting("deep_read", "thinking", "DEEP_READ_THINKING")`.
- `_complete_long`: effort from `setting("storyboard", "effort", "STORY_EFFORT")`; delete `DEFAULT_STORY_EFFORT`.
- `explain_command`: `story_model, scene_model = _story_models()`; `deep_read_model = setting("deep_read", "model", "DEEP_READ_MODEL")`; keep the pick model as is.
- `_upload_attempt_artifacts`: the prefix already interpolates `record["attempt"]`; make sure a string label works and `"chapter"` survives in the returned record.
- In `_run_explain_track`: pass `chapter_lanes=_chapter_lanes_on()` to `compose_story_video`. After the upload of the video and before the caption:

```python
        reviewer = None
        sheet_png = None
        if story.chapters:
            sheets, boards = [], []
            for chapter in story.chapters:
                png_path = tmp_path / "stills" / f"chapter_{chapter['index']}.png"
                contact_sheet(chapter["video_path"], chapter["timing"], png_path)
                s3_client.upload_file(str(png_path), bucket, f"stories/{key}/stills/chapter_{chapter['index']}.png")
                sheets.append(png_path.read_bytes())
                boards.append(chapter_board(story.storyboard, chapter["beats"]))
            all_png = stack_images([tmp_path / "stills" / f"chapter_{c['index']}.png" for c in story.chapters], tmp_path / "stills" / "all.png")
            sheet_key = f"stories/{key}/stills/all.png"
            s3_client.upload_file(str(all_png), bucket, sheet_key)
            sheet_png = all_png.read_bytes()
            if _reviewer_mode() == "log":
                verdict = review(sheets, boards, bind(_complete_review, usage_sink=model_usage),
                                 stage("reviewer").model, max_tokens=stage("reviewer").max_tokens,
                                 timeout=stage("reviewer").timeout_seconds)
                reviewer = verdict.model_dump()
```

- The story record gains `"chapters": story.chapters` (without `video_path`) and `"reviewer": reviewer`.
- The caption gains the lines from `_chapter_caption_lines(story.chapters)` after the claim.
- The ledger item gains `"chapter_lanes": bool(story.chapters)`, `"chapters": [{"index", "status", "attempts"}]`, `"reviewer_verdict"`, `"reviewer_chapter"`, `"reviewer_problem"` (None when no reviewer ran).
- `notify(..., photo_png=sheet_png, photo_s3_key=sheet_key if sheet_png else None)`.

- [ ] **Step 4: Write the notify changes**

`notify()` and the queued ping record take `photo_png` and `photo_s3_key`; `_deliver` sends the photo first (caption: the first line of the text) and then the video with the buttons; the quiet-hours flush downloads the photo by `photo_s3_key` next to the video. Read the existing queue record shape in `notify.py` and `flush_pings_command` in `worker.py` and extend both with the one new key.

- [ ] **Step 5: Terraform**

`infra/variables.tf`:

```hcl
variable "chapter_lanes" {
  description = "Build each storyboard chapter in its own lane (1 on, empty off)."
  type        = string
  default     = ""
}

variable "reviewer_mode" {
  description = "Stills reviewer: off, log (verdict stored, nothing changes), fix (reserved)."
  type        = string
  default     = "off"
}
```

`infra/ecs.tf`, explain task environment: `{ name = "CHAPTER_LANES", value = var.chapter_lanes }, { name = "REVIEWER", value = var.reviewer_mode }`.
`infra/scheduler.tf`, the built-lane `containerOverrides` environment: add `{ name = "CHAPTER_LANES", value = "1" }, { name = "REVIEWER", value = "log" }` so week one runs lanes on the built lane only while the task default stays off.

- [ ] **Step 6: Run the suite and lint**

Run: `uv run pytest -q` and `uv run ruff check src tests`
Expected: all passed, no lint findings

- [ ] **Step 7: Commit (orchestrator)**

```bash
git add src/agentlab/worker.py src/agentlab/notify.py tests/test_worker.py tests/test_notify.py infra/variables.tf infra/ecs.tf infra/scheduler.tf
git commit -m "feat: wire chapter lanes, stills, the logging reviewer and stage files into the explain worker"
```

---

## After the code

1. Push the branch, open the PR with the spec and plan linked; CI builds the image and runs the render tests.
2. Deploy by tag; `terraform apply` with `chapter_lanes` left at its default (off) and the built-lane override on.
3. Validation steps 1 to 4 of the spec, in order; the cloud replay is the ship gate for the first built-lane video on lanes.
