# Metaphor videos runbook

Spec: `docs/specs/2026-09-05-metaphor-videos.md`.

## What happens at 10:30

`worker explain` runs the three tracks as before.
The deep read runs on Claude Sonnet 5.5 with thinking switched off, so its 3000 output tokens all go to the digest and the scene plan.
After the deep read, each track tries the story path through `agentlab.story_video.compose_story_video`.
The story and scene models, Claude Opus 5.5 at high effort in production, write the storyboard and the scene code.
The storyboard builds the video around one bold visual metaphor that carries the paper's real mechanism.
The prompt asks for a teaching sequence: title and definition, concrete problem, mechanism, grounded result, application or implication, limitation, and final test question.
A storyboard is rejected only for invalid JSON, a shape error, or a number the source never states.
No rule decides which content goes in which beat.
The scene coder gets Daniel's creative brief and only the hard technical facts, so colour, motion, camera moves, and 3D are all open.
There is no judge and no score.
The scene code is written whole once, then runs the code guard, renders once at medium quality, and has its timing structure checked.
The first file that passes ships.
After a guard finding, a Manim error or timeout, or invalid timing, the scene model gets the current file and the exact error and answers with search/replace edits.
An edit applies only when its search text occurs exactly once in the file.
Otherwise that round writes a whole new file.
A video gets the first file plus at most five fix rounds.
A beat that runs longer than its narration still ships, because the narration track is padded to each beat's length.
Layout problems do not fail a render either: they are recorded as `layout_warnings` in the timing.
If no attempt passes, the story path raises `StoryFailed`, the track writes `STORY_FAILED` and `TRACK_FAILED` events to the ledger, and Telegram gets the digest link instead of a video.
Each shipped story stores its visual direction.
Later videos receive recent directions and must choose a substantially different concept.

## Where to look

- `video#<key>` items in the state table include `render_path`, `attempts`, `selected_attempt`, `story_key`, measured model calls, estimated model cost, and tagged AgentLab month-to-date AWS cost.
- `explain-<yyyymmdd>` items include `STORY_FAILED` and `TRACK_FAILED` events with the reason in `detail`.
- S3 `stories/<key>.json` contains the storyboard, attempts, timing with its `layout_warnings`, and a record of every attempt with its mode (`generate`, `edit`, or `rewrite`) and the exact failure of each failed one.
- S3 `stories/<key>.py` contains the generated scene source that rendered.
- S3 `stories/<key>/attempts/` contains every generated source file.
- CloudWatch `/ecs/agentlab-explain` contains warnings and Manim tracebacks for failed story attempts.

The Telegram caption shows the estimated model cost for that video.
It also shows tagged AgentLab AWS month-to-date spend when Cost Explorer is available.
Cost Explorer data can lag behind current usage.

## Run one paper locally

The local runner requires Boto3 1.41.0 or later with AWS Common Runtime support in the same environment used by `uv run` so it can resolve credentials created by `aws login`.
See the [Boto3 credential guide](https://docs.aws.amazon.com/boto3/latest/guide/credentials.html).
The current locked Boto3 and Botocore 1.40.61 environment lacks `LoginProvider`, so another `aws login` alone does not make the runner work.
Updating the SDK, adding its CRT dependency, and regenerating the lock remain pending setup work.

```bash
aws login
uv run python scripts/story_video_for_url.py https://arxiv.org/abs/<id> out/<name>
open out/<name>/video.mp4
```

This uses Bedrock, Polly, `uvx manim`, and ffmpeg on your machine.
It never touches DynamoDB or Telegram.

## Generated-scene isolation

The AST guard and filtered subprocess environment reduce accidental access to unsafe APIs and inherited AWS environment variables.
They do not create a filesystem or network sandbox.
The renderer still runs as the worker user with the worker filesystem and network available, so environment filtering cannot guarantee that credentials are unreachable.
Credential-isolated rendering remains an unimplemented requirement pending a decision between stronger isolation and a trusted-generated-code boundary.

## Re-render a shipped story

```bash
aws s3 cp s3://<results-bucket>/stories/<key>.py work/paper_story.py
aws s3 cp s3://<results-bucket>/stories/<key>.json work/story.json
cp src/agentlab/story_scene.py work/
```

Build a spec file with `{"storyboard": <story.json's storyboard>, "durations": [...], "captions": [...]}`.
Then run `SCENE_SPEC_JSON=work/spec.json PYTHONPATH=work uvx --python 3.12 manim render -ql work/paper_story.py PaperStory`.

## Knobs

- `STORY_MODEL` and `SCENE_MODEL` are Bedrock model IDs that each default to `DEEP_READ_MODEL`.
  The explain task sets them from the `story_model` and `scene_model` Terraform variables.
- `STORY_PRICE_MODEL` and `SCENE_PRICE_MODEL` price those calls when the model is an application inference profile ARN.
  Terraform sets both to `global.anthropic.claude-opus-5-5` by default.
- `STORY_EFFORT` sets the output effort of the storyboard, scene code, and scene edit calls on the Bedrock Converse path.
  It defaults to `high`.
- `DEEP_READ_THINKING` sets the thinking type of the deep read call on the Bedrock Converse path.
  Terraform sets it to `between_tools`, which switches Sonnet 5.5's thinking off.
  Unset, the call sends no thinking field.
- `DEEP_READ_PRICE_MODEL` prices the deep read when its model is an application inference profile ARN.
  Terraform sets it to `global.anthropic.claude-sonnet-5-5` by default.
- `agentlab.story_video.MAX_ATTEMPTS`, `RENDER_TIMEOUT`, `MODEL_CALL_TIMEOUT`, and `VIDEO_DEADLINE_SECONDS` control the retry and time limits.
- `storyboard.STORYBOARD_SYSTEM` and `scene_code.SCENE_CODE_SYSTEM` are the model prompts.

## Deploy

Push to main. GitHub Actions builds both images and pushes them to ECR (`.github/workflows/build-images.yml`).
Then, from the main checkout:

```bash
AWS_PROFILE=agentlab scripts/deploy_ci_images.sh
```

The deploy script checks that both images for the current commit are in ECR, pins their tags in `infra/*image_tag.auto.tfvars`, and runs `terraform apply`.
`story_model` and `scene_model` have no default, so `infra/runtime.auto.tfvars` must set them before `terraform apply`.
`deep_read_thinking` defaults to `between_tools`, which is meant for a Sonnet 5.5 `deep_read_model`.
If `deep_read_model` is still an older model, set `deep_read_thinking = ""` so the deep read sends no thinking field.
