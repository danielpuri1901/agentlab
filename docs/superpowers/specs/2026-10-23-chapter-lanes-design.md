# Chapter Lanes Design

Date: 2026-10-23.
Status: Daniel agreed to the direction in chat on 2026-10-23 ("agreed lets do it").
This document waits for his review of the details before the implementation plan.

## Goal

One failed scene no longer fails the whole video.
The scene stage is split into chapters.
Each chapter is built, checked, retried and checkpointed in its own lane, lanes run in parallel, and the video is joined at the end.
A chapter that cannot be rendered ships as a text card, so the video always sends.
An independent reviewer looks at stills of the finished chapters and may name one chapter to fix.
Stage settings move into one spec file per stage.

## Why

Today `story_video._compose` asks the scene model for one Manim file that covers every beat, renders the whole video, and retries the whole file on any failure.
A guard rejection, a render error, a timing error or a timeout on beat 9 costs the full attempt and up to 600 seconds of render, six times, and then the track dies for the day.
The unit of failure is the video, so the unit of retry is the video.

The pattern that fixes this is the bulkhead (Nygard, Release It!, 2007): partition the work so a failure stays inside its partition.
The two closest research systems both shard scenes and run them concurrently (Evidence, E1 and E2).
Anthropic's production lesson is the same: checkpoints per unit, resume instead of restart (E3).

## Decisions (Daniel, 2026-10-23)

- Chapter lanes: yes.
- Stills reviewer with a cap of one fix: yes, logging-only until it proves precision against his ratings.
- Contact sheet in the Telegram message: yes.
- One spec file per stage: yes, TOML (standard library), not YAML (new dependency).
- Music loop: no.
- Effort as a knob: only with the agent's own-job benchmark, as the 2026-07-14 rule requires.
- Everything else in the pipeline stays: grounding checks, AST guard, filtered render environment, deadline, cost ledger, taste flywheel.

## Scope

In scope: chapter grouping, the lane loop, the fallback card, the join, per-chapter checkpoints, stills and contact sheets, the reviewer in logging mode, the stage spec files, tests, rollout.

Out of scope:

- The reviewer's fix round. It is specified here but ships behind its own flag after calibration (Design 7).
- Prompt text inside the spec files. Settings move first; prompts stay in Python (Design 8).
- The deep-read prompt, the storyboard prompt, the models and the voice.
- Any change to the render image.
- Moving the pipeline to Managed Agents workflows or to a Step Functions Map per chapter (Evidence, rejected alternatives).

## Design

### 1. Chapter grouping (`src/agentlab/storyboard.py`)

New function `chapters(storyboard) -> list[list[int]]`: beat indexes grouped into contiguous chapters, deterministic, no model call.
Grouping follows the beat roles the storyboard already carries:

| Chapter | Roles |
|---|---|
| 1 | title, problem |
| 2 | mechanism (split into two chapters at the midpoint when there are more than 4 mechanism beats) |
| 3 | result, application |
| 4 | limit, question |

A chapter with no beats is skipped.
A storyboard whose roles do not follow this order (they always do after `parse_storyboard`, which enforces the role order) falls back to groups of three beats.
So a 6 to 12 beat storyboard gives 3 to 5 chapters.

A chapter storyboard is `storyboard.model_copy(update={"beats": subset})`: same title, definition, visual focus and colour key, fewer beats.
It is a copy for prompting and rendering, never re-validated, because `Storyboard.beats` has a minimum of six.

### 2. Scene prompt (`src/agentlab/scene_code.py`)

`build_scene_code_prompt` and `build_edit_prompt` take an optional `chapter` note.
The note says: this file is chapter k of n; its beats are `beat_1` to `beat_m` here and beats X to Y of the whole storyboard; the previous chapter ended with this visual (the `visual` text of the last beat before the chapter); start on an empty stage and reference nothing from other chapters; the colour key applies to the whole video.
The rest of the prompt is unchanged, so the cached system prompt is unchanged.
`check_scene_code(source, beat_count)` runs with the chapter's beat count.

Continuity rule, from the 2026-10-05 spec: objects persist and transform across beats.
It now holds inside a chapter.
Camera and stage reset at chapter seams, which fall where the lesson changes section.

### 3. Lane loop (`src/agentlab/story_video.py`)

Today's attempt loop becomes `_build_chapter(k, chapter_board, chapter_durations, ...)`.
It keeps every rule of the current loop: generate, edit with the exact failure, rewrite when the edit does not apply, resume a saved file without a model call, guard, render, timing check, one layout round with the first good render as fallback.
Three things change:

- Attempts per lane: `LANE_ATTEMPTS = 4` (today 6 for the whole video).
- Work directory per lane: `work_dir / f"chapter_{k}"`, so renders, attempts and timing files never collide.
- Exhaustion: when the lane runs out of attempts or the shared deadline leaves no room for a render, the lane returns the fallback card (Design 4) instead of raising.

`_compose` runs the lanes with `concurrent.futures.ThreadPoolExecutor(max_workers=min(len(chapters), LANE_WORKERS))`, `LANE_WORKERS = 3` on the 4 vCPU task.
Model calls release the interpreter while they wait; renders are subprocesses.
The deadline stays shared: `_deadline_bound_completion` and `_remaining_timeout` already close over the start time, and every lane uses them.
Fan-in collects one result per chapter, in chapter order: a `_Rendered` from the model or from the card.
Fan-in never receives an exception from a lane.
A lane that raises anything else is a bug in our code, not a model failure; it is logged and the chapter gets the card.

Shared objects across threads: the anthropic client and the boto3 S3 client are shared, both are safe for concurrent calls; the usage sink is a list, and appends are atomic in CPython; checkpoint writes go to distinct keys per chapter.

### 4. Fallback card (`src/agentlab/story_scene.py`, `src/agentlab/scene_code.py`)

`StoryScene.card(title, lines, seconds)`: the title at the top edge in the title size, up to two on-screen lines centred on the stage in the label size, fade in, hold, fade out, inside the stage bounds, in the video's palette.
`card_scene_source(chapter_board)` returns the source of a `PaperStory` whose `beat_i` methods call `card` with the beat's `on_screen_text` (or the first words of its narration when the beat has none).
The card goes through the same render seam as a model scene, with subtitles and timing from the base class.
It is our code, so the guard does not run on it, and a failing card render is a bug that fails the video loudly.
The card is never saved as the chapter's scene checkpoint; the lane's last source and feedback are saved as today, so a rerun tries the model again.

### 5. Join (`src/agentlab/video_render.py`, `story_video.py`)

Per-beat lengths are the concatenation of `beat_lengths(timing)` over the chapters in order.
`concat_audio(clips, target_seconds=lengths)` and `build_srt(clips, durations=lengths)` are unchanged.
`mux_final` takes a list of chapter videos: an ffmpeg concat demuxer list as input, the saturation filter, one libx264 encode, AAC audio.
The explainer-video worktree learned this on 2026-10-22 (commit `cb3cdbd`): chapters joined with stream copy had unclean timestamps at the seams; one re-encode fixes it, and the saturation pass already re-encodes once, so the cost is unchanged.

`StoryResult` gains `chapters`: for each chapter its attempts, status (`shipped`, `layout_fallback`, `card`) and source path.
`scene_source` becomes the chapter sources joined with a `# chapter k` header, so the attempt upload and the "recent visual directions" reader keep working; `visual_direction` reads chapter 1.

### 6. Stills and contact sheet (`src/agentlab/video_render.py`, `notify.py`, `worker.py`)

`contact_sheet(video_path, timing, out_png)`: one frame at the start plus 0.2 s, the midpoint, and the end minus 0.2 s of every beat, taken with ffmpeg `select` from the beat timing, tiled three per row, scaled to 1280 px wide.
One sheet per chapter, written beside the chapter video and uploaded to `stories/<video_key>/stills/chapter_<k>.png`.
The Telegram message gains one `send_photo` with the chapter sheets stacked, sent before the video, same caption prefix.
The caption names every chapter that shipped as a card: "chapter 2 of 4 is a text card (render timed out twice)".

### 7. Reviewer (`src/agentlab/scene_review.py`, new)

Input: the chapter sheets as base64 image blocks (the Messages adapter already accepts data URLs), plus each chapter's beat narration and on-screen text.
Never the code.
Model: the reviewer stage (Design 8), Haiku 4.5 at first.
Output: structured, through the Messages API response format (`output_config.format`, passed as `extra_fields`), validated with a Pydantic model: `verdict` (`ship` or `fix`), `chapter` (index or null), `problem` (one sentence a viewer would say: unreadable text, overlap, empty stage, wrong object).
Budget: one call, 300 output tokens, 60 seconds.
Any failure of the reviewer (timeout, error, invalid output, more than one chapter named) counts as `ship`.

Phase 1, this spec: the verdict is logged in the story JSON and the ledger row, nothing else changes.
Calibration: the verdicts of the first 20 reviewed videos against Daniel's ratings in `agentlab-state`.
Phase 2, behind `REVIEWER=fix`, only when flagged videos are low-rated with precision 0.7 or better: one edit round on the named chapter, with the problem text as feedback, through the lane's existing edit path, then a new join.
A fix that fails, or is flagged again, ships the original.
The reviewer can delay a video, never block it.

Why blind to code: MAST found most multi-agent verifiers do superficial checks on the artifact's shape (E5); the AST guard already covers the code; the reviewer's job is what a viewer sees.
Why one fix: self-correction without an external signal does not help (E6); the reviewer's note is the external signal, and one round keeps the cost bounded, as in Code2Video's single refinement round (E1).

### 8. Stage spec files (`src/agentlab/stages/*.toml`, `src/agentlab/stages.py`)

One TOML file per model stage: `deep_read`, `storyboard`, `scene`, `reviewer`.
Fields: `model`, `price_model`, `effort` (optional), `thinking` (optional), `max_tokens`, `timeout_seconds`, `attempts`.
`stages.py` loads them once with `tomllib`, validates with a Pydantic model, and exposes `stage("scene")`.
`stage.fingerprint` is the first twelve hex characters of the SHA-256 of the file bytes.
The checkpoint fingerprints for the storyboard and scene stages include the stage fingerprint, so a settings change invalidates saved stages the way a prompt change does today.
Environment variables keep their override role (`STORY_MODEL`, `SCENE_MODEL`, `STORY_EFFORT`, `DEEP_READ_THINKING`): Terraform can still swap a model without an image build.
Precedence: environment, then file.

Prompt text stays in Python.
The deep-read and scene prompts are composed from parts (the worked example, the scene API text, the example scene file), and moving that composition into TOML adds templating for no behaviour change.
Add it when a prompt edit without a code change becomes a real need.

### 9. Budgets

| Setting | Today | New |
|---|---|---|
| Attempts | 6 per video | 4 per lane |
| Render timeout | 600 s | 600 s cap per render, expected about a third of today |
| Video deadline | 2400 s | 2400 s, shared by all lanes |
| Layout rounds | 1 per video | 1 per lane |
| Parallel renders | 1 | up to 3 |
| Reviewer | none | 1 call, 1 fix at most, after calibration |

### 10. Cost and time

- Scene model: three to five calls per video instead of one, each with a third of the beats, under the same cached system prompt.
  Output tokens stay about the same in total.
  Cache reads replace one cache write per extra call.
- Render: wall clock becomes the slowest lane plus the join.
  Manim starts a process per render and LaTeX caches per process, so the gain is less than linear; the validation measures it.
- Reviewer: one Haiku call with three to five images, cents per video.
- Contact sheets: ffmpeg only.

### 11. Checkpoints

Stage name `scene-<k>` per chapter.
Fingerprint: scene model, storyboard fingerprint, chapter index, the chapter's beat count, the scene stage fingerprint, the scene prompt and API text.
A rerun pays only for lanes that did not finish.
The storyboard checkpoint is unchanged.

## Testing

Unit tests, `uv run pytest`:

- `test_storyboard.py`: `chapters()` on 6, 8 and 12 beat storyboards, the mechanism split, the fallback to groups of three on an out-of-order board, no empty chapter.
- `test_story_video.py`: with the existing seams (`write_scene_code`, `edit_scene_code`, `_render`, `narrate`, `concat_audio`, `mux_final` are module-level names), one lane fails the guard on every attempt and the video ships with a card for that chapter and model scenes for the others; the deadline runs out while two lanes are open and both ship their fallback; per-beat lengths concatenate in chapter order; the resume path renders a saved chapter without a model call; a reviewer error counts as ship.
- `test_video_render.py`: the concat command built for `mux_final` lists the chapters in order and encodes once; `contact_sheet` picks three timestamps per beat from a timing dict.
- `test_scene_review.py`: the verdict schema, the one-chapter rule, image blocks built from PNG bytes.
- `test_stages.py`: the four files load, the fingerprint changes when a byte changes, an environment override wins.
- `test_story_scene.py`, render-marked: the card scene renders for a two-beat chapter.
  This also runs in the image's test stage, so the card can never break in the cloud.

Cloud checks, in the order of the research report's validation steps:

1. Replay one checkpointed built-lane topic through lanes.
   Pass: the joined video length equals the sum of the chapter timings within 0.1 s, the srt lines sit on their beats, and the layout warning count is not above the monolithic run of the same topic.
2. Five built-lane topics through lanes against their monolithic runs.
   Pass: median wall clock from the attempt records down 30 percent or more, ledger cost per video within 20 percent above.
3. Reviewer calibration on the stills of the last 20 shipped videos against the ratings in `agentlab-state`.
   Pass: precision 0.7 or better on flagged videos.
   Fail: the reviewer stays in logging mode.
4. Two weeks of the built lane on chapter lanes.
   Pass: the mean rating is not below the monolithic baseline.

## Rollout and rollback

- Flag `CHAPTER_LANES` in the scheduler environment (Terraform), default off.
- Week 1: on for the built lane only.
- Then all tracks, when check 2 passes.
- `REVIEWER` flag: `off`, `log` (default once lanes are on), `fix` (after check 3).
- Rollback is the flag; the monolithic loop stays in the code until check 4 is read.
  After that the monolithic path is deleted, so there are not two scene loops to maintain.
- The chapter checkpoints use new stage names, so a flag flip never reuses the wrong saved stage.

## Risks

- Seam quality: continuity across chapters is lost by design.
  Only check 4 says what it costs.
- Reviewer precision on stills alone is unknown; Code2Video's critic also saw the code and an anchor grid.
  Logging mode first.
- Three Manim processes on 4 vCPU may slow each render; the per-render timeout stays at 600 s so a slow lane cannot take the video down, and check 2 measures the real gain.
- Thread safety: shared clients are safe, but any new per-video state must live in the lane's own objects.
- One more Telegram message per video.

## Evidence

- E1. Code2Video (Chen, Lin, Shou, October 2025), https://arxiv.org/abs/2510.01174.
  Planner, Coder, Critic; sections coded in parallel and merged through shared assets; the Critic does one refinement round on the rendered video and the code with a 6 by 6 anchor grid.
  Ablations: without parallel generation 5.6 times longer; without the Critic 21.3 points lower on TeachQuiz and 6.5 on aesthetics; best Manim code from Claude Opus 4.
- E2. TheoremExplainAgent (Ku et al., February 2025), https://arxiv.org/abs/2502.19400.
  Planner to per-scene Manim code, error-fix loop capped at five attempts (near zero success without fixes, up to 90 percent with five), scene concurrency and a combine step, optional VLM visual fix; most outputs still had minor layout issues.
- E3. Anthropic, How we built our multi-agent research system (June 2025), https://www.anthropic.com/engineering/multi-agent-research-system.
  Parallel subagents cut time by up to 90 percent; agents are stateful and errors compound; resume from where the agent was, with retries and regular checkpoints; one LLM judge call with a rubric was more consistent than several judges.
- E4. AWS Step Functions, Distributed Map failure thresholds, https://docs.aws.amazon.com/step-functions/latest/dg/state-map-distributed.html.
  `ToleratedFailurePercentage` defaults to zero, so one failed child fails the run unless tolerance is set; an inline Map retry re-runs every iteration.
  Containment has to live inside the unit, which is where the lane puts it.
- E5. MAST, Why Do Multi-Agent LLM Systems Fail? (Cemri et al., NeurIPS 2025), https://arxiv.org/abs/2503.13657.
  1600 annotated traces; verification failures about 23 percent of all; explicit verifiers reduce failures but most check only the artifact's shape; objective-level verification gave 15.6 points on ProgramDev.
- E6. Huang et al., Large Language Models Cannot Self-Correct Reasoning Yet (2023), https://arxiv.org/abs/2310.01798.
  Self-correction without external feedback does not help and can degrade; every fix round here carries one.
- Code facts: `story_video._compose` (one file, one render, six attempts, 2400 s), `story_scene.StoryScene.construct` (one method per beat, timing and layout warnings written at the end), `stage_checkpoints.StageCheckpoints.key` (`<stage>-<fingerprint>.json`), `anthropic_api._image_block` (base64 data URLs), `notify.send_photo`, the explain task at 4 vCPU and 8 GB in `infra/ecs.tf`, and commit `cb3cdbd` on the `explainer-video` worktree for the concat re-encode.

## Rejected alternatives

- Managed Agents dynamic workflows as the runtime.
  The hosted sandbox has no Manim and TeX image, and the deterministic grounding, guard, checkpoint and ledger code would move into prompt text.
- A Step Functions Map state per chapter.
  Three to five lanes inside one task need a thread pool, not a state machine, and the task already owns the work directory, the clients and the deadline.
- A vision judge on every attempt, as in the 2026-09-16 design.
  It was removed on 2026-10-04 for cost and noise; the reviewer here runs once per video, after the deterministic audit, with a cap of one fix.
