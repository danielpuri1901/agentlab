# Metaphor videos: the Compose stage rebuilt

Status: designed with Daniel 2026-09-05, approved in chat (generated scene per paper, verify loop, template fallback, ship the best attempt when the judge still complains).
This spec replaces stage 5 (RENDER) of `docs/specs/2026-08-23-daily-paper-videos.md`.
Stages 1 to 4 and 6 of that spec are unchanged.

## Why

Every daily video looks the same because the model only fills a form.
The form (ScenePlan: boxes, edges, six motif kinds) always renders as a flowchart with a moving highlight.
A flowchart is already figure 1 of the paper, so the viewer learns nothing from the video.

3Blue1Brown videos work differently: one metaphor per idea, the mechanism happens as a visible change on one object, and a new scene is written for every idea.
Those videos are made with Manim, the same tool this pipeline uses.
The only rule in the way is the old spec's "the model never writes animation code".
This spec drops that rule and replaces it with a guarded loop and a fallback, so the worst day still equals today's video.

## The new Compose stage

Input: the deep read's digest and validated ScenePlan (grounded numbers, limits, street-test question).
Output: a video whose visual is a per-paper metaphor animation, or the old template video when the new path fails.

```
digest + plan
  -> 1. storyboard (creative model call)
  -> 2. narration (Polly, one clip per beat, durations known before any code exists)
  -> 3. scene code (coder model call)
  -> 4. guard (AST allowlist)          fail -> back to 3 with the error
  -> 5. render at low quality, timed   fail -> back to 3 with the traceback
  -> 6. timing check (beat overruns)   fail -> back to 3 with the overruns
  -> 7. frame judge (vision call)      fix  -> back to 3 with the issues, keep this attempt as a candidate
  -> 8. best candidate rendered at medium quality, audio padded to actual beat lengths, mux
  -> any hard failure after the retries -> StoryFailed -> today's PaperScene template
```

Attempts: at most 3 scene-code attempts per video.
An attempt that renders and passes the timing check is a candidate even if the judge says fix.
The shipped candidate is the one with the highest judge score, latest on ties.
Only "no candidate at all" falls back to the template.
Storyboard failures (invalid after one retry) also fall back to the template.

## 1. Storyboard

Module `src/agentlab/storyboard.py`.
One model call, high temperature (1.0), model from env `STORY_MODEL`, default the deep-read model.

The prompt receives the digest and the ScenePlan (as JSON) and asks for three candidate metaphors, then the chosen one and its beats.
The prompt forbids a flowchart, a pipeline of labelled boxes, or "boxes light up in order" as the main visual.
It requires one physical object or scene that stands for the mechanism, and a visible change on that object in every mechanism beat.
It requires the paper's real numbers to appear on the metaphor (a bar, a counter, a fill level, a count of objects), never as a separate stats slide.
It requires the second-to-last beat to state the limits and the last beat to ask the street-test question.
Grounding rule as in the deep read: every number in a beat must appear in the digest or the plan's key_numbers.
Style rules as in the deep read: plain spoken sentences, no em dash, Simplified Technical English.

Three worked exemplars are given as style anchors, marked as never-reuse: compaction as a house move with one suitcase (the tournament video), Tree of Thoughts as a tree that grows and is pruned, RLHF as a wall of dials nudged by a thumbs-up.

Schema (pydantic, `Storyboard`):

```
Storyboard
  metaphor: str            1..200   the object and what it stands for
  why_this_metaphor: str   1..300   which change becomes visible on it
  rejected: list[str]      0..3     the other candidates, one line each (kept for inspection)
  mapping: list[Mapping]   2..6
    Mapping.paper_term: str 1..40
    Mapping.visual: str     1..60
  beats: list[Beat]        5..9
    Beat.narration: str      1..280  spoken, this is the voiceover and the caption
    Beat.visual: str         1..500  what is on screen, what changes, what the change proves
    Beat.on_screen_text: list[str] 0..3, each 1..40
```

Parsing follows scene_plan.py: fenced or dirty JSON tolerated, over-long strings clipped, structural violations fatal.
Grounding check (deterministic): every token matching `\d+(\.\d+)?%` or `\d{3,}` in any narration or on_screen_text must appear in the digest text or in a key_numbers value.
A grounding violation is a validation error: one retry with the error appended, then `StoryboardInvalid` (the caller falls back to the template).

Interface:

```python
def design_storyboard(digest: str, plan: ScenePlan, complete, model: str) -> Storyboard
```

`complete` is the same `(model, messages) -> str` callable the deep read uses.

## 2. Narration

`video_render.narrate` changes signature to take texts, not a plan:

```python
def narrate(polly_client, texts: list[str], voice_id: str, out_dir) -> list[NarrationClip]
```

The template path passes `scene_texts(plan)`; the story path passes `[beat.narration for beat in storyboard.beats]`.
Durations are measured with ffprobe as today and handed to the coder before it writes any code.

## 3. Scene code

Module `src/agentlab/scene_code.py`.
Model from env `SCENE_MODEL`, default the deep-read model, `max_tokens` 8000.

The coder receives: the storyboard JSON, the per-beat durations in seconds, the StoryScene API cheat-sheet (a constant string in this module, kept in sync with story_scene.py by a test), and the house rules.
It answers with exactly one fenced python block.

House rules given to the coder:

- Subclass `StoryScene` from `story_scene`; the class is named `PaperStory`; do not override `construct`.
- One method per beat, `beat_1` to `beat_n`, n equal to the storyboard's beat count.
- Each beat's animations must finish at least 0.3 s before that beat's narration ends (the durations are given); the base class pads the rest with a hold.
- Objects that persist across beats live on `self`.
- Everything stays inside the stage rectangle (x from -6.4 to 6.4, y from -2.3 to 3.6); the bottom band is the caption's and is never drawn on.
- No LaTeX: never `Tex`, `MathTex`, `DecimalNumber`, `Integer`, `Title`, `Variable`, `Matrix`, `Table`, axis labels, `include_numbers`; numbers are `Text` or `self.counter(...)`.
- No camera moves; zoom by scaling a group.
- No emoji, no images, no files, no network, no custom fonts.
- Only these imports: `manim`, `story_scene`, `math`, `random`, `itertools`, `functools`, `numpy`, `dataclasses`, `typing`, `colorsys`.
- Use `self.fit(...)` on every text block and every group before placing it.

Guard, `check_scene_code(source: str, beat_count: int) -> list[str]` (empty list means clean):

- Parses with `ast`; a syntax error is one finding.
- Imports outside the allowlist are findings.
- NumPy submodule imports are findings.
- Root NumPy access is limited to an explicit numeric surface, including arrays, vector operations, elementary functions, reductions, interpolation, and selected `numpy.linalg` operations.
- Any `Name` or `Attribute` attr in the forbidden set is a finding: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`, `getattr`, `setattr`, `delattr`, `vars`, `breakpoint`, `input`, `os`, `sys`, `subprocess`, `socket`, `pathlib`, `shutil`, `importlib`, `builtins`, `__builtins__`, `__subclasses__`, `__globals__`, `__dict__`, `__class__`, `__mro__`, `Tex`, `MathTex`, `SingleStringMathTex`, `DecimalNumber`, `Integer`, `Variable`, `Title`, `BulletedList`, `Matrix`, `IntegerMatrix`, `DecimalMatrix`, `MobjectMatrix`, `Table`, `MathTable`, `IntegerTable`, `DecimalTable`, `MobjectTable`, `get_axis_labels`, `get_x_axis_label`, `get_y_axis_label`, `add_coordinates`, `ImageMobject`, `SVGMobject`, `Code`, `add_sound`, `interactive_embed`.
- A keyword argument `include_numbers=True` anywhere is a finding.
- Exactly one class named `PaperStory` whose bases include `StoryScene`; a `construct` method on it is a finding.
- Methods `beat_1` to `beat_n` all present, no extra `beat_k` beyond n.

Interface:

```python
def write_scene_code(storyboard: Storyboard, durations: list[float], complete, model: str, feedback: str | None = None, previous_source: str | None = None) -> str
```

With `feedback` and `previous_source` set, the call is a fix round: the previous file and the failure text (guard findings, traceback tail, overruns, or judge issues) are appended and the model returns the full corrected file.

## 4. The StoryScene base class

Module `src/agentlab/story_scene.py`, standalone like video_scenes.py: imports only manim and the stdlib, never agentlab.
At render time it is copied next to the generated scene file, and the render subprocess gets that directory on `PYTHONPATH`, so `from story_scene import StoryScene` works under both `python -m manim` (the video image) and `uvx manim` (the laptop).

It reads `SCENE_SPEC_JSON`: `{"storyboard": <Storyboard.model_dump()>, "durations": [...], "captions": [...]}` with one duration and one caption per beat.
It writes `SCENE_TIMING_OUT` at the end of `construct`: `{"beats": [{"beat": 1, "start": s, "end": e, "narration": d, "overrun": max(0, (e - s) - d)}, ...], "total": t}`.

`construct` per beat i:

1. Swap the caption (FadeOut old, FadeIn new, 0.25 s, on the bottom band) using `captions[i]`.
2. `start = self.time`; call `self.beat_<i+1>()`.
3. `remaining = durations[i] - (self.time - start)`; if positive, `self.wait(remaining)`; record the beat.

`Scene.time` is the renderer clock (verified against Manim 0.21: `Scene.time` returns `renderer.time`, advanced by every play and wait).

Public API the coder may use (the cheat-sheet documents exactly these):

- Constants: `BACKGROUND`, `ACCENT` (#2f6fd6), `GOLD`, `GREEN`, `RED`, `GREY_A` to `GREY_D`, `WHITE`; `STAGE_TOP`, `STAGE_BOTTOM`, `STAGE_LEFT`, `STAGE_RIGHT`.
- `self.fit(mobject, max_w=None, max_h=None)`: shrink to the stage or the given bounds, returns the mobject.
- `self.label(text, size=28, color=WHITE, width=44)`: a wrapped `Text`, fitted.
- `self.counter(start, end, suffix="", size=44, color=ACCENT)`: returns `(mobject, animation)`; the mobject is a text counter, the animation counts it from start to end; `self.freeze(mobject)` turns it into a plain Text before any group animation touches it (the always_redraw hazard documented in video_scenes.py).
- `self.clear_stage(run_time=0.4)`: fades out everything except the caption.
- `self.hold(seconds)`: `self.wait`, named so the coder reaches for it.

The base class owns the background colour, the caption band, safe-margin fitting, and timing.
The generated class owns everything the viewer watches.

## 5. Guarded render

`video_render.render_scene_video` is generalised:

```python
def render_scene_video(scene_file: Path, scene_class: str, spec: dict, out_dir, quality: str = "l", timeout_seconds: int = 480, extra_env: dict | None = None) -> Path
```

- The spec dict is written to a temp JSON file and passed as `SCENE_SPEC_JSON`.
- The subprocess environment is built from `PATH`, `HOME`, `LANG`, `LC_ALL`, `TMPDIR`, `PYTHONPATH` (the scene file's directory prepended), plus `SCENE_SPEC_JSON` and any `extra_env`.
- Filtering inherited AWS environment variables reduces direct exposure, but it is not a filesystem, user, network, or credential-isolation boundary.
- The render subprocess retains the worker filesystem, worker user identity, and network access.
- Credential-isolated rendering remains required but unimplemented pending a decision between stronger isolation and an explicitly trusted-generated-code boundary.
- `timeout_seconds` is passed to `subprocess.run`; a timeout is a render failure.
- The template path keeps a thin wrapper, `render_template_video(plan, durations, out_dir, quality)`, that calls the general function with video_scenes.py and `PaperScene`.

Story renders use `SCENE_TIMING_OUT` via `extra_env`; the timing check reads it.
Timing check rule: any beat overrun above 0.75 s, or total overrun above 3 s, is a failure with a message naming the beats and their overruns.

Audio: `concat_audio(clips, target_seconds: list[float] | None)` pads each clip with silence to its target (the beat's actual length from the final render's timing file) before concatenation, so audio and picture stay aligned even when a beat ran slightly long.
The mux is unchanged.

## 6. Frame judge

Module `src/agentlab/frame_judge.py`.

- `sample_frames(video_path, times: list[float], out_dir) -> list[Path]`: one PNG per time via ffmpeg, scaled to 960 px wide. The story path samples each beat's midpoint from the timing file.
- `judge_frames(frames: list[Path], storyboard: Storyboard, complete, model: str) -> Judgement`: one vision call (images as base64 data URLs in OpenAI-style content parts, which litellm maps to Bedrock Converse). The prompt gives each frame its beat's visual and narration and asks for strict JSON.

```
Judgement
  beats: list[BeatJudgement]   one per frame
    beat: int
    shows_visual: bool
    legible: bool
    clean: bool          no overlap, nothing cut off at the frame edge, nothing drawn over the caption band
    issue: str | None
  score: int 0..10
  verdict: "pass" | "fix"
```

Verdict rule (applied in code from the per-beat fields, not trusted from the model): `fix` if any beat is not clean or not legible, or if two or more beats do not show their visual; otherwise `pass`.
Judge failures (bad JSON after one retry, model error) are not fatal: the attempt is treated as `pass` with score 5 and the failure is logged, so a judge outage never costs a video.

## 7. Orchestration

Module `src/agentlab/story_video.py`.

```python
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

class StoryFailed(Exception): ...

def compose_story_video(digest: str, plan: ScenePlan, polly_client, voice_id: str, complete, work_dir: Path, out_path: Path, story_model: str, scene_model: str, judge_model: str, max_attempts: int = 3) -> StoryResult
```

Every external step (narrate, render, sample_frames, judge) is a module-level name so tests monkeypatch them the way test_worker.py does.

## 8. Worker integration

`_run_explain_track` after the digest upload:

1. `voice_id = verify_voice(polly)`.
2. Try `compose_story_video(...)`. On `StoryFailed`, write a ledger event `STORY_FALLBACK` with the reason (first 200 chars), then run today's path: `narrate(polly, scene_texts(plan), ...)` and `render_video(plan, clips, video_path)`.
3. Upload artifacts: `videos/<key>.mp4` as today; on the story path also `stories/<key>.json` (storyboard, judgement, attempts, timing) and `stories/<key>.py` (the scene source).
4. The Telegram caption is unchanged.
5. The `video#<key>` ledger item gains `render_path` ("story" or "template"), `attempts`, `judge_score`, `story_key`.

Env: `STORY_MODEL`, `SCENE_MODEL`, `JUDGE_MODEL` all default to `DEFAULT_DEEP_READ_MODEL`.

The story path gets its own completion callable, `worker._complete_long`, identical to `_complete` but with `max_tokens` 8000 and a 600 s timeout, because a full scene file does not fit in the deep read's 3000-token budget.
The deep read and pick keep `_complete` unchanged.

## Infra

- `infra/ecs.tf`: the explain task goes to 4 vCPU / 8 GB (each video now renders up to four times).
- `infra/iam.tf`: `VideoArtifacts` gains `stories/*`.
- Dockerfile.video: unchanged (manim already in the venv; numpy comes with it; no LaTeX by design).

## Failure behaviour (silence still means broken)

- Storyboard invalid after retry, no renderable attempt in 3, or an exception anywhere in the story path: `STORY_FALLBACK` ledger event, template video ships, Daniel is not pinged separately (the video itself is the signal, and the ledger has the reason).
- Template failure after a story failure: today's fallback ping with the digest link, unchanged.
- Judge unavailable: attempt treated as pass with score 5, logged.

## Cost and time

Per video, typical: one storyboard call, one to two coder calls, one judge call, one to two low renders, one medium render. Roughly 6 to 12 minutes on 4 vCPU.
Worst case (three failing attempts plus final render): about 35 minutes.
Three tracks stay sequential, so the run may finish an hour later than today on a bad day.
Model cost per video: well under a dollar on Sonnet-tier pricing; bounded by the existing $50 alarm.

## Testing

Offline (default `uv run pytest`):

- storyboard: parse dirty output, clip lengths, structural failures, grounding check catches an invented number, retry then `StoryboardInvalid`.
- scene_code: a golden generated scene passes the guard; each forbidden import, name, LaTeX object, `include_numbers=True`, missing `beat_k`, extra `beat_k`, and a `construct` override each produce a finding; the cheat-sheet names every public StoryScene method.
- story_scene: pure helpers importable without manim (same pattern as video_scenes.py).
- frame_judge: verdict rule from per-beat fields; bad JSON retried then treated as pass with score 5.
- story_video: with fake narrate/render/judge seams, the loop retries on guard findings, tracebacks, overruns, and judge fix; three failures raise `StoryFailed`; the best-scoring candidate ships; the final render failure falls back to the low-quality render.
- video_render: clean subprocess env has no `AWS_*` key; timeout is passed; `concat_audio` pads to targets.
- worker: story success writes the new ledger fields and uploads `stories/` keys; `StoryFailed` writes `STORY_FALLBACK` and ships the template video.

Render-marked (`-m render`): a hand-written golden `PaperStory` fixture renders through `render_scene_video` with real manim, the timing file has one entry per beat and overruns of zero.

## Out of scope

Grok Imagine or any generative video model (a later side experiment).
Parallel track rendering.
Changing find, pick, deep read, delivery, or ratings.
Fixing the template's own layout (it is now the fallback only).
