# 3Blue1Brown-Style Video Generator Design

Date: 2026-10-05.
Status: Daniel approved the design in chat on 2026-10-05.
This document waits for his review before the implementation plan.

## Goal

Every daily video looks and teaches like a 3Blue1Brown (3b1b) video.
Daniel's target is a balance: creative, but also clean, smooth, and reliable.
He named 3b1b as the proxy and said the look matters most.
The change applies to every track (core, classic, novel) and to the planned "built" track.

## Why the videos look wrong today

Four causes in the current code:

1. The storyboard prompt asks for one bold visual metaphor and says "Prefer creative over safe" (`src/agentlab/storyboard.py:227-229`, commit `f256774`, 2026-10-04).
   The model invents a metaphor even when the real object explains better.
2. The scene prompt opens with "Make the most visually striking explanation you can. Invent the visuals." (`src/agentlab/scene_code.py:381`).
   This pushes flashy motion over clear explanation.
3. The render image has no LaTeX (`Dockerfile.video`).
   So the guard bans `MathTex`, `DecimalNumber`, `Matrix`, axis labels, and every other object that typesets math (`src/agentlab/scene_code.py:130`, commit `8e7b955`, 2026-09-05).
   3b1b videos depend on these objects.
4. A caption band takes about 21% of the frame (`STAGE_BOTTOM = -2.3` in `src/agentlab/story_scene.py`).

## Rule for every prompt rule

Every rule in the storyboard and scene prompts has a source of one of these kinds:

- 3b1b's practice: his scene code in `github.com/3b1b/videos`, or Grant Sanderson's own statements.
- A ruling by Daniel.
- A hard limit of this pipeline: the render cap, beat timing, or security.

A rule without a source is removed.
The Evidence section lists the sources.
Daniel set this rule on 2026-10-05, after a draft rule ("3D only when the idea is 3D") turned out to be a guess.

## Decisions (Daniel, 2026-10-05)

- Target look: 3b1b. Balance creative with clean, smooth, and reliable.
- No forced metaphor. The video only has to explain the idea.
- LaTeX on.
- Fewer restrictions. Keep only the security core.
- Captions dropped at first. Later the same day Daniel asked for subtitles: one chunk of at most two lines at a time, in a slim strip at the bottom. The stage ends above that strip, at y = -2.9.
- Approach: prompt rules plus one example scene. Not a prompt rewrite alone, and not a helper library.
- Ground truth: 3b1b's videos and the code behind them.
- Illustrative numbers are allowed inside vectors and matrices. Numbers presented as results stay grounded in the source.

## Scope

In scope: the storyboard prompt, the scene prompt, the scene API text, the guard, the base scene, the render image, one layout fix round, tests, evaluation, and rollout.

Out of scope:

- The "built" track. It gets its own spec next.
- A render sandbox. The security fence stays as it is.
- A vision-model judge.
- Pi creatures, glow dots, and other objects that only exist in 3b1b's own Manim version (manimgl).
- The deep-read prompt, the models, and the narration voice.

## Design

### 1. Storyboard prompt (`src/agentlab/storyboard.py`)

`STORYBOARD_SYSTEM` is replaced.
The new rules and their sources:

| Rule | Source |
|---|---|
| Role: director of a short explainer in 3Blue1Brown style. Goal: Daniel understands the mechanism on the first watch. | Daniel |
| Let the explanation form around the visuals. | Grant, 2023 interview (Evidence, G2) |
| Go from concrete to abstract. Show a concrete example before the general form. | Grant, SoME1 video (G1); `attention.py` opens with a dialogue before the math |
| Keep one or two examples front and center. | Grant, SoME2 results video (G3) |
| No forced metaphor. Pick what explains best: the real object, a graph, an equation, a geometric picture, or a diagram of the real parts. A metaphor is allowed when it explains. | Daniel |
| One colour per concept, the same in formulas and pictures for the whole video. Two to four concept colours per scene. | His code (Evidence, table rows "Fixed concept colours") |
| Objects persist and transform across beats. New objects grow out of copies of old ones, so the viewer sees where they come from. | His code (copy-transform row); Grant, manim demo (G5) |
| Nothing is there for decoration. | Grant, 2026 interview (G4) |
| 3D is for spatial ideas: vector and embedding spaces, surfaces, and layers stacked in depth. | His code (3D row) |

Two fields change meaning, with no schema change:

- `visual_focus` names the visual approach in one sentence. It is no longer a metaphor.
- `mapping` is the colour key. Each item maps a term to its colour and shape.

Unchanged: the role order, the beat count, the narration limits, the title and definition in the first beat, the number grounding check, and the ban on em dashes.

The note about recent visual directions changes.
Old: "Choose a substantially different visual concept, composition, and motion system."
New: do not reuse these visual ideas, and keep the same style.
`build_storyboard_prompt` drops the words "through one bold visual metaphor".

### 2. Scene prompt (`src/agentlab/scene_code.py`)

The first paragraph of `SCENE_CODE_SYSTEM` becomes: "Animate it the way 3Blue1Brown does: clean, smooth, and every motion explains something."

Look:

| Rule | Source |
|---|---|
| Background `#000000`. Text font CMU Serif. | His `custom_config.yml` |
| Palette: the manimgl colour constants. Where Manim Community (CE) uses a different value, `story_scene` exports the manimgl value. | His code; `manimlib` constants |
| On-screen text is short: median 2 words, at most 8. Titles use font size 60 to 72, labels 24 to 36. | His 2025 code (sample of about 50 `Text` strings) |
| Text that sits over lines or grids gets a black background stroke. | His code (`set_backstroke` in 56% of recent files) |
| Titles and equations go at the top edge. Labels sit next to what they name. | His code (`to_edge(UP)`; `next_to` 96 to 173 uses per file) |

Motion:

| Rule | Source |
|---|---|
| Most animations use the default 1 s. Bigger moves take 2 to 5 s. Longer runs are only for a process that shows time passing. Keep the default easing. | His code (timing rows) |
| Stagger groups with `LaggedStart` or `LaggedStartMap`: `lag_ratio` about 0.5 for a few objects, 0.01 to 0.25 for many. | His code (`lag_ratio` values in `attention.py` and `mlp.py`; 0.5 in the 2017 series) |
| Vocabulary: `FadeIn` and `FadeOut` with a small shift, `Create`, `Write`, `ReplacementTransform`, `FadeTransform`, `TransformFromCopy`, `TransformMatchingTex`, `MoveToTarget`, `GrowArrow`. | His code (class counts) |
| To point at something: dim the rest (opacity 0.25 to 0.35, or a black overlay at 0.75 to 0.8), then a `SurroundingRectangle` or a passing flash. | His code (`attention.py`) |
| Continuous change: `ValueTracker` with updaters or `always_redraw`. `DecimalNumber` for numbers that change. | His code (ValueTracker in 49% of recent files) |
| The camera is a layout tool: pan and zoom across one large board. 3D orbits are slow: 4 to 12 s, and ambient rotation of about 1 to 2 degrees per second. Pin 2D math to the screen over a 3D view. | His code (`attention.py`: camera moves in 37 plays; `mlp.py`: orbits of 4 to 12 s) |

Math and numbers:

| Rule | Source |
|---|---|
| `MathTex` for formulas, written as raw strings, coloured with `tex_to_color_map` from the colour key. | His code (`t2c` in 48% of recent files); CE mapping |
| `Axes`, `NumberLine`, and `NumberPlane` with labels. `Matrix` and `DecimalNumber` are allowed. | LaTeX is now in the image |
| A vector is a bracketed column of numbers. A matrix has brackets and ellipses. A `Brace` labels the true size. | His code (`NumericEmbedding`, `WeightMatrix`, the brace labelled 12,288) |
| Words and result numbers on screen come from the storyboard. Entries inside vectors and matrices may be illustrative values that stand for learned numbers. Never present them as results. | Daniel; his code (`RandomizeMatrixEntries`) |

The prompt carries a translation table from manimgl idioms to Manim CE 0.21.
The implementation checks every row against Manim CE 0.21 and removes a row that fails the check.

| manimgl | Manim CE |
|---|---|
| `ShowCreation` | `Create` |
| `Tex` (math) | `MathTex` |
| `TexText` | `Tex` |
| `t2c={...}` | `tex_to_color_map={...}` |
| `TransformMatchingStrings` | `TransformMatchingTex` or `TransformMatchingShapes` |
| `FlashAround` | `Circumscribe`, or `ShowPassingFlash` on a `SurroundingRectangle` |
| `VFadeIn` | `FadeIn` |
| `frame.reorient(0, 0, 0, center, height)` | `self.move_camera(frame_center=center, zoom=8 / height)` |
| `frame.reorient(theta, phi, ...)` | `self.move_camera(phi=..., theta=...)`, in radians |
| `frame.add_ambient_rotation()` | `self.begin_ambient_camera_rotation(rate=...)` |
| `fix_in_frame()` | `self.add_fixed_in_frame_mobjects(...)` |
| `.animate.f().set_anim_args(run_time=2)` | `.animate(run_time=2).f()` |
| `set_backstroke(BLACK, 5)` | `set_stroke(BLACK, 5, background=True)` |
| `GlowDot` | `Dot` |

Example scene:

- One real scene: LoRA (arXiv 2106.09685), taken from the first local run of the new pipeline and polished by hand (Daniel: the one-shot example should be real). It replaced an earlier invented SortNet scene.
- It follows the rules above: a colour key, transforms from copies, staggered groups, a camera pan, and `MathTex`.
- It is written for this repository. No 3b1b code is copied, because `3b1b/videos` is licensed CC BY-NC-SA 4.0.
- The prompt says: copy the style, never the content.
- The CI render test also uses it (see Testing).

Scene API text (`STORY_SCENE_API`):

- Constants: `BACKGROUND` is `#000000`, plus the palette. The stage bounds become the full frame minus a 0.5 margin, his edge buffer.
- `counter` and `freeze` are removed. `DecimalNumber` with a `ValueTracker` replaces them, as in his code.
- The sentences about captions are removed.

Unchanged: the class and beat structure, the timing budgets, the 10-minute render cap, the file size cap, and the import list (apart from the guard changes below).

### 3. Render image (`Dockerfile.video`)

- Install TeX Live the way Manim's own image does: `install-tl` with a minimal profile, then `tlmgr install` of Manim's documented package list without `ctex` (Manim docs v0.21.0, installation page; `ManimCommunity/manim` `docker/Dockerfile`).
- Copy `/usr/local/texlive` into the final stage and put its `bin` folder on `PATH` (the `aarch64-linux` folder on the arm64 runner).
- Install the TeX Live package `cm-unicode` (CMU fonts, SIL Open Font License). Register its OpenType folder with fontconfig, so Pango finds "CMU Serif".
- The test stage needs TeX too, because it renders the example scene.
- Images still build only in GitHub Actions. CI reports the new image size.

### 4. Guard (`src/agentlab/scene_code.py`)

Allowed now:

- The whole "anything that needs LaTeX" group: `Tex`, `MathTex`, `SingleStringMathTex`, `DecimalNumber`, `Integer`, `Variable`, `Title`, `BulletedList`, the `Matrix` and `Table` families, `BarChart`, `get_axis_labels`, `get_x_axis_label`, `get_y_axis_label`, `add_coordinates`, and `include_numbers=True`.
- Helper classes, when they are not scenes, with `super` and `super().__init__`.
  His scenes build reusable parts as classes, so the model will write them too.
  Only `PaperStory` may subclass `StoryScene` or any other scene class.
- `Code`, only with the keyword `code_string` and no positional argument. A file path could read a secret into a frame.
- `np.random`, through a namespace allowlist: `random`, `uniform`, `normal`, `randint`, `choice`, `seed`, `default_rng`.
- These numpy functions, which the allowlist lacks today: `tanh`, `sinh`, `cosh`, `meshgrid`, `tile`, `repeat`, `roll`, `eye`, `identity`, `diag`, `transpose`, `flip`, `exp2`, `log2`, `mod`, `histogram`, `convolve`, `polyfit`, `polyval`.
- numpy file input and output functions stay blocked (`load`, `save`, `fromfile`, `memmap`, `loadtxt`, `savetxt`, `genfromtxt`, `ctypeslib`).

Still banned: `open`, `exec`, `eval`, `compile`, `__import__`, `globals`, `locals`, `getattr`, `setattr`, `delattr`, `type`, `object`, `dir`, `vars`, `breakpoint`, `input`, `os`, `sys`, `subprocess`, `socket`, `pathlib`, `shutil`, `importlib`, `builtins`, dunder attributes, `camera`, `renderer`, `file_writer`, `window`, `config`, `ImageMobject`, `SVGMobject`, `add_sound`, and `interactive_embed`.

Why the core stays: the render runs inside the worker with its filesystem, its network, and a reachable AWS role (`src/agentlab/video_render.py` docstring).
The model writes the scene code after it reads untrusted web text.

### 5. Base scene (`src/agentlab/story_scene.py`)

- No caption band. `_swap_caption` and its call in the construct loop go away.
- The stage becomes the full frame minus a 0.5 margin.
- `story_video.py` still writes the `.srt` file.
- `BACKGROUND` becomes `#000000`.
- The stage is the frame minus the 0.5 margin: `STAGE_TOP` 3.5, `STAGE_BOTTOM` -3.5, `STAGE_RIGHT` 6.61, `STAGE_LEFT` -6.61.
- The default `Text` font becomes CMU Serif. The implementation checks that `Text.set_default(font="CMU Serif")` works in Manim CE 0.21.
- The module exports the palette.

### 6. Layout check and one layout round (`story_scene.py`, `story_video.py`)

Today the audit writes off-stage boxes to `beat_times.json` as `layout_warnings`.
Nothing reads them.

New audit at the end of each beat:

- Collect the visible text objects: `Text`, `MarkupText`, `Paragraph`, `Tex`, `MathTex`, `DecimalNumber`, and `Integer`.
  Visible means a fill or stroke opacity above 0.05.
  Skip a text object that sits inside another text object.
- When the camera is flat, map each box to screen coordinates: screen point = (world point - frame center) x zoom.
- Report text that leaves the frame margin.
- Report two text objects whose boxes overlap by more than 10% of the smaller box.
- Report text that lies partly inside a rectangle or polygon: more than 10% and less than 90% of it inside. Fully inside is a label in its box; fully outside is a label beside it.
- Report a line or arrow that runs through the middle of a text (the text box shrunk by 15% on each side), so an arrow that stops at a label's edge stays quiet. Curves are left out, because their boxes would flag every label near a graph.
- Skip a box that lies wholly outside the frame.
  When the camera pans across a large board, the parts it does not show are off screen on purpose.
- Count each text object once.
  Manim leaves a temporary group after `Swap` and similar animations, so the same text can sit in two families.
- When the camera is tilted (phi, theta, or gamma not at the default), skip the check for that beat and add the beat number to `layout_skipped` in the timing output.

Loop change in `_compose`:

- A render with valid timing but with layout warnings becomes the fallback video.
- If the layout round is unused and enough time remains for one more render, the next attempt is an edit round with the warnings as its feedback.
- If that attempt renders with no warnings, it ships.
- If that attempt fails, or still has warnings, the fallback ships.
- Each video gets at most one layout round.
- A layout warning never makes a video fail.

### 7. Cost and time

- The scene prompt grows by the example scene and the translation table: about 3,000 to 4,000 tokens, in the cached system prompt.
- LaTeX compiles each new formula once per render. Manim caches the result on disk.
- The layout round adds at most one edit call and one render.
- The video deadline stays at 2,400 s.
- A layout round only starts when at least 1,260 s remain: one edit call (600 s), one render (600 s), and the mux.
- The final mux applies 3b1b's saturation of 1.5 (his `custom_config.yml`, `file_writer`).
  This re-encodes the video once with libx264.

### 8. Checkpoints

The storyboard and scene checkpoint fingerprints include the prompt text.
Without this, a replay of a paper rendered before the change would reuse the old storyboard and scene from S3.

## Testing

Unit tests:

- Guard: each LaTeX name passes. Each security name fails. `Code(code_file=...)` and `Code("path")` fail. `np.random.random` passes. `np.load` fails.
- Layout geometry: overlap detection, the screen mapping with a pan and with a zoom, and the skip for a tilted camera.
- Compose loop with fake renders:
  - warnings, then a clean render: the clean render ships;
  - warnings twice: the fallback ships;
  - warnings, then a failure: the fallback ships;
  - there is never a second layout round.
- The existing suite stays green.

CI: the Docker test stage renders the example scene at low quality with fixed beat durations.
The test checks that the video exists and that its length equals the sum of the durations within 0.5 s.

Quality evaluation, which is the real test:

- Three papers: the three most recent production videos, one per track where possible.
- Before: their production videos from S3. After: the new pipeline on the same URLs.
- Daniel compares frames side by side, next to frames from 3b1b videos. Those reference frames stay local and private and are never committed.
- Pass criteria:
  1. A paused frame passes for 3b1b, in Daniel's judgment.
  2. Daniel rates the videos CLEAR on the first watch.
  3. Render success is not worse than before. Measure attempts per video and failed tracks over the evaluation runs and the first week in production (ledger field `attempts`, event `TRACK_FAILED`).
- Local runs need installs (the Manim `video` dependency group, TeX packages, CMU fonts) and video downloads. Each one needs Daniel's OK, with a manifest, before it happens.

## Rollout and rollback

1. The work happens on the branch `3b1b-style-videos`.
2. The local quality evaluation passes.
3. The branch merges to main. GitHub Actions builds the app and video images (tags `app-<sha7>` and `video-<sha7>`), because images only build from main.
4. `scripts/deploy_ci_images.sh` deploys the new tags.
5. One replay runs in the cloud (`EXPLAIN_URL`) before the next 10:30 run, as the check on the real path.
6. The next 10:30 run uses the new style on every track.

Rollback: deploy the previous video image tag.
Roll back if the cloud replay fails, or if the first daily run loses a track that worked before.

## Risks

- LaTeX errors become a new kind of render failure. The fix round gets the LaTeX error line, and the prompt asks for raw strings.
- A bigger image makes the first image pull on Fargate slower. CI reports the size. If the pull time hurts, trim packages.
- Overlap is not checked on beats with a tilted camera.
- Illustrative numbers could look like results. The rule keeps them inside vectors and matrices, and the narration never cites them.
- The default `smooth` easing in Manim CE is a sigmoid. The manimgl `smooth` matches CE `smootherstep`. This design does not change easing. Revisit it if the motion looks wrong.
- Some Manim CE colour constants differ from manimgl. The implementation reads both sources and exports the manimgl values that differ.

## Evidence

Source repository: `3b1b/videos` at commit `306a134`, read on 2026-10-05 through the GitHub API.
Nothing was cloned or copied into this repository.
A research agent and its sub-agents collected the counts below by parsing each file with Python `ast`, cross-checked with regex.

Files read in full: `_2017/nn/part1.py`, `_2017/nn/part2.py`, `_2017/nn/part3.py`, `_2024/transformers/attention.py`, `_2024/transformers/mlp.py`, `_2024/puzzles/max_rand.py`, and `_2025/laplace/derivative_supplements.py`.
File shares come from GitHub code search over the 2022 to 2026 folders (131 files) and the 2016 to 2020 folders (213 files).

### Deep-learning videos, per file

| Measure | nn part 1 (2017) | nn part 2 (2017) | nn part 3 (2017) | attention (2024) | mlp (2024) |
|---|---|---|---|---|---|
| `self.play` calls | 275 | 189 | 331 | 216 | 183 |
| Plays with no explicit `run_time` | 85% | 78% | 90% | 65% | 80% |
| Plays with 4 or more animations | 8% | 10% | 6% | 18% | 19% |
| `LaggedStart` and `LaggedStartMap` uses | 35 | 28 | 64 | 122 | 40 |
| Copy-transform idiom | not counted | not counted | 58 `ReplacementTransform` calls, mostly of copies | 46 `TransformFromCopy` | 26 `TransformFromCopy` |
| Camera moves | none | none | none | 37 plays | 34 plays |
| Scenes with 3D | 0 of 48 | 0 of 60 | 0 of 35 | 4 full and 2 partial of 20 | 2 full and 2 partial of 16 |
| Fixed concept colours | positive weight green, negative red, bias blue, sigmoid yellow | positive blue, negative red, cost red, bias maroon | cost red, weight blue, bias maroon, z green, target yellow | query yellow, key teal, value red, output pink | embedding yellow, weights blue, neuron on blue, off red |

Explicit `run_time` values cluster at 1 to 2 s.
The longest are 5 s in the 2017 series and 20 s in 2024 (a zoom-out over a 50 x 50 grid).
Custom easing (`rate_func`) appears in under 5% of plays in the 2024 files.
On-screen text is short labels and questions.
Long text appears only when the text is the subject, such as a poem or a story.

### Recent files (2022 to 2026), share of files that use each item

`.animate` 87%, `lag_ratio` 83%, `FadeIn` 82%, `ShowCreation` 80%, `Write` 79%, `SurroundingRectangle` 63%, `ReplacementTransform` 57%, `set_backstroke` 56%, `FadeTransform` 54%, `ValueTracker` 49%, `t2c` 48%, `FlashAround` 39%, `TransformMatchingTex` 29%, `Indicate` 8%.
3D markers appear in 41% of recent files, but within files on flat topics only 2 of 60 scenes use 3D.
His configuration (`custom_config.yml`) sets a `#000000` background and the CMU Serif font.

### Grant Sanderson's statements

The research agent collected these on 2026-10-05.
They are paraphrased here and were not checked again.

- G1: Structure an explanation from the concrete to the abstract. SoME1 announcement video, 2021-07-16, `youtube.com/watch?v=ojjzXyQCzso`.
- G2: Put the visuals first and let the explanation form around them. Interview, `aperiodical.com/2023/09/eipi-to-watch-3blue1brown/`, 2023-09-20.
- G3: Keep one or two examples front and center. SoME2 results video, 2022-10, `youtube.com/watch?v=cDofhN-RJqg`.
- G4: No animation or visual just for its own sake. Interview, `analyticsindiamag.com`, 2026-07-20.
- G5: Long scenes help because they share context. Manim demo, 2024-10-12, `youtube.com/watch?v=rbu7Zu5X1zI`.
- G6: Motion should almost always be smooth; linear when the animation is time itself. Same demo.

### License

`3b1b/videos` is licensed CC BY-NC-SA 4.0 (`LICENSE.txt`). Manim itself is MIT.
This design copies no code from it.
The example scene is original work.
