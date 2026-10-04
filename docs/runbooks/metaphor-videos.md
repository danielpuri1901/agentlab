# Metaphor videos runbook

Spec: `docs/specs/2026-09-05-metaphor-videos.md`.

## What happens at 10:30

`worker explain` runs the three tracks as before.
After the deep read, each track tries the story path through `agentlab.story_video.compose_story_video`.
The story and scene models, Claude Opus 5.5 at high effort in production, write the storyboard and the scene code.
The storyboard builds the video around one bold visual metaphor that carries the paper's real mechanism.
Each generated video follows a stable teaching sequence: title and definition, concrete problem, mechanism, grounded result, application or implication, limitation, and final test question.
The scene coder gets Daniel's creative brief and only the hard technical facts, so colour, motion, camera moves, and 3D are all open.
There is no judge and no score.
Each attempt writes the scene code, runs the code guard, renders once at medium quality, and checks the timing structure.
The first attempt that passes ships.
Only a coder error, a guard finding, a Manim error or timeout, or invalid timing costs another attempt, up to three, and the coder gets the exact error back.
A beat that runs longer than its narration still ships, because the narration track is padded to each beat's length.
Layout problems do not fail a render either: they are recorded as `layout_warnings` in the timing.
If no attempt passes, the story path raises `StoryFailed`, the track writes `STORY_FAILED` and `TRACK_FAILED` events to the ledger, and Telegram gets the digest link instead of a video.
Each shipped story stores its visual direction.
Later videos receive recent directions and must choose a substantially different concept.

## Where to look

- `video#<key>` items in the state table include `render_path`, `attempts`, `selected_attempt`, `story_key`, measured model calls, estimated model cost, and tagged AgentLab month-to-date AWS cost.
- `explain-<yyyymmdd>` items include `STORY_FAILED` and `TRACK_FAILED` events with the reason in `detail`.
- S3 `stories/<key>.json` contains the storyboard, attempts, timing with its `layout_warnings`, and a record of every attempt with the exact failure of each failed one.
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
- `STORY_EFFORT` sets the output effort of the storyboard and scene code calls on the Bedrock Converse path.
  It defaults to `high`.
- `agentlab.story_video.MAX_ATTEMPTS`, `RENDER_TIMEOUT`, `MODEL_CALL_TIMEOUT`, and `VIDEO_DEADLINE_SECONDS` control the retry and time limits.
- `storyboard.STORYBOARD_SYSTEM` and `scene_code.SCENE_CODE_SYSTEM` are the model prompts.

## Deploy

```bash
scripts/build_and_push_video_image.sh
cd infra && terraform apply
```

The build script rewrites `infra/video_image_tag.auto.tfvars` with the video image tag.
`story_model` and `scene_model` have no default, so `infra/runtime.auto.tfvars` must set them before `terraform apply`.
