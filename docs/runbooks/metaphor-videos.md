# Metaphor videos runbook

Spec: `docs/specs/2026-09-05-metaphor-videos.md`.

## What happens at 10:30

`worker explain` runs the three tracks as before.
After the deep read, each track tries the story path through `agentlab.story_video.compose_story_video`.
The story path creates a storyboard and narration, then makes up to three low-quality generated scene attempts and judges every renderable attempt.
It renders the best candidate at medium quality for delivery.
If that final render fails or has invalid timing, the compose loop uses the accepted low-quality preview instead.
If the story path cannot produce any renderable scene and raises `StoryFailed`, the track writes a `STORY_FALLBACK` event to the ledger and renders the existing template.
The Telegram message looks the same in either path.

## Where to look

- `video#<key>` items in the state table include `render_path` (`story` or `template`), `attempts`, `judge_score`, and `story_key`.
- `explain-<yyyymmdd>` items include `STORY_FALLBACK` events with the reason in `detail`.
- S3 `stories/<key>.json` contains the storyboard, judgement, attempts, and timing.
- S3 `stories/<key>.py` contains the generated scene source that rendered.
- CloudWatch `/ecs/agentlab-explain` contains warnings and Manim tracebacks for failed story attempts, including a warning when the accepted preview replaces a failed final render.

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

- `STORY_MODEL`, `SCENE_MODEL`, and `JUDGE_MODEL` are Bedrock model IDs that each default to `DEEP_READ_MODEL`.
- `agentlab.story_video.MAX_ATTEMPTS`, `LOW_RENDER_TIMEOUT`, and `FINAL_RENDER_TIMEOUT` control the retry and render limits.
- `agentlab.story_scene.PER_BEAT_OVERRUN_LIMIT` and `TOTAL_OVERRUN_LIMIT` control accepted scene timing.
- `storyboard.STORYBOARD_SYSTEM`, `scene_code.SCENE_CODE_SYSTEM`, and `frame_judge.JUDGE_SYSTEM` are the model prompts.

## Deploy

```bash
scripts/build_and_push_video_image.sh
cd infra && terraform apply
```

The build script rewrites `infra/video_image_tag.auto.tfvars` with the video image tag.
