# Metaphor Videos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the fixed-template Compose stage of the daily paper videos with a per-paper metaphor animation: a storyboard call, a generated Manim scene, a guarded render-and-judge loop, and the old template as the fallback.

**Architecture:** Five new modules (`storyboard`, `story_scene`, `scene_code`, `frame_judge`, `story_video`) sit between the existing deep read and the existing mux/deliver code. `video_render` is generalised so any scene file can be rendered through one credential-free, time-limited subprocess seam. `worker._run_explain_track` tries the story path and falls back to today's template on `StoryFailed`.

**Tech Stack:** Python 3.12, uv, pydantic 2, litellm on Bedrock (Claude Sonnet 4.6), Manim Community 0.21 (pinned in uv.lock, `video` dependency group), ffmpeg/ffprobe, Amazon Polly, moto for AWS tests, pytest, ruff.

**Spec:** `docs/specs/2026-09-05-metaphor-videos.md` (read it first; the old stage list is in `docs/specs/2026-08-23-daily-paper-videos.md`).

## Global Constraints

- Run tests with `uv run pytest -q` from the repo root. Render-marked tests are excluded by default; run one with `uv run pytest -m render tests/<file>::<test> -q`.
- Lint with `uv run ruff check src tests` and `uv run ruff format src tests` before every commit.
- `story_scene.py` and `video_scenes.py` never import `agentlab`; they import only manim and the stdlib, with the manim import wrapped in try/except so the main venv (which has no manim) can import their pure helpers.
- The model names in code stay exactly `bedrock/global.anthropic.claude-sonnet-4-6` (deep read default) and `bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0` (pick default).
- No em dash anywhere: code, prompts, docs, commit messages. Use a plain dash.
- In Markdown files, each sentence goes on its own line.
- Names say what a thing is or does; no history in names.
- Never stage or commit `docs/golden-papers-labeling.md` or anything under `scripts/videos/`: those are Daniel's uncommitted work.
- Commit messages: conventional prefix (`feat:`, `test:`, `docs:`, `infra:`), no co-author trailer.
- The stage rectangle for generated scenes is x from -6.4 to 6.4 and y from -2.3 to 3.6; the caption band below y = -2.3 belongs to the base class.
- Retry budget: at most 3 scene-code attempts per video; low render timeout 480 s; final render timeout 900 s; per-beat overrun limit 0.75 s; total overrun limit 3.0 s.

---

## File structure

| Path | Responsibility |
|---|---|
| `src/agentlab/video_render.py` (modify) | The one subprocess seam for manim/ffmpeg: clean env, timeout, any scene file; narration from texts; audio padding |
| `src/agentlab/storyboard.py` (create) | Storyboard schema, prompt, parse, grounding check, `design_storyboard` |
| `src/agentlab/story_scene.py` (create) | `StoryScene` Manim base class: timing, captions, palette, helpers; pure timing helpers |
| `src/agentlab/scene_code.py` (create) | Coder prompt, API cheat-sheet, AST guard, `write_scene_code` |
| `src/agentlab/frame_judge.py` (create) | Frame sampling, vision judge call, verdict rule |
| `src/agentlab/story_video.py` (create) | `compose_story_video`: the attempt loop, best-candidate pick, final render, mux |
| `src/agentlab/worker.py` (modify) | Story path first, template fallback, artifacts, ledger fields, `_complete_long` |
| `scripts/story_video_for_url.py` (create) | Local end-to-end: one URL to one story video, no DynamoDB, no Telegram |
| `infra/ecs.tf`, `infra/iam.tf` (modify) | 4 vCPU / 8 GB; `stories/*` prefix |
| `docs/runbooks/metaphor-videos.md` (create) | How to run, inspect, and tune |
| `tests/test_video_render.py` (modify), `tests/test_worker.py` (modify) | Updated seams |
| `tests/test_storyboard.py`, `tests/test_story_scene.py`, `tests/test_scene_code.py`, `tests/test_frame_judge.py`, `tests/test_story_video.py` (create) | One test file per module |
| `tests/fixtures/storyboard_golden.json`, `tests/fixtures/paper_story_golden.py` (create) | A hand-written storyboard and scene that pass every check; shared by Tasks 3 to 6 |

Task order: Tasks 1 to 5 are independent of each other (they share only the spec's interfaces) and can run in parallel. Task 6 needs 1 to 5. Task 7 needs 6. Task 8 is the orchestrator's live verification and deploy.

---

### Task 1: video_render becomes the general render seam

**Files:**
- Modify: `src/agentlab/video_render.py`
- Test: `tests/test_video_render.py`

**Interfaces:**
- Consumes: nothing new.
- Produces (later tasks rely on these exact names):
  - `narrate(polly_client, texts: list[str], voice_id: str, out_dir) -> list[NarrationClip]`
  - `render_scene_video(scene_file, scene_class: str, spec: dict, out_dir, quality: str = "l", timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS, extra_env: dict | None = None) -> Path`
  - `render_template_video(plan: ScenePlan, durations: list[float], out_dir, quality: str = "l") -> Path`
  - `template_spec(plan, durations) -> dict`
  - `render_env(scene_dir: Path, spec_path: Path, extra_env: dict | None = None) -> dict`
  - `concat_audio(clips, out_path, target_seconds: list[float] | None = None) -> Path`
  - `mux_final(video_path, audio_path, out_path) -> Path` (public name for the existing `_mux_final`)
  - `build_srt(clips, durations: list[float] | None = None) -> str`
  - `DEFAULT_RENDER_TIMEOUT_SECONDS = 480`, `RENDER_ENV_KEYS`
  - `render_video(plan, clips, out_path)` unchanged in signature.

- [ ] **Step 1: Write the failing tests**

Replace the two `narrate` tests, the two `render_scene_video` tests, and the `render_video` orchestration test, and add the new ones. In `tests/test_video_render.py`:

```python
def test_narrate_returns_one_clip_per_text_with_right_text_and_duration(sample_plan, tmp_path, monkeypatch):
    monkeypatch.setattr(video_render, "ffprobe_duration", lambda path: 4.5)
    polly = FakePolly(
        [{"Id": "Ivy", "LanguageCode": "en-US", "SupportedEngines": ["neural"]}],
        audio=b"fake-mp3-bytes",
    )
    texts = video_render.scene_texts(sample_plan)
    clips = video_render.narrate(polly, texts, "Ivy", tmp_path / "audio")

    assert [c.text for c in clips] == texts
    assert all(c.seconds == 4.5 for c in clips)
    assert all(c.path.exists() and c.path.read_bytes() == b"fake-mp3-bytes" for c in clips)
    assert [call["Text"] for call in polly.synthesize_calls] == texts
    assert all(call["VoiceId"] == "Ivy" and call["Engine"] == "neural" for call in polly.synthesize_calls)


def test_narrate_creates_out_dir_if_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(video_render, "ffprobe_duration", lambda path: 3.0)
    polly = FakePolly([{"Id": "Ivy", "LanguageCode": "en-US", "SupportedEngines": ["neural"]}])
    out_dir = tmp_path / "does" / "not" / "exist" / "yet"
    video_render.narrate(polly, ["one", "two"], "Ivy", out_dir)
    assert out_dir.exists()


def test_render_env_has_no_aws_keys_and_puts_scene_dir_first_on_pythonpath(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIA-should-not-leak")
    monkeypatch.setenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", "/v2/credentials/x")
    monkeypatch.setenv("PYTHONPATH", "/elsewhere")
    monkeypatch.setenv("PATH", "/usr/bin")
    scene_dir = tmp_path / "scene"
    spec_path = tmp_path / "spec.json"

    env = video_render.render_env(scene_dir, spec_path, extra_env={"SCENE_TIMING_OUT": "/t.json"})

    assert not any(key.startswith("AWS_") for key in env)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(scene_dir)
    assert "/elsewhere" in env["PYTHONPATH"]
    assert env["SCENE_SPEC_JSON"] == str(spec_path)
    assert env["SCENE_TIMING_OUT"] == "/t.json"
    assert env["PATH"] == "/usr/bin"


def test_render_template_video_builds_expected_manim_command(monkeypatch, tmp_path, sample_plan):
    calls = []
    media_dir = tmp_path / "out"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return _fake_manim_run(media_dir)(cmd, **kwargs)

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    monkeypatch.setattr(video_render, "_manim_command", lambda: ["uvx", "--python", "3.12", "manim"])
    durations = video_render.default_scene_durations(sample_plan)

    result = video_render.render_template_video(sample_plan, durations, media_dir, quality="l")

    assert result.name == "PaperScene.mp4"
    cmd, kwargs = calls[0]
    assert cmd[:4] == ["uvx", "--python", "3.12", "manim"]
    assert "-ql" in cmd
    assert str(video_render.VIDEO_SCENES_FILE) in cmd
    assert video_render.SCENE_CLASS in cmd
    assert kwargs["timeout"] == video_render.DEFAULT_RENDER_TIMEOUT_SECONDS
    spec = json.loads(Path(kwargs["env"]["SCENE_SPEC_JSON"]).read_text(encoding="utf-8"))
    assert spec["durations"] == durations
    assert spec["plan"]["title"] == sample_plan.title
    assert spec["captions"] == video_render.scene_texts(sample_plan)


def test_render_scene_video_takes_any_scene_file_and_timeout(monkeypatch, tmp_path):
    calls = []
    media_dir = tmp_path / "out"

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        out_file = media_dir / "videos" / "paper_story" / "480p15" / "PaperStory.mp4"
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_bytes(b"fake-mp4")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    monkeypatch.setattr(video_render, "_manim_command", lambda: ["uvx", "--python", "3.12", "manim"])
    scene_file = tmp_path / "scene" / "paper_story.py"
    scene_file.parent.mkdir()
    scene_file.write_text("# scene", encoding="utf-8")

    result = video_render.render_scene_video(
        scene_file, "PaperStory", {"storyboard": {}, "durations": [1.0], "captions": ["x"]},
        media_dir, quality="l", timeout_seconds=42, extra_env={"SCENE_TIMING_OUT": "/t.json"},
    )

    assert result.name == "PaperStory.mp4"
    cmd, kwargs = calls[0]
    assert str(scene_file) in cmd and "PaperStory" in cmd
    assert kwargs["timeout"] == 42
    assert kwargs["env"]["SCENE_TIMING_OUT"] == "/t.json"
    assert kwargs["env"]["PYTHONPATH"].split(os.pathsep)[0] == str(scene_file.parent)


def test_template_spec_raises_on_duration_count_mismatch(sample_plan):
    with pytest.raises(ValueError):
        video_render.template_spec(sample_plan, [1.0])


def test_concat_audio_pads_each_clip_to_its_target(monkeypatch, tmp_path):
    run_calls = []
    monkeypatch.setattr(
        video_render, "run_subprocess",
        lambda cmd, **kwargs: run_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    clips = [
        NarrationClip(path=tmp_path / "a.mp3", seconds=3.0, text="a"),
        NarrationClip(path=tmp_path / "b.mp3", seconds=2.0, text="b"),
    ]
    video_render.concat_audio(clips, tmp_path / "out.mp3", target_seconds=[3.4, 1.5])
    filter_arg = run_calls[0][run_calls[0].index("-filter_complex") + 1]
    assert "[0:a]apad=whole_dur=3.400[a0];" in filter_arg
    assert "[1:a]apad=whole_dur=2.000[a1];" in filter_arg  # never shorter than the clip
    assert filter_arg.endswith("[a0][a1]concat=n=2:v=0:a=1[out]")


def test_concat_audio_without_targets_is_a_plain_concat(monkeypatch, tmp_path):
    run_calls = []
    monkeypatch.setattr(
        video_render, "run_subprocess",
        lambda cmd, **kwargs: run_calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    clips = [NarrationClip(path=tmp_path / "a.mp3", seconds=3.0, text="a")]
    video_render.concat_audio(clips, tmp_path / "out.mp3")
    filter_arg = run_calls[0][run_calls[0].index("-filter_complex") + 1]
    assert filter_arg == "[0:a]concat=n=1:v=0:a=1[out]"


def test_build_srt_uses_given_durations_when_provided(tmp_path):
    clips = [
        NarrationClip(path=tmp_path / "a.mp3", seconds=1.0, text="first"),
        NarrationClip(path=tmp_path / "b.mp3", seconds=1.0, text="second"),
    ]
    srt = video_render.build_srt(clips, durations=[2.5, 1.0])
    assert "00:00:00,000 --> 00:00:02,500" in srt
    assert "00:00:02,500 --> 00:00:03,500" in srt
```

Update `test_render_video_orchestrates_render_concat_mux`: monkeypatch `video_render.render_template_video` (signature `(plan, durations, out_dir, quality="m")`) instead of `render_scene_video`, keep every other assertion. Update the render-marked test at the bottom to call `render_template_video`.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_video_render.py -q`
Expected: failures on `narrate` signature, missing `render_env`, `render_template_video`, `template_spec`, `concat_audio`, and `build_srt(durations=...)`.

- [ ] **Step 3: Implement**

In `src/agentlab/video_render.py`:

```python
DEFAULT_RENDER_TIMEOUT_SECONDS = 480
# The render subprocess gets a fresh environment built from these keys only:
# no AWS_* variable and no AWS_CONTAINER_CREDENTIALS_RELATIVE_URI reaches
# manim, so model-written scene code (story_video.py) can never reach the
# task role's credentials even if scene_code.py's guard missed something.
RENDER_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


def narrate(polly_client, texts: list[str], voice_id: str, out_dir) -> list[NarrationClip]:
    """One mp3 per text, duration measured with ffprobe. The template path
    passes scene_texts(plan); the story path passes each beat's narration."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, text in enumerate(texts):
        response = polly_client.synthesize_speech(
            Text=text, OutputFormat="mp3", VoiceId=voice_id, Engine="neural"
        )
        audio_path = out_dir / f"clip_{i:02d}.mp3"
        audio_path.write_bytes(response["AudioStream"].read())
        clips.append(NarrationClip(path=audio_path, seconds=ffprobe_duration(audio_path), text=text))
    return clips


def build_srt(clips: list[NarrationClip], durations: list[float] | None = None) -> str:
    lengths = durations if durations is not None else [c.seconds for c in clips]
    if len(lengths) != len(clips):
        raise ValueError(f"durations has {len(lengths)} entries, clips has {len(clips)}")
    entries = []
    cursor = 0.0
    for i, (clip, length) in enumerate(zip(clips, lengths, strict=True), start=1):
        start, end = cursor, cursor + length
        cursor = end
        wrapped = "\n".join(textwrap.wrap(clip.text, SRT_WRAP_WIDTH)) or clip.text
        entries.append(f"{i}\n{_format_srt_timestamp(start)} --> {_format_srt_timestamp(end)}\n{wrapped}\n")
    return "\n".join(entries)


def render_env(scene_dir: Path, spec_path: Path, extra_env: dict | None = None) -> dict:
    env = {key: os.environ[key] for key in RENDER_ENV_KEYS if key in os.environ}
    python_path = str(scene_dir)
    if os.environ.get("PYTHONPATH"):
        python_path = f"{python_path}{os.pathsep}{os.environ['PYTHONPATH']}"
    env["PYTHONPATH"] = python_path
    env["SCENE_SPEC_JSON"] = str(spec_path)
    if extra_env:
        env.update(extra_env)
    return env


def _write_scene_spec(spec: dict) -> Path:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", prefix="agentlab-scene-spec-", delete=False) as handle:
        json.dump(spec, handle)
        return Path(handle.name)


def _find_rendered_video(out_dir: Path, scene_class: str) -> Path:
    matches = sorted(Path(out_dir).glob(f"videos/*/*/{scene_class}.mp4"))
    if not matches:
        raise FileNotFoundError(f"no rendered {scene_class}.mp4 found under {out_dir}")
    return matches[-1]


def render_scene_video(
    scene_file, scene_class: str, spec: dict, out_dir, quality: str = "l",
    timeout_seconds: int = DEFAULT_RENDER_TIMEOUT_SECONDS, extra_env: dict | None = None,
) -> Path:
    """Render any Manim scene file through the one guarded subprocess seam:
    fresh environment (render_env), hard timeout, spec handed over as a
    JSON path in SCENE_SPEC_JSON. Raises subprocess.CalledProcessError on a
    manim failure and subprocess.TimeoutExpired on a timeout."""
    scene_file = Path(scene_file)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    spec_path = _write_scene_spec(spec)
    env = render_env(scene_file.parent, spec_path, extra_env)
    cmd = _manim_command() + ["render", f"-q{quality}", "--media_dir", str(out_dir), str(scene_file), scene_class]
    run_subprocess(cmd, env=env, timeout=timeout_seconds)
    return _find_rendered_video(out_dir, scene_class)


def template_spec(plan: ScenePlan, durations: list[float]) -> dict:
    texts = scene_texts(plan)
    if len(durations) != len(texts):
        raise ValueError(f"durations has {len(durations)} entries, plan needs {len(texts)}")
    return {"plan": plan.model_dump(), "durations": durations, "captions": texts}


def render_template_video(plan: ScenePlan, durations: list[float], out_dir, quality: str = "l") -> Path:
    """Today's fixed PaperScene template, through the general seam."""
    return render_scene_video(VIDEO_SCENES_FILE, SCENE_CLASS, template_spec(plan, durations), out_dir, quality=quality)


def concat_audio(clips: list[NarrationClip], out_path: Path, target_seconds: list[float] | None = None) -> Path:
    """Concatenate narration clips; with target_seconds, first pad each clip
    with silence to its target (never shorter than the clip itself), so the
    audio lines up with beats that ran a little longer than their narration."""
    if target_seconds is not None and len(target_seconds) != len(clips):
        raise ValueError(f"target_seconds has {len(target_seconds)} entries, clips has {len(clips)}")
    inputs = []
    for clip in clips:
        inputs += ["-i", str(clip.path)]
    n = len(clips)
    if target_seconds is None:
        filter_str = "".join(f"[{i}:a]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    else:
        pads = "".join(
            f"[{i}:a]apad=whole_dur={max(target, clip.seconds):.3f}[a{i}];"
            for i, (clip, target) in enumerate(zip(clips, target_seconds, strict=True))
        )
        filter_str = pads + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filter_str, "-map", "[out]", str(out_path)]
    run_subprocess(cmd)
    return out_path
```

Rename `_mux_final` to `mux_final` (keep the body). `render_video` becomes:

```python
def render_video(plan: ScenePlan, clips: list[NarrationClip], out_path) -> tuple[Path, Path]:
    out_path = Path(out_path)
    work_dir = Path(tempfile.mkdtemp(prefix="agentlab-video-"))
    durations = [clip.seconds for clip in clips]
    silent_video = render_template_video(plan, durations, work_dir / "render", quality="m")
    audio_path = concat_audio(clips, work_dir / "narration.mp3")
    srt_path = out_path.with_suffix(".srt")
    srt_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path.write_text(build_srt(clips), encoding="utf-8")
    video_path = mux_final(silent_video, audio_path, out_path)
    return video_path, srt_path
```

Update the module docstring's "Concat pattern" paragraph to mention the clean environment, the timeout, and the per-clip padding. `import os` is already there.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_video_render.py tests/test_worker.py -q`
Expected: all pass (test_worker's `narrate` monkeypatch is positional, so it still works).

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/agentlab/video_render.py tests/test_video_render.py
git commit -m "feat: render seam takes any scene file, clean env, timeout; narrate from texts; padded concat"
```

---

### Task 2: storyboard.py

**Files:**
- Create: `src/agentlab/storyboard.py`
- Modify: `src/agentlab/scene_plan.py` (make `_extract_json_object` public as `extract_json_object`, keep the old name as an alias)
- Test: `tests/test_storyboard.py`
- Create: `tests/fixtures/storyboard_golden.json`

**Interfaces:**
- Consumes: `ScenePlan` from `agentlab.scene_plan`; `complete(model, messages) -> str`.
- Produces: `Storyboard`, `Beat`, `Mapping`, `StoryboardInvalid`, `design_storyboard(digest, plan, complete, model) -> Storyboard`, `parse_storyboard(raw, digest, plan) -> tuple[Storyboard | None, str]`, `ungrounded_numbers(data: dict, digest: str, plan: ScenePlan) -> list[str]`, `build_storyboard_prompt(digest, plan) -> str`, `STORYBOARD_SYSTEM`.

- [ ] **Step 1: Write the golden storyboard fixture**

`tests/fixtures/storyboard_golden.json` (the compaction house move, Daniel's own metaphor; five beats; every number below appears in the test digest defined in Step 2):

```json
{
  "metaphor": "A house move with one small suitcase: the house is the whole chat, the suitcase is the token budget, and a gold passport is the one code the agent must not lose.",
  "why_this_metaphor": "Compaction is choosing what to pack; the viewer sees the passport left on a shelf or packed first, and that is the entire mechanism.",
  "rejected": ["A river with a dam that lets only some water through", "A librarian shelving books into one small box"],
  "mapping": [
    {"paper_term": "chat history", "visual": "a house full of grey furniture"},
    {"paper_term": "token budget", "visual": "one small suitcase"},
    {"paper_term": "exact identifier", "visual": "a gold passport"},
    {"paper_term": "compaction prompt", "visual": "the packing list pinned above the door"}
  ],
  "beats": [
    {"narration": "This house is an agent's whole chat. You must move today, and only one small suitcase comes with you.", "visual": "A large grey rectangle labelled house on the left fills with nine small grey furniture blocks. A small gold-outlined suitcase appears on the right. A gold passport sits among the furniture.", "on_screen_text": ["house = the chat", "suitcase = the budget"]},
    {"narration": "The first packing list says: summarize concisely. The mover grabs the sofa and leaves the passport on the shelf.", "visual": "A label 'packing list: summarize concisely' appears above the door. One furniture block shrinks and slides into the suitcase. The passport stays behind and dims. A red cross appears next to the suitcase.", "on_screen_text": ["summarize concisely"]},
    {"narration": "The second list says: list every code first. Same mover, same suitcase. Now the passport goes in before anything else.", "visual": "The label changes to 'packing list: list every code first'. The passport slides into the suitcase first and glows. A green check replaces the red cross.", "on_screen_text": ["list every code first"]},
    {"narration": "Measured on a weak model, a plain summary keeps zero percent of the codes. Codes first keeps 44%.", "visual": "Two bars grow from the suitcase: a short red bar labelled 0% and a tall gold bar counting up to 44%.", "on_screen_text": ["0%", "44%"]},
    {"narration": "The paper does not test lists longer than 50 items. So here is the street test: does your compaction prompt name the codes first?", "visual": "A dashed boundary line appears at the edge of the house labelled '50 items tested'. The suitcase stays centred with the question text below it.", "on_screen_text": ["50 items tested"]}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_storyboard.py`:

```python
import json
from pathlib import Path

import pytest

from agentlab import storyboard as sb
from agentlab.scene_plan import ScenePlan

FIXTURE = Path(__file__).parent / "fixtures" / "storyboard_golden.json"
PLAN_FIXTURE = Path(__file__).parent / "fixtures" / "sample_plan.json"

DIGEST = (
    "# Headline\nCompaction keeps codes only if the prompt asks for them.\n\n"
    "## What the paper shows\nA plain summary keeps 0% of exact codes on a weak model; "
    "codes first keeps 44%. Tested on lists of up to 50 items.\n\n## Limits\n"
    "The paper does not test lists longer than 50 items."
)


@pytest.fixture
def plan() -> ScenePlan:
    return ScenePlan(**json.loads(PLAN_FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _fenced(data: dict) -> str:
    return "Here are three candidates...\n```json\n" + json.dumps(data) + "\n```"


def test_golden_storyboard_validates(golden):
    board = sb.Storyboard(**golden)
    assert len(board.beats) == 5
    assert board.beats[0].on_screen_text == ["house = the chat", "suitcase = the budget"]


def test_parse_storyboard_tolerates_fences_and_prose(golden, plan):
    board, error = sb.parse_storyboard(_fenced(golden), DIGEST, plan)
    assert error == ""
    assert board.metaphor.startswith("A house move")


def test_parse_storyboard_clips_long_strings_instead_of_failing(golden, plan):
    golden["metaphor"] = "x" * 500
    golden["beats"][0]["visual"] = "y" * 900
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert len(board.metaphor) == sb.MAX_METAPHOR
    assert len(board.beats[0].visual) == sb.MAX_VISUAL


@pytest.mark.parametrize("beat_count", [4, 10])
def test_parse_storyboard_rejects_bad_beat_counts(golden, plan, beat_count):
    beat = golden["beats"][0]
    golden["beats"] = [beat] * beat_count
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "beats" in error


def test_parse_storyboard_rejects_garbage(plan):
    board, error = sb.parse_storyboard("not json at all", DIGEST, plan)
    assert board is None
    assert "JSON" in error


def test_ungrounded_numbers_flags_invented_numbers_only(golden, plan):
    golden["beats"][3]["narration"] = "A plain summary keeps 0% of the codes. Codes first keeps 91%."
    golden["beats"][1]["on_screen_text"] = ["1,999 items"]
    # 1,999 normalises to 1999 (commas ignored) and is nowhere in the digest or plan.
    assert sb.ungrounded_numbers(golden, DIGEST, plan) == ["91%", "1999"]


def test_ungrounded_numbers_accepts_numbers_from_the_plan(golden, plan):
    # 1250 and 74% are key_numbers in sample_plan.json, not in DIGEST.
    golden["beats"][3]["narration"] = "The survey covers 1250 papers and 74% are from this year."
    assert sb.ungrounded_numbers(golden, DIGEST, plan) == []


def test_parse_storyboard_treats_ungrounded_number_as_validation_error(golden, plan):
    golden["beats"][3]["narration"] = "Codes first keeps 91%."
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "91%" in error


def test_design_storyboard_retries_once_with_the_error_then_succeeds(golden, plan):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    replies = iter([_fenced(bad), _fenced(golden)])
    calls = []

    def complete(model, messages):
        calls.append(messages)
        return next(replies)

    board = sb.design_storyboard(DIGEST, plan, complete, model="m")
    assert len(calls) == 2
    assert "beats" in calls[1][-1]["content"]
    assert len(board.beats) == 5


def test_design_storyboard_raises_after_second_failure(golden, plan):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    with pytest.raises(sb.StoryboardInvalid):
        sb.design_storyboard(DIGEST, plan, lambda model, messages: _fenced(bad), model="m")


def test_prompt_carries_digest_plan_and_the_hard_rules(plan):
    prompt = sb.build_storyboard_prompt(DIGEST, plan)
    assert DIGEST in prompt
    assert plan.street_test_question in prompt
    for rule in ("three candidate", "flowchart", "never", "street-test"):
        assert rule in sb.STORYBOARD_SYSTEM + prompt
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `uv run pytest tests/test_storyboard.py -q`
Expected: `ModuleNotFoundError: agentlab.storyboard`.

- [ ] **Step 4: Implement**

In `src/agentlab/scene_plan.py`, rename `_extract_json_object` to `extract_json_object` and add `_extract_json_object = extract_json_object` right after it (the tests in test_scene_plan.py may reference the old name).

`src/agentlab/storyboard.py`:

```python
"""Storyboard: the visual-choice step of the metaphor videos
(docs/specs/2026-09-05-metaphor-videos.md, section 1).

One creative model call turns the deep read (digest + grounded ScenePlan)
into a Storyboard: one metaphor, a paper-term-to-visual mapping, and 5 to
9 beats of narration plus a concrete visual description. The coder
(scene_code.py) draws from this; the judge (frame_judge.py) checks against
it. Same parse discipline as scene_plan.py: fences and prose tolerated,
over-long strings clipped, structure and grounding fatal (one retry, then
StoryboardInvalid so the worker falls back to the template).

Temperature: litellm sends no temperature unless asked, and the Anthropic
API's default is 1.0, which is the high-ambiguity setting this step wants.
"""

import json
import re
from collections.abc import Callable

from pydantic import BaseModel, Field, ValidationError, field_validator

from agentlab.scene_plan import ScenePlan, extract_json_object

MAX_METAPHOR = 200
MAX_WHY = 300
MAX_REJECTED = 3
MAX_REJECTED_LEN = 200
MIN_MAPPING = 2
MAX_MAPPING = 6
MAX_TERM = 40
MAX_MAPPING_VISUAL = 60
MIN_BEATS = 5
MAX_BEATS = 9
MAX_NARRATION = 280
MAX_VISUAL = 500
MAX_ON_SCREEN = 3
MAX_ON_SCREEN_LEN = 40


class Mapping(BaseModel):
    paper_term: str = Field(min_length=1, max_length=MAX_TERM)
    visual: str = Field(min_length=1, max_length=MAX_MAPPING_VISUAL)


class Beat(BaseModel):
    narration: str = Field(min_length=1, max_length=MAX_NARRATION)
    visual: str = Field(min_length=1, max_length=MAX_VISUAL)
    on_screen_text: list[str] = Field(default_factory=list, max_length=MAX_ON_SCREEN)

    @field_validator("on_screen_text")
    @classmethod
    def _labels_short(cls, labels: list[str]) -> list[str]:
        for label in labels:
            if not label or len(label) > MAX_ON_SCREEN_LEN:
                raise ValueError(f"on_screen_text entries must be 1 to {MAX_ON_SCREEN_LEN} characters")
        return labels


class Storyboard(BaseModel):
    metaphor: str = Field(min_length=1, max_length=MAX_METAPHOR)
    why_this_metaphor: str = Field(min_length=1, max_length=MAX_WHY)
    rejected: list[str] = Field(default_factory=list, max_length=MAX_REJECTED)
    mapping: list[Mapping] = Field(min_length=MIN_MAPPING, max_length=MAX_MAPPING)
    beats: list[Beat] = Field(min_length=MIN_BEATS, max_length=MAX_BEATS)


class StoryboardInvalid(ValueError):
    """No valid storyboard after the retry; the caller falls back to the template."""


_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%|\d{3,}")


def _normalise(text: str) -> str:
    return text.replace(",", "").replace(" %", "%")


def ungrounded_numbers(data: dict, digest: str, plan: ScenePlan) -> list[str]:
    """Numbers in beat narration or on-screen text that appear neither in
    the digest nor in the scene plan (which the deep read already grounded).
    Percentages and any 3+ digit run count; commas are ignored so 1,250
    matches 1250."""
    haystack = _normalise(digest + "\n" + plan.model_dump_json())
    found: list[str] = []
    for beat in data.get("beats") or []:
        if not isinstance(beat, dict):
            continue
        texts = [beat.get("narration", "")] + list(beat.get("on_screen_text") or [])
        for text in texts:
            if not isinstance(text, str):
                continue
            for token in _NUMBER_RE.findall(_normalise(text)):
                if token not in haystack and token not in found:
                    found.append(token)
    return found


def _clip(data: dict) -> dict:
    for key, limit in (("metaphor", MAX_METAPHOR), ("why_this_metaphor", MAX_WHY)):
        if isinstance(data.get(key), str):
            data[key] = data[key][:limit]
    if isinstance(data.get("rejected"), list):
        data["rejected"] = [r[:MAX_REJECTED_LEN] for r in data["rejected"] if isinstance(r, str)][:MAX_REJECTED]
    if isinstance(data.get("mapping"), list):
        for item in data["mapping"]:
            if isinstance(item, dict):
                for key, limit in (("paper_term", MAX_TERM), ("visual", MAX_MAPPING_VISUAL)):
                    if isinstance(item.get(key), str):
                        item[key] = item[key][:limit]
    if isinstance(data.get("beats"), list):
        for beat in data["beats"]:
            if isinstance(beat, dict):
                for key, limit in (("narration", MAX_NARRATION), ("visual", MAX_VISUAL)):
                    if isinstance(beat.get(key), str):
                        beat[key] = beat[key][:limit]
                if isinstance(beat.get("on_screen_text"), list):
                    beat["on_screen_text"] = [
                        t[:MAX_ON_SCREEN_LEN] for t in beat["on_screen_text"] if isinstance(t, str) and t
                    ][:MAX_ON_SCREEN]
    return data


def parse_storyboard(raw: str, digest: str, plan: ScenePlan) -> tuple[Storyboard | None, str]:
    text = extract_json_object(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "storyboard JSON must be an object"
    data = _clip(data)
    try:
        board = Storyboard(**data)
    except ValidationError as exc:
        return None, str(exc)
    missing = ungrounded_numbers(data, digest, plan)
    if missing:
        return None, (
            "these numbers appear in the beats but not in the digest or scene plan: "
            + ", ".join(missing)
            + ". Use only numbers the source states."
        )
    return board, ""


STORYBOARD_SYSTEM = """You design the visual story for a 90-second explainer video \
about one research paper, in the style of 3Blue1Brown: one metaphor, one object the \
viewer watches change, and the change IS the explanation. You are given the paper's \
digest and a grounded scene plan (claim, mechanism, numbers, limits, street-test \
question).

Work in two parts. First, list three candidate metaphors, one line each. A candidate \
names a concrete physical object or scene the viewer can watch (a suitcase being packed, \
a tree being pruned, a wall of dials, a queue at a gate, a map with routes, a bucket \
filling, a scale tipping), what it stands for, and what visible change on it shows the \
paper's mechanism working. Then pick the one where cause and effect is most visible on \
screen, and say why in one sentence. The two you did not pick go in "rejected".

Then write the beats, 5 to 9 (five to nine). Each beat has:
- narration: what the voice says, 1 to 3 short spoken sentences, at most 280 characters. \
This text is also the caption.
- visual: what is on screen and what changes during this beat, concrete enough that a \
programmer can draw it with circles, rectangles, lines, dots, arrows, plain text and \
simple bars. Say what appears, where (left, right, centre, above), what moves, what \
changes colour or size, and what that change proves. At most 500 characters.
- on_screen_text: up to three short labels (at most 40 characters each) that should be \
drawn as text. Optional.

Rules:
- Beat 1 is the hook: the object appears and the viewer learns what it stands for. Do \
not open with the paper title or a definition.
- The middle beats show the mechanism as changes on the object. Before and after on the \
same object beats a list of steps.
- The paper's real numbers appear ON the metaphor: a bar grows to 41%, a counter climbs \
to 1250, three of ten items turn red. Never a separate statistics slide.
- The second-to-last beat states the limits: what the paper does NOT claim, shown as a \
boundary on the object.
- The last beat asks the scene plan's street-test question, with the object still on \
screen.
- One metaphor for the whole video. Never a flowchart, never a row of labelled boxes \
with arrows, never boxes lighting up in order, never a pipeline diagram.
- No emoji, no images, no photos: everything must be drawable with simple shapes and \
text.
- Narration is plain spoken English: short sentences, one idea per sentence, never an \
em dash (use a comma or a period).
- Every number in narration or on-screen text must appear in the digest or the scene \
plan, written the same way (44%, 1250). Never attribute to the paper anything it does \
not say.

Three exemplars, given only to show the level of concreteness. They are for other \
papers; never reuse them.

1. Compaction (a house move): "This house is an agent's whole chat. You must move today \
with one small suitcase." Visual: a grey house full of furniture blocks on the left, a \
gold-outlined suitcase on the right, a gold passport among the furniture. Next beat: the \
packing list says "summarize concisely", a sofa slides into the suitcase, the passport \
stays on the shelf and dims, a red cross appears. Next beat: the list says "list every \
code first", the passport slides in first and glows, a green check replaces the cross.

2. Tree of Thoughts (a growing tree): a single trunk labelled "prompt" grows three \
branches; each branch grows three twigs; a grey gardener's blade cuts the twigs whose \
small score circle is below 0.5, they fall away dim; the one surviving path glows and \
its leaf becomes the answer. Numbers: the survivor count shows "4 of 27 kept".

3. RLHF (a wall of dials): a grid of 40 small dials all pointing in random directions; \
two candidate answers appear as cards; a hand-shaped cursor taps the better card; a wave \
sweeps across the wall and every dial nudges a few degrees toward the same direction; \
after three taps the wall is almost aligned, and the alignment bar reads the paper's \
win rate.

Output: after your candidate list and choice, output exactly one fenced ```json block \
with keys metaphor, why_this_metaphor, rejected, mapping (list of {paper_term, \
visual}), beats (list of {narration, visual, on_screen_text}). Nothing after the block."""


def build_storyboard_prompt(digest: str, plan: ScenePlan) -> str:
    return (
        "<digest>\n"
        f"{digest}\n"
        "</digest>\n\n"
        "<scene_plan>\n"
        f"{plan.model_dump_json(indent=1)}\n"
        "</scene_plan>\n\n"
        "Reminder of the hard rules now that you have read the paper: one metaphor, the "
        "mechanism as visible change on one object, real numbers on the object, the limits "
        "in the second-to-last beat, the street-test question in the last beat, never a "
        "flowchart, only numbers the digest or scene plan states.\n\n"
        "List three candidate metaphors, choose one, then write the fenced json storyboard."
    )


def design_storyboard(
    digest: str, plan: ScenePlan, complete: Callable[[str, list[dict]], str], model: str
) -> Storyboard:
    messages = [
        {"role": "system", "content": STORYBOARD_SYSTEM},
        {"role": "user", "content": build_storyboard_prompt(digest, plan)},
    ]
    raw = complete(model, messages)
    board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        retry = messages + [
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    "Your storyboard JSON was invalid. Validation error: "
                    f"{error}\nSend the corrected fenced json storyboard again, "
                    "fixing only what the error names, keeping the same metaphor."
                ),
            },
        ]
        raw = complete(model, retry)
        board, error = parse_storyboard(raw, digest, plan)
    if board is None:
        raise StoryboardInvalid(f"storyboard invalid after retry: {error}")
    return board
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_storyboard.py tests/test_scene_plan.py -q`
Expected: all pass.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/agentlab/storyboard.py src/agentlab/scene_plan.py tests/test_storyboard.py tests/fixtures/storyboard_golden.json
git commit -m "feat: storyboard step - one metaphor per paper, grounded beats"
```

---

### Task 3: story_scene.py base class and the golden scene

**Files:**
- Create: `src/agentlab/story_scene.py`
- Create: `tests/fixtures/paper_story_golden.py`
- Test: `tests/test_story_scene.py`

**Interfaces:**
- Consumes: the storyboard JSON shape from Task 2 (only as a dict; this module never imports agentlab).
- Produces:
  - Pure helpers (importable without manim): `load_spec() -> dict`, `wrap_text(s, width=44) -> str`, `beat_record(index, start, end, narration_seconds) -> dict`, `overrun_report(timing, per_beat_limit=0.75, total_limit=3.0) -> str | None`, `beat_midpoints(timing) -> list[float]`, `beat_lengths(timing) -> list[float]`.
  - Constants: `BACKGROUND`, `ACCENT`, `GOLD`, `GREEN`, `RED`, `STAGE_TOP`, `STAGE_BOTTOM`, `STAGE_LEFT`, `STAGE_RIGHT`, `STAGE_WIDTH`, `STAGE_HEIGHT`, `SCENE_CLASS = "PaperStory"`, `TIMING_ENV = "SCENE_TIMING_OUT"`.
  - Class `StoryScene(Scene)` (only when manim imports) with public methods `fit`, `label`, `counter`, `freeze`, `clear_stage`, `hold`; `construct` is final.
  - The timing JSON written to `SCENE_TIMING_OUT`: `{"beats": [{"beat", "start", "end", "narration", "overrun"}], "total"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_story_scene.py`:

```python
import json
from pathlib import Path

import pytest

from agentlab import story_scene

GOLDEN_SCENE = Path(__file__).parent / "fixtures" / "paper_story_golden.py"
GOLDEN_BOARD = Path(__file__).parent / "fixtures" / "storyboard_golden.json"


def test_module_imports_without_manim_and_exposes_pure_helpers():
    assert callable(story_scene.overrun_report)
    assert story_scene.SCENE_CLASS == "PaperStory"
    assert story_scene.STAGE_BOTTOM < 0 < story_scene.STAGE_TOP


def test_beat_record_measures_overrun_never_negative():
    rec = story_scene.beat_record(2, start=10.0, end=14.5, narration_seconds=4.0)
    assert rec == {"beat": 2, "start": 10.0, "end": 14.5, "narration": 4.0, "overrun": 0.5}
    assert story_scene.beat_record(1, 0.0, 3.0, 4.0)["overrun"] == 0.0


def test_overrun_report_is_none_within_limits():
    timing = {"beats": [story_scene.beat_record(1, 0, 4.4, 4.0), story_scene.beat_record(2, 4.4, 8.0, 3.6)]}
    assert story_scene.overrun_report(timing) is None


def test_overrun_report_names_the_offending_beats():
    timing = {"beats": [story_scene.beat_record(1, 0, 5.0, 4.0), story_scene.beat_record(2, 5.0, 8.0, 3.0)]}
    report = story_scene.overrun_report(timing)
    assert "beat 1" in report and "1.0 s" in report
    assert "beat 2" not in report


def test_overrun_report_flags_total_even_when_each_beat_is_small():
    beats = [story_scene.beat_record(i, i * 4.0, i * 4.0 + 4.7, 4.0) for i in range(1, 6)]
    report = story_scene.overrun_report({"beats": beats})
    assert "total overrun" in report


def test_beat_midpoints_and_lengths():
    timing = {"beats": [story_scene.beat_record(1, 0.0, 4.0, 4.0), story_scene.beat_record(2, 4.0, 10.0, 6.0)]}
    assert story_scene.beat_midpoints(timing) == [2.0, 7.0]
    assert story_scene.beat_lengths(timing) == [4.0, 6.0]


def test_load_spec_requires_env(monkeypatch):
    monkeypatch.delenv("SCENE_SPEC_JSON", raising=False)
    with pytest.raises(RuntimeError):
        story_scene.load_spec()


def test_golden_scene_defines_one_beat_method_per_golden_beat():
    import ast

    tree = ast.parse(GOLDEN_SCENE.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "PaperStory")
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    beats = json.loads(GOLDEN_BOARD.read_text(encoding="utf-8"))["beats"]
    assert {f"beat_{i}" for i in range(1, len(beats) + 1)} <= methods
    assert "construct" not in methods


@pytest.mark.render
def test_golden_scene_renders_and_writes_timing(tmp_path):
    import shutil

    from agentlab import video_render

    board = json.loads(GOLDEN_BOARD.read_text(encoding="utf-8"))
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    shutil.copy(GOLDEN_SCENE, scene_dir / "paper_story.py")
    shutil.copy(Path(story_scene.__file__), scene_dir / "story_scene.py")
    durations = [6.0, 7.0, 7.0, 6.0, 8.0]
    captions = [b["narration"] for b in board["beats"]]
    timing_path = tmp_path / "beat_times.json"

    video = video_render.render_scene_video(
        scene_dir / "paper_story.py", "PaperStory",
        {"storyboard": board, "durations": durations, "captions": captions},
        tmp_path / "media", quality="l", extra_env={"SCENE_TIMING_OUT": str(timing_path)},
    )

    assert video.exists()
    timing = json.loads(timing_path.read_text(encoding="utf-8"))
    assert [b["beat"] for b in timing["beats"]] == [1, 2, 3, 4, 5]
    assert all(b["overrun"] < 0.05 for b in timing["beats"])
    assert abs(timing["total"] - sum(durations)) < 0.2
    assert abs(video_render.ffprobe_duration(video) - sum(durations)) < 0.5
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_story_scene.py -q`
Expected: `ModuleNotFoundError: agentlab.story_scene`.

- [ ] **Step 3: Implement story_scene.py**

```python
"""StoryScene: the base class every generated paper scene subclasses
(docs/specs/2026-09-05-metaphor-videos.md, section 4).

Executed standalone by manim (`python -m manim` in the video image, `uvx
manim` on the laptop), so like video_scenes.py it imports ONLY manim and
the stdlib, never agentlab. story_video.py copies this file next to the
generated scene and puts that directory on PYTHONPATH, which is why the
generated code says `from story_scene import StoryScene`.

Reads SCENE_SPEC_JSON: {"storyboard": <Storyboard.model_dump()>,
"durations": [seconds per beat], "captions": [text per beat]}.
Writes SCENE_TIMING_OUT at the end of construct: {"beats": [{"beat",
"start", "end", "narration", "overrun"}], "total"}.

The base class owns: background, the caption band (bottom of frame,
swapped at the start of every beat), safe-margin fitting, and timing (each
beat is padded with a hold so its length is at least its narration's
length, so the audio muxed later lines up). The generated class owns
everything the viewer watches: one method per beat, beat_1 .. beat_n.

Scene.time is manim's renderer clock (Manim Community 0.21: Scene.time
returns renderer.time, advanced by every play and wait), which is what
makes per-beat padding exact.

The pure helpers above the manim import (beat_record, overrun_report,
beat_midpoints, beat_lengths, wrap_text, load_spec) are importable from the
main venv without manim, and story_video.py uses them.
"""

import json
import os
import re
import textwrap
from pathlib import Path

BACKGROUND = "#05070c"
ACCENT = "#2f6fd6"
GOLD = "#e8c547"
GREEN = "#4caf7d"
RED = "#d9534f"

STAGE_TOP = 3.6
STAGE_BOTTOM = -2.3
STAGE_LEFT = -6.4
STAGE_RIGHT = 6.4
STAGE_WIDTH = STAGE_RIGHT - STAGE_LEFT
STAGE_HEIGHT = STAGE_TOP - STAGE_BOTTOM

SCENE_CLASS = "PaperStory"
TIMING_ENV = "SCENE_TIMING_OUT"
SPEC_ENV = "SCENE_SPEC_JSON"

CAPTION_FONT_SIZE = 20
CAPTION_WRAP_WIDTH = 60
CAPTION_MAX_HEIGHT = 1.4
CAPTION_SWAP_SECONDS = 0.25

PER_BEAT_OVERRUN_LIMIT = 0.75
TOTAL_OVERRUN_LIMIT = 3.0

_BEAT_METHOD_RE = re.compile(r"^beat_(\d+)$")


def load_spec() -> dict:
    path = os.environ.get(SPEC_ENV)
    if not path:
        raise RuntimeError(f"{SPEC_ENV} env var not set; story_scene.py is run through agentlab.story_video")
    return json.loads(Path(path).read_text(encoding="utf-8"))


def wrap_text(s: str, width: int = 44) -> str:
    return "\n".join(textwrap.wrap(s, width)) or s


def beat_record(index: int, start: float, end: float, narration_seconds: float) -> dict:
    return {
        "beat": index,
        "start": float(start),
        "end": float(end),
        "narration": float(narration_seconds),
        "overrun": max(0.0, round((end - start) - narration_seconds, 3)),
    }


def overrun_report(
    timing: dict, per_beat_limit: float = PER_BEAT_OVERRUN_LIMIT, total_limit: float = TOTAL_OVERRUN_LIMIT
) -> str | None:
    """None when every beat fits its narration (within the limits); else a
    message the coder can act on, naming each offending beat."""
    beats = timing.get("beats") or []
    bad = [b for b in beats if b.get("overrun", 0.0) > per_beat_limit]
    total = sum(b.get("overrun", 0.0) for b in beats)
    if not bad and total <= total_limit:
        return None
    lines = [f"beat {b['beat']} ran {b['overrun']:.1f} s past its {b['narration']:.1f} s narration" for b in bad]
    if total > total_limit:
        lines.append(f"total overrun {total:.1f} s is above the {total_limit:.1f} s limit")
    return (
        "Beats ran longer than their narration. Shorten run_time values or drop animations "
        "so each beat's animations end at least 0.3 s before its narration ends:\n- "
        + "\n- ".join(lines)
    )


def beat_midpoints(timing: dict) -> list[float]:
    return [(b["start"] + b["end"]) / 2 for b in timing.get("beats") or []]


def beat_lengths(timing: dict) -> list[float]:
    return [b["end"] - b["start"] for b in timing.get("beats") or []]


try:
    from manim import (
        BOLD,
        DOWN,
        WHITE,
        FadeIn,
        FadeOut,
        Scene,
        Text,
        ValueTracker,
    )
    from manim.utils.color import GREY_A, GREY_B, GREY_C, GREY_D

    _MANIM_AVAILABLE = True
except ImportError:  # pragma: no cover - only hit without manim installed
    _MANIM_AVAILABLE = False


if _MANIM_AVAILABLE:

    class StoryScene(Scene):
        """Subclass as PaperStory, define beat_1 .. beat_n, never override
        construct. See the API cheat-sheet in agentlab.scene_code."""

        def construct(self):
            self.camera.background_color = BACKGROUND
            spec = load_spec()
            self.storyboard = spec["storyboard"]
            durations = spec["durations"]
            captions = spec["captions"]
            n = len(self.storyboard["beats"])
            if len(durations) != n or len(captions) != n:
                raise ValueError(f"spec has {len(durations)} durations and {len(captions)} captions for {n} beats")
            self._caption = None
            self._timing: list[dict] = []
            for i in range(n):
                method = getattr(self, f"beat_{i + 1}", None)
                if method is None:
                    raise AttributeError(f"{SCENE_CLASS} is missing beat_{i + 1}")
                start = self.time
                self._swap_caption(captions[i])
                method()
                remaining = durations[i] - (self.time - start)
                if remaining > 0:
                    self.wait(remaining)
                self._timing.append(beat_record(i + 1, start, self.time, durations[i]))
            self._write_timing()

        # -- what the generated code may call ---------------------------------

        def fit(self, mobject, max_w: float | None = None, max_h: float | None = None):
            """Shrink a mobject to the stage (or the given bounds); returns it."""
            max_w = STAGE_WIDTH if max_w is None else max_w
            max_h = STAGE_HEIGHT if max_h is None else max_h
            if mobject.width > max_w:
                mobject.scale_to_fit_width(max_w)
            if mobject.height > max_h:
                mobject.scale_to_fit_height(max_h)
            return mobject

        def label(self, text: str, size: int = 28, color=WHITE, width: int = 44, bold: bool = False):
            """A wrapped, fitted Text. Place it yourself (next_to, move_to)."""
            kwargs = {"weight": BOLD} if bold else {}
            return self.fit(Text(wrap_text(text, width), font_size=size, color=color, line_spacing=1.2, **kwargs))

        def counter(self, start: float, end: float, suffix: str = "", size: int = 44, color=ACCENT, decimals: int = 0):
            """A text number that counts from start to end. Returns (mobject,
            animation): place the mobject, then self.play(animation, run_time=...).
            Call self.freeze(mobject) before any FadeOut/Transform that includes it."""
            tracker = ValueTracker(float(start))

            def render(value: float):
                return Text(f"{value:.{decimals}f}{suffix}", font_size=size, color=color, weight=BOLD)

            mobject = render(float(start))
            mobject.add_updater(lambda m: m.become(render(tracker.get_value()).move_to(m.get_center())))
            return mobject, tracker.animate.set_value(float(end))

        def freeze(self, mobject):
            """Stop a counter updating so later group animations are safe."""
            mobject.clear_updaters()
            return mobject

        def clear_stage(self, run_time: float = 0.4):
            """Fade out everything except the caption."""
            keep = id(self._caption) if self._caption is not None else None
            targets = [m for m in list(self.mobjects) if id(m) != keep]
            for m in targets:
                m.clear_updaters()
            if targets:
                self.play(*[FadeOut(m) for m in targets], run_time=run_time)

        def hold(self, seconds: float):
            """Wait; use it to let a change sink in."""
            self.wait(max(float(seconds), 0.01))

        # -- owned by the base class -------------------------------------------

        def _swap_caption(self, text: str):
            new = self.fit(
                Text(wrap_text(text, CAPTION_WRAP_WIDTH), font_size=CAPTION_FONT_SIZE, color=WHITE, line_spacing=1.15),
                max_w=STAGE_WIDTH,
                max_h=CAPTION_MAX_HEIGHT,
            )
            new.to_edge(DOWN, buff=0.3)
            anims = [FadeIn(new)]
            if self._caption is not None:
                anims.append(FadeOut(self._caption))
            self.play(*anims, run_time=CAPTION_SWAP_SECONDS)
            self._caption = new

        def _write_timing(self):
            path = os.environ.get(TIMING_ENV)
            if not path:
                return
            Path(path).write_text(
                json.dumps({"beats": self._timing, "total": float(self.time)}, indent=1), encoding="utf-8"
            )

```

`GREY_A` to `GREY_D` and `WHITE` are module attributes once the manim import succeeds, so generated code can write `from story_scene import StoryScene, ACCENT, GOLD, GREEN, RED, GREY_B, WHITE`. No `__all__` is needed; ruff may flag the GREY names as unused imports, so add `# noqa: F401` on that import line with a comment saying they are re-exported for generated scenes.

- [ ] **Step 4: Write the golden scene fixture**

`tests/fixtures/paper_story_golden.py` (must obey every guard rule from Task 4; keep every beat's animations under its duration minus 0.3 s for the durations used in the render test: 6, 7, 7, 6, 8):

```python
"""Golden generated scene: the compaction house move, written by hand to
the StoryScene contract. Used by the render test and by scene_code's guard
test as the canonical clean example."""

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
        # y = -1.9: inside the stage, above the caption band at STAGE_BOTTOM.
        question.move_to(self.case.get_center() + DOWN * 2.2)
        self.fit(question, max_w=6.0)
        self.play(FadeIn(question), self.case.animate.set_stroke(ACCENT, width=4), run_time=1.4)
        self.hold(0.6)
```

- [ ] **Step 5: Run the offline tests, then the render test**

Run: `uv run pytest tests/test_story_scene.py -q`
Expected: all offline tests pass.

Run: `uv run pytest -m render tests/test_story_scene.py -q`
Expected: pass; takes a minute or two on the laptop (uvx manim). If a beat overruns, shorten that beat's `run_time` values in the fixture, not the limits. If manim reports a bad import, fix the fixture's import list (only names that exist in Manim 0.21).

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/agentlab/story_scene.py tests/test_story_scene.py tests/fixtures/paper_story_golden.py
git commit -m "feat: StoryScene base class - timing, captions, palette for generated scenes"
```

---

### Task 4: scene_code.py, the coder prompt and the guard

**Files:**
- Create: `src/agentlab/scene_code.py`
- Test: `tests/test_scene_code.py`

**Interfaces:**
- Consumes: `Storyboard` (Task 2, only for `.model_dump()` and `.beats`); `story_scene.py`'s file path (`agentlab.story_scene.__file__`) and `SCENE_CLASS`.
- Produces: `check_scene_code(source: str, beat_count: int) -> list[str]`, `write_scene_code(storyboard, durations, complete, model, feedback=None, previous_source=None) -> str`, `extract_python_block(raw) -> str`, `build_scene_code_prompt(storyboard, durations, feedback, previous_source) -> str`, `SCENE_CODE_SYSTEM`, `STORY_SCENE_API`, `ALLOWED_IMPORTS`, `FORBIDDEN_NAMES`.

- [ ] **Step 1: Write the failing tests**

`tests/test_scene_code.py`:

```python
import ast
import json
from pathlib import Path

import pytest

from agentlab import scene_code, story_scene
from agentlab.storyboard import Storyboard

GOLDEN_SCENE = (Path(__file__).parent / "fixtures" / "paper_story_golden.py").read_text(encoding="utf-8")
GOLDEN_BOARD = json.loads((Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(encoding="utf-8"))
BEATS = len(GOLDEN_BOARD["beats"])


def test_golden_scene_passes_the_guard():
    assert scene_code.check_scene_code(GOLDEN_SCENE, BEATS) == []


@pytest.mark.parametrize(
    "snippet, needle",
    [
        ("import os\n", "os"),
        ("from pathlib import Path\n", "pathlib"),
        ("import requests\n", "requests"),
        ("x = open('/etc/passwd')\n", "open"),
        ("y = __import__('os')\n", "__import__"),
        ("z = ().__class__.__mro__\n", "__class__"),
        ("from manim import MathTex\n", "MathTex"),
        ("from manim import DecimalNumber\n", "DecimalNumber"),
        ("from manim import BarChart\n", "BarChart"),
        ("w = self.camera.frame\n", "camera"),
    ],
)
def test_guard_flags_forbidden_things(snippet, needle):
    source = snippet + GOLDEN_SCENE
    findings = scene_code.check_scene_code(source, BEATS)
    assert any(needle in f for f in findings), findings


def test_guard_flags_include_numbers_true():
    source = GOLDEN_SCENE.replace(
        "self.hold(0.6)", "self.axes = Axes(x_range=[0, 1], axis_config={'include_numbers': True}); self.hold(0.6)"
    )
    source = source.replace("from manim import (", "from manim import (\n    Axes,")
    findings = scene_code.check_scene_code(source, BEATS)
    assert any("include_numbers" in f for f in findings)


def test_guard_requires_every_beat_method_and_no_extras():
    missing = GOLDEN_SCENE.replace("def beat_5(self):", "def beat_9(self):")
    findings = scene_code.check_scene_code(missing, BEATS)
    assert any("beat_5" in f for f in findings)
    assert any("beat_9" in f for f in findings)


def test_guard_rejects_construct_override_and_wrong_class():
    with_construct = GOLDEN_SCENE + "\n    def construct(self):\n        pass\n"
    assert any("construct" in f for f in scene_code.check_scene_code(with_construct, BEATS))
    renamed = GOLDEN_SCENE.replace("class PaperStory(StoryScene):", "class Other(StoryScene):")
    assert any("PaperStory" in f for f in scene_code.check_scene_code(renamed, BEATS))


def test_guard_reports_syntax_errors():
    findings = scene_code.check_scene_code("def (:\n", BEATS)
    assert findings and "syntax" in findings[0]


def test_extract_python_block_takes_the_last_fenced_block():
    raw = "thinking...\n```python\nx = 1\n```\nfix:\n```python\nclass PaperStory: pass\n```\n"
    assert scene_code.extract_python_block(raw) == "class PaperStory: pass"


def test_extract_python_block_falls_back_to_raw_when_unfenced():
    raw = "from story_scene import StoryScene\nclass PaperStory(StoryScene):\n    pass\n"
    assert scene_code.extract_python_block(raw) == raw.strip()


def test_cheat_sheet_names_every_public_story_scene_method():
    tree = ast.parse(Path(story_scene.__file__).read_text(encoding="utf-8"))
    cls = next(n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "StoryScene")
    public = {n.name for n in cls.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_") and n.name != "construct"}
    assert public == {"fit", "label", "counter", "freeze", "clear_stage", "hold"}
    for name in public:
        assert f"self.{name}(" in scene_code.STORY_SCENE_API


def test_prompt_lists_each_beat_with_its_duration_and_budget():
    board = Storyboard(**GOLDEN_BOARD)
    prompt = scene_code.build_scene_code_prompt(board, [6.0, 7.0, 7.0, 6.0, 8.0], None, None)
    assert "beat_1" in prompt and "6.0 s" in prompt and "5.7 s" in prompt
    assert board.beats[0].visual in prompt


def test_write_scene_code_fix_round_includes_previous_source_and_feedback():
    board = Storyboard(**GOLDEN_BOARD)
    seen = []

    def complete(model, messages):
        seen.append(messages)
        return "```python\n" + GOLDEN_SCENE + "\n```"

    source = scene_code.write_scene_code(
        board, [6.0] * BEATS, complete, model="m", feedback="beat 2 ran 1.2 s over", previous_source="OLD SOURCE"
    )
    assert source == GOLDEN_SCENE.strip()
    user = seen[0][-1]["content"]
    assert "OLD SOURCE" in user and "beat 2 ran 1.2 s over" in user
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_scene_code.py -q`
Expected: `ModuleNotFoundError: agentlab.scene_code`.

- [ ] **Step 3: Implement**

```python
"""Scene code: the coder prompt and the guard for model-written Manim
scenes (docs/specs/2026-09-05-metaphor-videos.md, section 3).

The guard is an AST allowlist, not a sandbox: it keeps honest generated
code inside manim + a few pure stdlib modules, and refuses everything that
needs LaTeX (the video image has none) or touches files, processes, the
network, or Python's introspection escape hatches. The render itself runs
in a credential-free, time-limited subprocess (video_render.render_env),
which is the real containment; the guard is there so a bad file fails in
milliseconds with a message the model can act on, instead of minutes into
a render.
"""

import ast
import re
from collections.abc import Callable

from agentlab.storyboard import Storyboard

SCENE_CLASS = "PaperStory"
BASE_CLASS = "StoryScene"
MAX_TOKENS = 8000
BEAT_MARGIN_SECONDS = 0.3

ALLOWED_IMPORTS = frozenset(
    {"manim", "story_scene", "math", "random", "itertools", "functools", "numpy", "dataclasses", "typing", "colorsys"}
)

FORBIDDEN_NAMES = frozenset(
    {
        # files, processes, network, introspection
        "open", "exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr",
        "delattr", "vars", "breakpoint", "input", "os", "sys", "subprocess", "socket", "pathlib",
        "shutil", "importlib", "builtins", "__builtins__", "__subclasses__", "__globals__", "__dict__",
        "__class__", "__mro__", "camera",
        # anything that needs LaTeX (the video image has none)
        "Tex", "MathTex", "SingleStringMathTex", "DecimalNumber", "Integer", "Variable", "Title",
        "BulletedList", "Matrix", "IntegerMatrix", "DecimalMatrix", "MobjectMatrix", "Table", "MathTable",
        "IntegerTable", "DecimalTable", "MobjectTable", "BarChart", "TransformMatchingTex",
        "get_axis_labels", "get_x_axis_label", "get_y_axis_label", "add_coordinates", "get_text", "get_tex",
        # media and files
        "ImageMobject", "SVGMobject", "Code", "add_sound", "interactive_embed",
    }
)

_BEAT_RE = re.compile(r"^beat_(\d+)$")


def check_scene_code(source: str, beat_count: int) -> list[str]:
    """Findings (empty means clean). Order preserved, duplicates removed."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: line {exc.lineno}: {exc.msg}"]
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in ALLOWED_IMPORTS:
                    findings.append(f"import not allowed: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".")[0]
            if node.level or root not in ALLOWED_IMPORTS:
                findings.append(f"import not allowed: from {node.module or '.'}")
            for alias in node.names:
                if alias.name in FORBIDDEN_NAMES:
                    findings.append(f"forbidden name imported: {alias.name}")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            findings.append(f"forbidden name: {node.id}")
        elif isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            findings.append(f"forbidden attribute: {node.attr}")
        elif (
            isinstance(node, ast.keyword)
            and node.arg == "include_numbers"
            and isinstance(node.value, ast.Constant)
            and node.value.value is True
        ):
            findings.append("include_numbers=True needs LaTeX, which the render image does not have")
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=True):
                if (
                    isinstance(key, ast.Constant) and key.value == "include_numbers"
                    and isinstance(value, ast.Constant) and value.value is True
                ):
                    findings.append("include_numbers=True needs LaTeX, which the render image does not have")
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == SCENE_CLASS]
    if len(classes) != 1:
        findings.append(f"exactly one top-level class named {SCENE_CLASS} is required")
        return _dedup(findings)
    cls = classes[0]
    bases = [b.id if isinstance(b, ast.Name) else b.attr if isinstance(b, ast.Attribute) else "" for b in cls.bases]
    if BASE_CLASS not in bases:
        findings.append(f"{SCENE_CLASS} must subclass {BASE_CLASS}")
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    if "construct" in methods:
        findings.append(f"{SCENE_CLASS} must not override construct; the base class owns it")
    for k in range(1, beat_count + 1):
        if f"beat_{k}" not in methods:
            findings.append(f"missing method beat_{k} (the storyboard has {beat_count} beats)")
    for name in sorted(methods):
        match = _BEAT_RE.match(name)
        if match and int(match.group(1)) > beat_count:
            findings.append(f"unexpected method {name}: the storyboard has only {beat_count} beats")
    return _dedup(findings)


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


STORY_SCENE_API = """The base class (already written, do not redefine it) gives you:

Constants (import them from story_scene): BACKGROUND, ACCENT (#2f6fd6, the one accent \
colour), GOLD, GREEN, RED, GREY_A, GREY_B, GREY_C, GREY_D, WHITE, STAGE_TOP (3.6), \
STAGE_BOTTOM (-2.3), STAGE_LEFT (-6.4), STAGE_RIGHT (6.4).

Methods:
- self.fit(mobject, max_w=None, max_h=None): shrink to the stage or the given bounds; returns the mobject. Call it on every text block and every group before placing it.
- self.label(text, size=28, color=WHITE, width=44, bold=False): a wrapped, fitted Text. Place it with next_to / move_to / to_edge.
- self.counter(start, end, suffix="", size=44, color=ACCENT, decimals=0): returns (mobject, animation). Place the mobject, then self.play(animation, run_time=...) to count it up. Call self.freeze(mobject) afterwards, before any FadeOut or Transform that includes it.
- self.freeze(mobject): stop a counter updating.
- self.clear_stage(run_time=0.4): fade out everything except the caption. Use it when the metaphor changes view.
- self.hold(seconds): wait, to let a change sink in.

The base class already: sets the dark background, draws the caption for each beat in the bottom band, and pads each beat so it lasts exactly its narration. You only write beat_1 .. beat_n."""


SCENE_CODE_SYSTEM = """You write one Manim Community v0.21 scene file that animates a \
storyboard for a short paper-explainer video. The storyboard names one metaphor and a \
list of beats; each beat has narration (already recorded, its duration is given) and a \
visual description. Your job is to draw exactly that visual, beat by beat, with clean \
motion, in the style of 3Blue1Brown: simple shapes, one accent colour, the change on the \
object is the explanation.

Contract:
- File starts with `from manim import (...)` naming only what you use, then `from \
story_scene import StoryScene` plus any constants you use from it.
- Exactly one class, `class PaperStory(StoryScene):`. Do not override construct.
- One method per beat, beat_1 to beat_n, n equal to the storyboard's beat count.
- Each beat's animations (the sum of run_time values plus any self.hold) must end at \
least 0.3 s before that beat's narration ends. The budget per beat is listed below. The \
base class pads the rest.
- Objects that persist across beats live on self (self.house, self.case, ...). Later \
beats move, recolour, or transform them; that continuity is the whole point.
- Everything stays inside the stage: x from -6.4 to 6.4, y from -2.3 to 3.6. The band \
below y = -2.3 is the caption's; never draw there. Call self.fit on every text block and \
every group.
- No LaTeX: never Tex, MathTex, DecimalNumber, Integer, Title, Variable, Matrix, Table, \
BarChart, axis labels, include_numbers=True. Numbers are Text or self.counter(...).
- No camera moves (plain Scene): zoom by scaling a group. No emoji, no images, no \
files, no network, no custom fonts, no sound.
- Only these imports: manim, story_scene, math, random, itertools, functools, numpy, \
dataclasses, typing, colorsys.
- Use real Manim vocabulary: Create for shapes, Write or FadeIn for text, \
.animate.move_to / .set_fill / .scale for state changes, Transform / ReplacementTransform \
/ FadeTransform for one thing becoming another, Indicate / Circumscribe / Flash for \
emphasis, MoveAlongPath for travel, VGroup + arrange / arrange_in_grid for layout, \
always with explicit run_time.
- Text sizes: 20 to 30 for labels, 36 to 44 for one headline number. Never more than \
about 12 words on screen at once besides the caption.
- Prefer showing the change over labelling it: a bar growing to 44% beats the words \
"44% improvement".

Answer with exactly one fenced ```python block containing the whole file and nothing \
else outside it."""


def build_scene_code_prompt(
    storyboard: Storyboard, durations: list[float], feedback: str | None, previous_source: str | None
) -> str:
    beat_lines = []
    for i, (beat, seconds) in enumerate(zip(storyboard.beats, durations, strict=True), start=1):
        budget = max(seconds - BEAT_MARGIN_SECONDS, 0.5)
        labels = f" On-screen text: {', '.join(beat.on_screen_text)}." if beat.on_screen_text else ""
        beat_lines.append(
            f"beat_{i}: narration lasts {seconds:.1f} s, so your animations must total at most "
            f"{budget:.1f} s.\n  Narration: {beat.narration}\n  Visual: {beat.visual}{labels}"
        )
    prompt = (
        f"Metaphor: {storyboard.metaphor}\nWhy: {storyboard.why_this_metaphor}\n"
        "Mapping:\n"
        + "\n".join(f"- {m.paper_term} = {m.visual}" for m in storyboard.mapping)
        + "\n\nBeats:\n"
        + "\n".join(beat_lines)
        + "\n\n"
        + STORY_SCENE_API
    )
    if feedback:
        prompt += (
            "\n\nThis is a fix round. Your previous file is below, followed by what went wrong. "
            "Return the FULL corrected file, changing only what the feedback needs.\n\n"
            f"<previous_file>\n{previous_source or ''}\n</previous_file>\n\n"
            f"<what_went_wrong>\n{feedback}\n</what_went_wrong>"
        )
    prompt += "\n\nWrite the complete file now, in one fenced python block."
    return prompt


_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.DOTALL)


def extract_python_block(raw: str) -> str:
    blocks = _FENCE_RE.findall(raw or "")
    if blocks:
        return blocks[-1].strip()
    return (raw or "").strip()


def write_scene_code(
    storyboard: Storyboard,
    durations: list[float],
    complete: Callable[[str, list[dict]], str],
    model: str,
    feedback: str | None = None,
    previous_source: str | None = None,
) -> str:
    messages = [
        {"role": "system", "content": SCENE_CODE_SYSTEM},
        {"role": "user", "content": build_scene_code_prompt(storyboard, durations, feedback, previous_source)},
    ]
    return extract_python_block(complete(model, messages))
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_scene_code.py -q`
Expected: all pass. If `test_golden_scene_passes_the_guard` fails, the golden fixture (Task 3) uses a forbidden name; fix the fixture, not the guard.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/agentlab/scene_code.py tests/test_scene_code.py
git commit -m "feat: scene code - coder prompt, API cheat-sheet, AST guard"
```

---

### Task 5: frame_judge.py

**Files:**
- Create: `src/agentlab/frame_judge.py`
- Test: `tests/test_frame_judge.py`

**Interfaces:**
- Consumes: `video_render.run_subprocess` (the ffmpeg seam), `Storyboard`, `scene_plan.extract_json_object`.
- Produces: `sample_frames(video_path, times: list[float], out_dir) -> list[Path]`, `judge_frames(frames, storyboard, complete, model) -> Judgement`, `Judgement`, `BeatJudgement`, `verdict_from_beats(beats) -> str`, `parse_judgement(raw) -> tuple[Judgement | None, str]`, `judgement_feedback(judgement) -> str`, `build_judge_messages(frames, storyboard) -> list[dict]`, `FALLBACK_SCORE = 5`.

- [ ] **Step 1: Write the failing tests**

`tests/test_frame_judge.py`:

```python
import json
import subprocess
from pathlib import Path

from agentlab import frame_judge, video_render
from agentlab.storyboard import Storyboard

BOARD = Storyboard(**json.loads((Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(encoding="utf-8")))


def _beats(**overrides):
    base = [{"beat": i, "shows_visual": True, "legible": True, "clean": True, "issue": None} for i in range(1, 6)]
    for idx, fields in overrides.items():
        base[int(idx) - 1].update(fields)
    return base


def test_verdict_pass_when_all_clean_and_visible():
    assert frame_judge.verdict_from_beats([frame_judge.BeatJudgement(**b) for b in _beats()]) == "pass"


def test_verdict_fix_on_any_unclean_or_illegible_beat():
    beats = [frame_judge.BeatJudgement(**b) for b in _beats(**{"3": {"clean": False, "issue": "text overlaps bar"}})]
    assert frame_judge.verdict_from_beats(beats) == "fix"
    beats = [frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"legible": False}})]
    assert frame_judge.verdict_from_beats(beats) == "fix"


def test_verdict_tolerates_one_missing_visual_but_not_two():
    one = [frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"shows_visual": False}})]
    assert frame_judge.verdict_from_beats(one) == "pass"
    two = [frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"shows_visual": False}, "4": {"shows_visual": False}})]
    assert frame_judge.verdict_from_beats(two) == "fix"


def test_parse_judgement_recomputes_verdict_and_clamps_score():
    raw = "```json\n" + json.dumps({"beats": _beats(**{"1": {"clean": False}}), "score": 14, "verdict": "pass"}) + "\n```"
    judgement, error = frame_judge.parse_judgement(raw)
    assert error == ""
    assert judgement.score == 10
    assert judgement.verdict == "fix"


def test_judge_frames_retries_bad_json_then_falls_back_to_pass(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")
    replies = iter(["garbage", "still garbage"])
    judgement = frame_judge.judge_frames([frame] * 5, BOARD, lambda model, messages: next(replies), model="m")
    assert judgement.verdict == "pass"
    assert judgement.score == frame_judge.FALLBACK_SCORE
    assert "judge unavailable" in judgement.note


def test_judge_frames_survives_a_model_exception(tmp_path):
    frame = tmp_path / "f.png"
    frame.write_bytes(b"\x89PNG fake")

    def boom(model, messages):
        raise RuntimeError("bedrock down")

    judgement = frame_judge.judge_frames([frame] * 5, BOARD, boom, model="m")
    assert judgement.verdict == "pass" and judgement.score == frame_judge.FALLBACK_SCORE


def test_build_judge_messages_pairs_each_frame_with_its_beat(tmp_path):
    frames = []
    for i in range(5):
        p = tmp_path / f"frame_{i}.png"
        p.write_bytes(b"png" + bytes([i]))
        frames.append(p)
    messages = frame_judge.build_judge_messages(frames, BOARD)
    content = messages[-1]["content"]
    images = [part for part in content if part.get("type") == "image_url"]
    texts = " ".join(part["text"] for part in content if part.get("type") == "text")
    assert len(images) == 5
    assert all(part["image_url"]["url"].startswith("data:image/png;base64,") for part in images)
    for beat in BOARD.beats:
        assert beat.visual in texts


def test_sample_frames_calls_ffmpeg_once_per_time(monkeypatch, tmp_path):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"png")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(video_render, "run_subprocess", fake_run)
    frames = frame_judge.sample_frames(tmp_path / "v.mp4", [1.5, 4.25], tmp_path / "frames")
    assert len(frames) == 2 and all(f.exists() for f in frames)
    assert calls[0][calls[0].index("-ss") + 1] == "1.500"
    assert "scale=960:-1" in " ".join(calls[1])


def test_judgement_feedback_lists_only_beats_with_problems():
    judgement = frame_judge.Judgement(
        beats=[frame_judge.BeatJudgement(**b) for b in _beats(**{"2": {"clean": False, "issue": "label off the right edge"}})],
        score=6,
    )
    text = frame_judge.judgement_feedback(judgement)
    assert "beat 2" in text and "label off the right edge" in text
    assert "beat 1" not in text
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_frame_judge.py -q`
Expected: `ModuleNotFoundError: agentlab.frame_judge`.

- [ ] **Step 3: Implement**

```python
"""Frame judge: one vision call checks sampled frames of a rendered story
video against its storyboard (docs/specs/2026-09-05-metaphor-videos.md,
section 6). The verdict is computed in code from the per-beat fields, never
trusted from the model. A judge outage is never fatal: the attempt is
treated as a pass with FALLBACK_SCORE and the reason is kept in `note`.

Images travel as OpenAI-style content parts (image_url with a base64 data
URL); litellm maps that to Bedrock Converse image blocks.
"""

import base64
import json
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from agentlab import video_render
from agentlab.scene_plan import extract_json_object
from agentlab.storyboard import Storyboard

FALLBACK_SCORE = 5
FRAME_WIDTH = 960
MAX_MISSING_VISUALS = 1


class BeatJudgement(BaseModel):
    beat: int
    shows_visual: bool
    legible: bool
    clean: bool
    issue: str | None = None


class Judgement(BaseModel):
    beats: list[BeatJudgement]
    score: int = Field(ge=0, le=10)
    verdict: Literal["pass", "fix"] = "pass"
    note: str | None = None


def verdict_from_beats(beats: list[BeatJudgement]) -> str:
    if any(not b.clean or not b.legible for b in beats):
        return "fix"
    if sum(1 for b in beats if not b.shows_visual) > MAX_MISSING_VISUALS:
        return "fix"
    return "pass"


def parse_judgement(raw: str) -> tuple[Judgement | None, str]:
    try:
        data = json.loads(extract_json_object(raw))
    except json.JSONDecodeError as exc:
        return None, f"JSON parse error: {exc}"
    if not isinstance(data, dict):
        return None, "judgement must be a JSON object"
    score = data.get("score", FALLBACK_SCORE)
    try:
        score = int(round(float(score)))
    except (TypeError, ValueError):
        score = FALLBACK_SCORE
    data["score"] = min(10, max(0, score))
    data.pop("verdict", None)
    try:
        judgement = Judgement(**data)
    except ValidationError as exc:
        return None, str(exc)
    judgement.verdict = verdict_from_beats(judgement.beats)
    return judgement, ""


def sample_frames(video_path, times: list[float], out_dir) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    frames = []
    for i, t in enumerate(times, start=1):
        out = out_dir / f"frame_{i:02d}.png"
        video_render.run_subprocess(
            ["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video_path),
             "-frames:v", "1", "-vf", f"scale={FRAME_WIDTH}:-1", str(out)]
        )
        frames.append(out)
    return frames


JUDGE_SYSTEM = """You check rendered frames of a short explainer video against its \
storyboard. Each frame is the middle of one beat. For each frame decide: shows_visual \
(does the frame show what the beat's visual description says should be on screen at \
that point, roughly), legible (is every piece of text readable at this size, nothing \
tiny or garbled), clean (nothing overlaps another element, nothing is cut off at the \
frame edge, nothing is drawn over the caption text in the bottom band). Put a short \
concrete issue when something is wrong, else null. Then give an overall score from 0 \
to 10 for how well the frames tell the storyboard's story. Be strict about overlap and \
cut-off elements, lenient about artistic interpretation. Answer with ONLY a fenced json \
object: {"beats": [{"beat": 1, "shows_visual": true, "legible": true, "clean": true, \
"issue": null}, ...], "score": 7}."""


def _image_part(path: Path) -> dict:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{encoded}"}}


def build_judge_messages(frames: list[Path], storyboard: Storyboard) -> list[dict]:
    content: list[dict] = [{"type": "text", "text": f"Metaphor: {storyboard.metaphor}"}]
    for i, (frame, beat) in enumerate(zip(frames, storyboard.beats, strict=False), start=1):
        content.append(
            {"type": "text", "text": f"Beat {i}. Narration: {beat.narration}\nVisual: {beat.visual}"}
        )
        content.append(_image_part(Path(frame)))
    content.append({"type": "text", "text": "Judge every beat above. Fenced json only."})
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": content}]


def judge_frames(
    frames: list[Path], storyboard: Storyboard, complete: Callable[[str, list[dict]], str], model: str
) -> Judgement:
    messages = build_judge_messages(frames, storyboard)
    error = ""
    for _attempt in range(2):
        try:
            raw = complete(model, messages)
        except Exception as exc:  # noqa: BLE001 - a judge outage must never cost a video
            error = f"{type(exc).__name__}: {exc}"
            continue
        judgement, error = parse_judgement(raw)
        if judgement is not None:
            return judgement
    return Judgement(beats=[], score=FALLBACK_SCORE, verdict="pass", note=f"judge unavailable: {error}")


def judgement_feedback(judgement: Judgement) -> str:
    lines = []
    for b in judgement.beats:
        problems = []
        if not b.clean:
            problems.append("overlap or cut off")
        if not b.legible:
            problems.append("text not legible")
        if not b.shows_visual:
            problems.append("does not show the described visual")
        if problems:
            detail = f" ({b.issue})" if b.issue else ""
            lines.append(f"beat {b.beat}: {', '.join(problems)}{detail}")
    if not lines:
        return "The frame judge found no specific problems."
    return "The frame judge flagged these beats; fix only these:\n- " + "\n- ".join(lines)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_frame_judge.py -q`
Expected: all pass.

- [ ] **Step 5: Lint and commit**

```bash
uv run ruff check src tests && uv run ruff format src tests
git add src/agentlab/frame_judge.py tests/test_frame_judge.py
git commit -m "feat: frame judge - vision check of sampled frames against the storyboard"
```

---

### Task 6: story_video.py, the compose loop, plus the local runner script

**Files:**
- Create: `src/agentlab/story_video.py`
- Create: `scripts/story_video_for_url.py`
- Test: `tests/test_story_video.py`

**Interfaces:**
- Consumes (exact names from Tasks 1 to 5): `storyboard.design_storyboard`, `storyboard.StoryboardInvalid`, `scene_code.write_scene_code`, `scene_code.check_scene_code`, `frame_judge.sample_frames`, `frame_judge.judge_frames`, `frame_judge.judgement_feedback`, `story_scene.overrun_report`, `story_scene.beat_midpoints`, `story_scene.beat_lengths`, `story_scene.SCENE_CLASS`, `story_scene.TIMING_ENV`, `video_render.narrate`, `video_render.render_scene_video`, `video_render.concat_audio`, `video_render.mux_final`, `video_render.build_srt`.
- Produces: `compose_story_video(digest, plan, polly_client, voice_id, complete, work_dir, out_path, story_model, scene_model, judge_model, max_attempts=3) -> StoryResult`, `StoryResult`, `StoryFailed`, `LOW_RENDER_TIMEOUT = 480`, `FINAL_RENDER_TIMEOUT = 900`, `MAX_ATTEMPTS = 3`.
- Every external step is called through a module-level name in `story_video` (`design_storyboard`, `narrate`, `write_scene_code`, `check_scene_code`, `render_scene_video`, `sample_frames`, `judge_frames`, `concat_audio`, `mux_final`) so tests monkeypatch `agentlab.story_video.<name>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_story_video.py`:

```python
import json
import subprocess
from pathlib import Path

import pytest

from agentlab import story_video
from agentlab.frame_judge import BeatJudgement, Judgement
from agentlab.scene_plan import ScenePlan
from agentlab.storyboard import Storyboard, StoryboardInvalid
from agentlab.video_render import NarrationClip

FIXTURES = Path(__file__).parent / "fixtures"
BOARD = Storyboard(**json.loads((FIXTURES / "storyboard_golden.json").read_text(encoding="utf-8")))
PLAN = ScenePlan(**json.loads((FIXTURES / "sample_plan.json").read_text(encoding="utf-8")))
GOLDEN_SCENE = (FIXTURES / "paper_story_golden.py").read_text(encoding="utf-8")
DIGEST = "# Digest\n0% and 44% on 50 items.\n## Limits\nNone."
N = len(BOARD.beats)


def _timing(overruns=None):
    overruns = overruns or [0.0] * N
    beats, cursor = [], 0.0
    for i, over in enumerate(overruns, start=1):
        length = 5.0 + over
        beats.append({"beat": i, "start": cursor, "end": cursor + length, "narration": 5.0, "overrun": over})
        cursor += length
    return {"beats": beats, "total": cursor}


def _judgement(score=8, fix_beat=None):
    beats = [BeatJudgement(beat=i, shows_visual=True, legible=True, clean=True) for i in range(1, N + 1)]
    if fix_beat:
        beats[fix_beat - 1] = BeatJudgement(beat=fix_beat, shows_visual=True, legible=True, clean=False, issue="overlap")
    j = Judgement(beats=beats, score=score)
    j.verdict = "fix" if fix_beat else "pass"
    return j


@pytest.fixture
def seams(monkeypatch, tmp_path):
    """Fake every external step; each fake records calls and can be steered."""
    state = {"renders": [], "codes": [], "judgements": [], "render_fail": [], "timings": [], "coder": []}

    monkeypatch.setattr(story_video, "design_storyboard", lambda digest, plan, complete, model: BOARD)

    def fake_narrate(polly, texts, voice, out_dir):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        clips = []
        for i, t in enumerate(texts):
            p = Path(out_dir) / f"clip_{i:02d}.mp3"
            p.write_bytes(b"mp3")
            clips.append(NarrationClip(path=p, seconds=5.0, text=t))
        return clips

    monkeypatch.setattr(story_video, "narrate", fake_narrate)

    def fake_coder(storyboard, durations, complete, model, feedback=None, previous_source=None):
        state["coder"].append(feedback)
        return state["codes"].pop(0) if state["codes"] else GOLDEN_SCENE

    monkeypatch.setattr(story_video, "write_scene_code", fake_coder)

    def fake_render(scene_file, scene_class, spec, out_dir, quality="l", timeout_seconds=0, extra_env=None):
        state["renders"].append((quality, timeout_seconds))
        if state["render_fail"] and state["render_fail"].pop(0):
            raise subprocess.CalledProcessError(1, ["manim"], stderr="Traceback...\nNameError: x")
        timing = state["timings"].pop(0) if state["timings"] else _timing()
        Path(extra_env["SCENE_TIMING_OUT"]).write_text(json.dumps(timing), encoding="utf-8")
        out = Path(out_dir) / "videos" / "x" / "480p15" / f"{scene_class}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"video")
        return out

    monkeypatch.setattr(story_video, "render_scene_video", fake_render)
    monkeypatch.setattr(story_video, "sample_frames", lambda video, times, out_dir: [Path(out_dir) / f"{i}.png" for i in range(len(times))])
    monkeypatch.setattr(
        story_video, "judge_frames",
        lambda frames, storyboard, complete, model: state["judgements"].pop(0) if state["judgements"] else _judgement(),
    )
    monkeypatch.setattr(
        story_video, "concat_audio",
        lambda clips, out, target_seconds=None: state.__setitem__("targets", target_seconds) or Path(out),
    )
    monkeypatch.setattr(story_video, "mux_final", lambda video, audio, out: Path(out).write_bytes(b"final") or Path(out))
    return state


def _compose(tmp_path):
    return story_video.compose_story_video(
        DIGEST, PLAN, polly_client=None, voice_id="Ivy", complete=lambda m, msgs: "",
        work_dir=tmp_path / "work", out_path=tmp_path / "out" / "video.mp4",
        story_model="s", scene_model="c", judge_model="j",
    )


def test_happy_path_one_attempt_then_final_render(seams, tmp_path):
    result = _compose(tmp_path)
    assert result.video_path.read_bytes() == b"final"
    assert result.attempts == 1
    assert result.judge_score == 8
    assert [q for q, _ in seams["renders"]] == ["l", "m"]
    assert seams["renders"][0][1] == story_video.LOW_RENDER_TIMEOUT
    assert seams["renders"][1][1] == story_video.FINAL_RENDER_TIMEOUT
    assert seams["targets"] == [5.0] * N
    assert result.srt_path.exists()
    assert "class PaperStory" in result.scene_source


def test_guard_finding_goes_back_to_the_coder(seams, tmp_path):
    seams["codes"] = ["import os\n" + GOLDEN_SCENE]
    result = _compose(tmp_path)
    assert result.attempts == 2
    assert "import not allowed: os" in seams["coder"][1]
    assert len([q for q, _ in seams["renders"] if q == "l"]) == 1


def test_render_traceback_goes_back_to_the_coder(seams, tmp_path):
    seams["render_fail"] = [True]
    result = _compose(tmp_path)
    assert result.attempts == 2
    assert "NameError" in seams["coder"][1]


def test_overrun_goes_back_to_the_coder(seams, tmp_path):
    seams["timings"] = [_timing([0.0, 2.0, 0.0, 0.0, 0.0])]
    result = _compose(tmp_path)
    assert result.attempts == 2
    assert "beat 2" in seams["coder"][1]


def test_judge_fix_retries_and_best_score_ships(seams, tmp_path):
    seams["judgements"] = [_judgement(score=7, fix_beat=2), _judgement(score=4, fix_beat=3), _judgement(score=6, fix_beat=1)]
    result = _compose(tmp_path)
    assert result.attempts == 3
    assert result.judge_score == 7  # attempt 1 was best, ships despite later attempts
    assert "beat 2" in seams["coder"][1]


def test_three_failures_raise_story_failed(seams, tmp_path):
    seams["render_fail"] = [True, True, True]
    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path)
    assert "3 attempts" in str(exc.value)


def test_storyboard_invalid_becomes_story_failed(seams, monkeypatch, tmp_path):
    def bad(digest, plan, complete, model):
        raise StoryboardInvalid("no metaphor")

    monkeypatch.setattr(story_video, "design_storyboard", bad)
    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path)
    assert "storyboard" in str(exc.value)


def test_final_render_failure_falls_back_to_the_low_quality_render(seams, tmp_path):
    seams["render_fail"] = [False, True]
    result = _compose(tmp_path)
    assert result.video_path.read_bytes() == b"final"
    assert result.attempts == 1


def test_unexpected_exception_becomes_story_failed(seams, monkeypatch, tmp_path):
    monkeypatch.setattr(story_video, "sample_frames", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(story_video.StoryFailed) as exc:
        _compose(tmp_path)
    assert "OSError" in str(exc.value)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_story_video.py -q`
Expected: `ModuleNotFoundError: agentlab.story_video`.

- [ ] **Step 3: Implement story_video.py**

```python
"""Compose a metaphor video: storyboard, narration, generated scene,
guarded render, judge, best candidate, final render, mux
(docs/specs/2026-09-05-metaphor-videos.md, section 7).

Every external step is a module-level name (design_storyboard, narrate,
write_scene_code, check_scene_code, render_scene_video, sample_frames,
judge_frames, concat_audio, mux_final) so tests monkeypatch
agentlab.story_video.<name>, the same seam style as worker.py.

Anything that goes wrong here becomes StoryFailed; the worker catches only
that and falls back to the template video, so a bug in this path can never
cost the day's video.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from agentlab import story_scene
from agentlab.frame_judge import judge_frames, judgement_feedback, sample_frames
from agentlab.scene_code import check_scene_code, write_scene_code
from agentlab.scene_plan import ScenePlan
from agentlab.story_scene import SCENE_CLASS, TIMING_ENV, beat_lengths, beat_midpoints, overrun_report
from agentlab.storyboard import Storyboard, StoryboardInvalid, design_storyboard
from agentlab.video_render import build_srt, concat_audio, mux_final, narrate, render_scene_video

STORY_SCENE_FILE = Path(story_scene.__file__)
LOW_RENDER_TIMEOUT = 480
FINAL_RENDER_TIMEOUT = 900
MAX_ATTEMPTS = 3
STDERR_TAIL_LINES = 40


class StoryFailed(Exception):
    """The story path produced nothing shippable; the caller falls back."""


@dataclass
class StoryResult:
    video_path: Path
    srt_path: Path
    storyboard: Storyboard
    scene_source: str
    attempts: int
    judge_score: int | None
    judgement: dict | None
    timing: dict


@dataclass
class _Candidate:
    attempt: int
    source: str
    scene_file: Path
    video: Path
    timing: dict
    judgement: object


def _stderr_tail(exc: subprocess.CalledProcessError) -> str:
    text = exc.stderr or exc.stdout or ""
    if isinstance(text, bytes):
        text = text.decode(errors="replace")
    return "\n".join(text.strip().splitlines()[-STDERR_TAIL_LINES:])


def _write_attempt(scene_dir: Path, attempt: int, source: str) -> Path:
    attempt_dir = scene_dir / f"attempt_{attempt}"
    attempt_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(STORY_SCENE_FILE, attempt_dir / "story_scene.py")
    scene_file = attempt_dir / "paper_story.py"
    scene_file.write_text(source, encoding="utf-8")
    return scene_file


def _render(scene_file: Path, spec: dict, quality: str, timeout: int) -> tuple[Path, dict]:
    timing_path = scene_file.parent / f"beat_times_{quality}.json"
    video = render_scene_video(
        scene_file, SCENE_CLASS, spec, scene_file.parent / f"media_{quality}", quality=quality,
        timeout_seconds=timeout, extra_env={TIMING_ENV: str(timing_path)},
    )
    return video, json.loads(timing_path.read_text(encoding="utf-8"))


def compose_story_video(
    digest: str,
    plan: ScenePlan,
    polly_client,
    voice_id: str,
    complete,
    work_dir,
    out_path,
    story_model: str,
    scene_model: str,
    judge_model: str,
    max_attempts: int = MAX_ATTEMPTS,
) -> StoryResult:
    try:
        return _compose(
            digest, plan, polly_client, voice_id, complete, Path(work_dir), Path(out_path),
            story_model, scene_model, judge_model, max_attempts,
        )
    except StoryFailed:
        raise
    except Exception as exc:  # noqa: BLE001 - the worker falls back on StoryFailed only
        raise StoryFailed(f"{type(exc).__name__}: {str(exc)[:300]}") from exc


def _compose(
    digest, plan, polly_client, voice_id, complete, work_dir: Path, out_path: Path,
    story_model, scene_model, judge_model, max_attempts,
) -> StoryResult:
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        storyboard = design_storyboard(digest, plan, complete, model=story_model)
    except StoryboardInvalid as exc:
        raise StoryFailed(f"storyboard: {exc}") from exc

    captions = [beat.narration for beat in storyboard.beats]
    clips = narrate(polly_client, captions, voice_id, work_dir / "narration")
    durations = [clip.seconds for clip in clips]
    spec = {"storyboard": storyboard.model_dump(), "durations": durations, "captions": captions}
    scene_dir = work_dir / "scene"

    candidates: list[_Candidate] = []
    failures: list[str] = []
    feedback: str | None = None
    previous: str | None = None
    attempts = 0
    for attempt in range(1, max_attempts + 1):
        attempts = attempt
        source = write_scene_code(
            storyboard, durations, complete, model=scene_model, feedback=feedback, previous_source=previous
        )
        previous = source
        findings = check_scene_code(source, len(storyboard.beats))
        if findings:
            feedback = "The guard rejected the file:\n- " + "\n- ".join(findings)
            failures.append(f"attempt {attempt}: guard: {findings[0]}")
            continue
        scene_file = _write_attempt(scene_dir, attempt, source)
        try:
            video, timing = _render(scene_file, spec, "l", LOW_RENDER_TIMEOUT)
        except subprocess.CalledProcessError as exc:
            feedback = "Manim failed with this traceback:\n" + _stderr_tail(exc)
            failures.append(f"attempt {attempt}: render error")
            continue
        except subprocess.TimeoutExpired:
            feedback = (
                f"The render did not finish within {LOW_RENDER_TIMEOUT} s. The scene is too heavy: "
                "use fewer mobjects, no per-frame updaters except self.counter, shorter run_times."
            )
            failures.append(f"attempt {attempt}: render timeout")
            continue
        report = overrun_report(timing)
        if report:
            feedback = report
            failures.append(f"attempt {attempt}: overrun")
            continue
        frames = sample_frames(video, beat_midpoints(timing), scene_file.parent / "frames")
        judgement = judge_frames(frames, storyboard, complete, model=judge_model)
        candidates.append(_Candidate(attempt, source, scene_file, video, timing, judgement))
        if judgement.verdict == "pass":
            break
        feedback = judgement_feedback(judgement)

    if not candidates:
        raise StoryFailed(f"no renderable scene in {attempts} attempts: " + "; ".join(failures))

    best = max(candidates, key=lambda c: (c.judgement.score, c.attempt))
    try:
        final_video, timing = _render(best.scene_file, spec, "m", FINAL_RENDER_TIMEOUT)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        final_video, timing = best.video, best.timing

    lengths = beat_lengths(timing)
    audio = concat_audio(clips, work_dir / "narration.mp3", target_seconds=lengths)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path = out_path.with_suffix(".srt")
    srt_path.write_text(build_srt(clips, durations=lengths), encoding="utf-8")
    video_path = mux_final(final_video, audio, out_path)
    return StoryResult(
        video_path=Path(video_path),
        srt_path=srt_path,
        storyboard=storyboard,
        scene_source=best.source,
        attempts=attempts,
        judge_score=best.judgement.score,
        judgement=best.judgement.model_dump(),
        timing=timing,
    )
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_story_video.py -q`
Expected: all pass.

- [ ] **Step 5: Write the local runner**

`scripts/story_video_for_url.py` (dev tool; needs AWS credentials for Bedrock and Polly, manim via uvx, ffmpeg; no DynamoDB, no Telegram):

```python
"""One URL to one metaphor video on this machine, nothing else touched.

    uv run python scripts/story_video_for_url.py https://arxiv.org/abs/2608.31076 out/autoscirub

Writes <out>/digest.md, <out>/storyboard.json, <out>/paper_story.py,
<out>/video.mp4, <out>/judgement.json. Uses the same models and the same
compose_story_video the worker uses, so what you see here is what the
10:30 run produces. AWS credentials must be current (`aws login`).
"""

import json
import os
import sys
from pathlib import Path

import boto3

from agentlab.scene_plan import DEFAULT_DEEP_READ_MODEL, deep_read
from agentlab.story_video import StoryFailed, compose_story_video
from agentlab.video_render import verify_voice
from agentlab.worker import _complete, _complete_long, _fetch_text


def main(url: str, out_dir: str) -> int:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    model = os.environ.get("DEEP_READ_MODEL", DEFAULT_DEEP_READ_MODEL)
    digest, plan = deep_read(url, _fetch_text, _complete, model=model)
    (out / "digest.md").write_text(digest, encoding="utf-8")
    (out / "scene_plan.json").write_text(plan.model_dump_json(indent=1), encoding="utf-8")
    polly = boto3.client("polly")
    try:
        result = compose_story_video(
            digest, plan, polly, verify_voice(polly), _complete_long, out / "work", out / "video.mp4",
            story_model=os.environ.get("STORY_MODEL", model),
            scene_model=os.environ.get("SCENE_MODEL", model),
            judge_model=os.environ.get("JUDGE_MODEL", model),
        )
    except StoryFailed as exc:
        print(f"story failed: {exc}")
        return 1
    (out / "storyboard.json").write_text(result.storyboard.model_dump_json(indent=1), encoding="utf-8")
    (out / "paper_story.py").write_text(result.scene_source, encoding="utf-8")
    (out / "judgement.json").write_text(json.dumps(result.judgement, indent=1), encoding="utf-8")
    print(f"video: {result.video_path}  attempts: {result.attempts}  judge: {result.judge_score}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
```

`_complete_long` does not exist until Task 7; the script imports it, so Task 7 must land before the script runs. That is fine: the script is not imported by any test.

- [ ] **Step 6: Lint and commit**

```bash
uv run ruff check src tests scripts && uv run ruff format src tests scripts
git add src/agentlab/story_video.py tests/test_story_video.py scripts/story_video_for_url.py
git commit -m "feat: compose_story_video - attempt loop, best candidate, final render, mux"
```

---

### Task 7: worker integration, infra, runbook

**Files:**
- Modify: `src/agentlab/worker.py` (imports; `_complete_long`; `_run_explain_track`; `explain_command`)
- Modify: `tests/test_worker.py`
- Modify: `infra/ecs.tf` (explain task size), `infra/iam.tf` (`stories/*`)
- Create: `docs/runbooks/metaphor-videos.md`

**Interfaces:**
- Consumes: `story_video.compose_story_video`, `story_video.StoryFailed`, `story_video.StoryResult`, `video_render.scene_texts`.
- Produces: `worker._complete_long(model, messages) -> str`; ledger `video#` items with `render_path`, `attempts`, `judge_score`, `story_key`; ledger event `STORY_FALLBACK`; S3 keys `stories/<key>.json`, `stories/<key>.py`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_worker.py`, extend the explain section. Add to the imports: `from agentlab.story_video import StoryFailed, StoryResult` and `from agentlab.storyboard import Storyboard`. Update `_patch_explain_render_stages` so existing tests keep exercising the template path:

```python
def _fake_story_failed(*args, **kwargs):
    raise StoryFailed("test: forced fallback")


def _patch_explain_render_stages(monkeypatch, fail_urls=frozenset(), story=None):
    monkeypatch.setattr("agentlab.worker.deep_read", _make_fake_deep_read(fail_urls))
    monkeypatch.setattr("agentlab.worker.verify_voice", lambda polly_client: "Joanna")
    monkeypatch.setattr("agentlab.worker.narrate", lambda polly_client, texts, voice_id, out_dir: ["clip"])
    monkeypatch.setattr("agentlab.worker.render_video", _fake_render_video)
    monkeypatch.setattr("agentlab.worker.compose_story_video", story or _fake_story_failed)
```

Add these tests:

```python
GOLDEN_BOARD = json.loads((Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(encoding="utf-8"))


def _fake_story_success(digest, plan, polly_client, voice_id, complete, work_dir, out_path, **kwargs):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"story-mp4")
    srt = out_path.with_suffix(".srt")
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nhi\n", encoding="utf-8")
    return StoryResult(
        video_path=out_path, srt_path=srt, storyboard=Storyboard(**GOLDEN_BOARD),
        scene_source="class PaperStory: pass", attempts=2, judge_score=8,
        judgement={"score": 8, "beats": [], "verdict": "pass", "note": None},
        timing={"beats": [], "total": 25.0},
    )


def test_explain_story_path_ships_and_records_artifacts(moto_fabric_with_ssm, telegram_calls, monkeypatch):
    _set_daytime(monkeypatch)
    _set_explain_env(monkeypatch, track="core")
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert "explain: core=sent" in result.output

    table = boto3.resource("dynamodb").Table(TABLE)
    videos = [i for i in table.scan()["Items"] if i["experiment_id"].startswith("video#")]
    assert len(videos) == 1
    row = videos[0]
    assert row["render_path"] == "story"
    assert row["attempts"] == 2 and row["judge_score"] == 8
    key = row["experiment_id"].removeprefix("video#")
    assert row["story_key"] == f"stories/{key}.json"

    s3 = boto3.client("s3")
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert f"stories/{key}.json" in keys and f"stories/{key}.py" in keys
    story = json.loads(s3.get_object(Bucket=BUCKET, Key=f"stories/{key}.json")["Body"].read())
    assert story["storyboard"]["metaphor"].startswith("A house move")
    assert story["judgement"]["score"] == 8

    events = [i for i in table.scan()["Items"] if i["sk"].startswith("event#")]
    assert not any(e["event"] == "STORY_FALLBACK" for e in events)


def test_explain_story_failure_falls_back_to_template_and_logs(moto_fabric_with_ssm, telegram_calls, monkeypatch):
    _set_daytime(monkeypatch)
    _set_explain_env(monkeypatch, track="core")
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch)  # compose raises StoryFailed

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert "explain: core=sent" in result.output

    table = boto3.resource("dynamodb").Table(TABLE)
    items = table.scan()["Items"]
    row = next(i for i in items if i["experiment_id"].startswith("video#"))
    assert row["render_path"] == "template"
    assert row["story_key"] is None
    fallback = [i for i in items if i["sk"].startswith("event#") and i["event"] == "STORY_FALLBACK"]
    assert len(fallback) == 1
    assert "forced fallback" in fallback[0]["detail"]
    assert fallback[0]["arm"] == "core"
```

Check how the existing tests read the table and bucket names (`TABLE`, `BUCKET`) and reuse those names.

- [ ] **Step 2: Run the tests to see them fail**

Run: `uv run pytest tests/test_worker.py -q -k explain`
Expected: `AttributeError: module 'agentlab.worker' has no attribute 'compose_story_video'` on the monkeypatch.

- [ ] **Step 3: Implement the worker changes**

Imports in `src/agentlab/worker.py`:

```python
from agentlab.story_video import StoryFailed, compose_story_video
from agentlab.video_render import narrate, render_video, scene_texts, verify_voice
```

Below `_complete`:

```python
def _complete_long(model: str, messages: list[dict]) -> str:
    """The story path's model calls (storyboard, scene code, judge): a full
    scene file does not fit the deep read's 3000-token budget, so this one
    allows 8000 output tokens and a 600 s timeout. Lazy litellm import for
    the same circular-import reason as _complete."""
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    response = litellm.completion(model=model, messages=messages, max_tokens=8000, timeout=600)
    return response.choices[0].message.content
```

`_run_explain_track` gains three parameters after `pick_model`: `story_model: str, scene_model: str, judge_model: str`. Replace the block from `voice_id = verify_voice(polly_client)` through the `table.put_item(...)` with:

```python
    voice_id = verify_voice(polly_client)
    today = datetime.now(UTC).strftime("%Y%m%d")
    with tempfile.TemporaryDirectory(prefix=f"agentlab-explain-{track}-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        video_path = tmp_path / "video.mp4"
        story = None
        try:
            story = compose_story_video(
                digest, plan, polly_client, voice_id, _complete_long, tmp_path / "story", video_path,
                story_model=story_model, scene_model=scene_model, judge_model=judge_model,
            )
        except StoryFailed as exc:
            # The worst day equals the old video: log why, render the template.
            transition(table, f"explain-{today}", "STORY_FALLBACK", track, str(exc)[:200])
            clips = narrate(polly_client, scene_texts(plan), voice_id, tmp_path / "narration")
            render_video(plan, clips, video_path)

        video_key = f"videos/{key}.mp4"
        s3_client.upload_file(str(video_path), bucket, video_key)

        story_key = None
        if story is not None:
            story_key = f"stories/{key}.json"
            story_record = {
                "storyboard": story.storyboard.model_dump(),
                "judgement": story.judgement,
                "attempts": story.attempts,
                "timing": story.timing,
            }
            s3_client.put_object(Bucket=bucket, Key=story_key, Body=json.dumps(story_record, indent=1).encode("utf-8"))
            s3_client.put_object(Bucket=bucket, Key=f"stories/{key}.py", Body=story.scene_source.encode("utf-8"))

        caption = f"{plan.one_line_claim}\n\n{plan.street_test_question}\n\n{digest_url}"
        buttons = [[("COOL", f"vid:{key}:cool"), ("MEH", f"vid:{key}:meh"), ("SKIP", f"vid:{key}:skip")]]
        notify(table, ssm_client, caption, buttons=buttons, video_path=str(video_path), video_s3_key=video_key)

    sent_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    table.put_item(
        Item={
            "experiment_id": f"video#{key}",
            "sk": "video",
            "track": track,
            "url": url,
            "title": title,
            "digest_key": digest_key,
            "video_key": video_key,
            "sent_ts": sent_ts,
            "rating": None,
            "rating_ts": None,
            "render_path": "story" if story is not None else "template",
            "attempts": story.attempts if story is not None else None,
            "judge_score": story.judge_score if story is not None else None,
            "story_key": story_key,
        }
    )
    return "sent"
```

Keep the existing comment about ratings measuring reaction next to the buttons. In `explain_command`, read the three models and pass them through:

```python
    story_model = os.environ.get("STORY_MODEL", deep_read_model)
    scene_model = os.environ.get("SCENE_MODEL", deep_read_model)
    judge_model = os.environ.get("JUDGE_MODEL", deep_read_model)
    ...
            statuses[track] = _run_explain_track(
                track, table, ssm_client, s3_client, polly_client, results_bucket,
                deep_read_model, pick_model, story_model, scene_model, judge_model, partial,
            )
```

Update the module's explain section comment (`fetch -> dedup -> pick -> deep-read -> render -> deliver -> ledger`) to `... -> deep-read -> story (or template) -> deliver -> ledger`.

- [ ] **Step 4: Run the worker tests**

Run: `uv run pytest tests/test_worker.py -q`
Expected: all pass, including the older explain tests (they now go through the forced fallback).

- [ ] **Step 5: Infra**

`infra/ecs.tf`, the explain task definition: change `cpu = "2048"` to `cpu = "4096"` and `memory = "4096"` to `memory = "8192"`, and replace the comment above the resource with: the task now renders each video up to four times (low-quality attempts plus the final medium render) through `compose_story_video`, so it gets 4 vCPU / 8 GB; still ARM64 Fargate.

`infra/iam.tf`, `VideoArtifacts` statement: add `"${aws_s3_bucket.results.arn}/stories/*",` after the `videos/*` line, and extend the comment: `stories/` holds the storyboard, judgement, and generated scene source for each story-path video.

Run: `cd infra && terraform fmt && terraform validate` (validate needs the providers initialised; if `terraform init` has not been run on this machine, skip validate and say so in the commit message).

- [ ] **Step 6: Runbook**

`docs/runbooks/metaphor-videos.md`:

```markdown
# Metaphor videos runbook

Spec: `docs/specs/2026-09-05-metaphor-videos.md`.

## What happens at 10:30

`worker explain` runs the three tracks as before.
After the deep read, each track tries the story path (`agentlab.story_video.compose_story_video`): storyboard, narration, up to three generated scenes, judge, final render.
If that path raises `StoryFailed`, the track writes a `STORY_FALLBACK` event to the ledger and renders today's template instead.
The Telegram message looks the same either way.

## Where to look

- `video#<key>` items in the state table: `render_path` (story or template), `attempts`, `judge_score`, `story_key`.
- `explain-<yyyymmdd>` items: `STORY_FALLBACK` events carry the reason in `detail`.
- S3 `stories/<key>.json`: storyboard, judgement, timing. `stories/<key>.py`: the scene source that rendered.
- CloudWatch `/ecs/agentlab-explain`: manim tracebacks for failed attempts.

## Run one paper locally

```
aws login
uv run python scripts/story_video_for_url.py https://arxiv.org/abs/<id> out/<name>
open out/<name>/video.mp4
```

This uses Bedrock, Polly, uvx manim, and ffmpeg on your machine.
It never touches DynamoDB or Telegram.

## Re-render a shipped story

```
aws s3 cp s3://<results-bucket>/stories/<key>.py work/paper_story.py
aws s3 cp s3://<results-bucket>/stories/<key>.json work/story.json
cp src/agentlab/story_scene.py work/
```

Build a spec file `{"storyboard": <story.json's storyboard>, "durations": [...], "captions": [...]}` and run `SCENE_SPEC_JSON=work/spec.json PYTHONPATH=work uvx --python 3.12 manim render -ql work/paper_story.py PaperStory`.

## Knobs

- `STORY_MODEL`, `SCENE_MODEL`, `JUDGE_MODEL`: Bedrock model ids; default to the deep-read model.
- `agentlab.story_video.MAX_ATTEMPTS`, `LOW_RENDER_TIMEOUT`, `FINAL_RENDER_TIMEOUT`.
- `agentlab.story_scene.PER_BEAT_OVERRUN_LIMIT`, `TOTAL_OVERRUN_LIMIT`.
- The prompts: `storyboard.STORYBOARD_SYSTEM`, `scene_code.SCENE_CODE_SYSTEM`, `frame_judge.JUDGE_SYSTEM`.

## Deploy

```
scripts/build_and_push_video_image.sh
cd infra && terraform apply
```

The video image tag file `infra/video_image_tag.auto.tfvars` is rewritten by the build script.
```

- [ ] **Step 7: Full suite, lint, commit**

Run: `uv run pytest -q && uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`
Expected: all green.

```bash
git add src/agentlab/worker.py tests/test_worker.py infra/ecs.tf infra/iam.tf docs/runbooks/metaphor-videos.md
git commit -m "feat: worker tries the story path first, template on StoryFailed; 4 vCPU explain task; stories/ prefix"
```

---

### Task 8: Live verification and deploy (orchestrator, needs AWS)

Not for a subagent. Steps, in order:

- [ ] `aws sts get-caller-identity` works (Daniel ran `aws login`).
- [ ] `uv run pytest -q` green on the branch; `uv run pytest -m render tests/test_story_scene.py -q` green.
- [ ] `uv run python scripts/story_video_for_url.py https://arxiv.org/abs/2608.31076 out/autoscirub` (yesterday's core paper) and watch the montage: `ffmpeg -i out/autoscirub/video.mp4 -vf "fps=1/10,scale=640:-1,tile=3x3" -frames:v 1 out/autoscirub/montage.png`. Judge it against the tournament clip. Iterate on the three prompts until a run passes the judge on attempt 1 or 2 and the frames look like a metaphor, not a diagram.
- [ ] Run one classic too (`https://arxiv.org/abs/1706.03762`) to see a second metaphor; confirm the two videos share no skeleton.
- [ ] `aws ecs describe-task-definition --task-definition agentlab-explain --query "taskDefinition.containerDefinitions[0].image"` to learn the live tag before replacing it.
- [ ] Docker running; `scripts/build_and_push_video_image.sh`; `cd infra && terraform plan` (expect: task definition size, IAM statement, image tag) then `terraform apply`.
- [ ] Ask Daniel before triggering a manual run (it sends a real video to his Telegram and consumes a classic); otherwise wait for the next 10:30 and check `explain: core=... classic=... novel=...` in CloudWatch plus the ledger's `render_path`.
- [ ] Merge the branch into main after the first live run ships a story-path video.
