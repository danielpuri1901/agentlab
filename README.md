# AgentLab

I don't have time to read papers individually anymore.
I am restrained by time, so AgentLab turns them into short visual lessons I can watch each day.

## Play the architecture game

[Play the game](https://danielpuri1901.github.io/agentlab/architecture/video-pipeline-game.html)

The source lives at [`docs/architecture/video-pipeline-game.html`](docs/architecture/video-pipeline-game.html).

The standalone game includes:

- A 26-step guided run.
- A cache boundary lab.
- A failure drill.
- A durable state map.
- Live cost, time, retry, cache, quality meters.

It needs no server, package install, CDN, network request.

## What it does

AgentLab finds research papers, removes repeats, ranks the candidates, reads the selected paper, then builds a narrated video.

Three daily tracks give the feed some range:

- CORE follows my current interests.
- CLASSIC works through a curated list.
- NOVEL looks farther away from my usual topics.

Each track runs inside its own failure boundary.
A broken video cannot cancel the remaining tracks.

## Video pipeline

```text
EventBridge Scheduler
  -> ECS Fargate
  -> paper selection
  -> deep read
  -> storyboard
  -> Polly narration
  -> generated Manim scene
  -> static Python guard
  -> low-resolution preview
  -> timing check
  -> sampled frames
  -> visual judge
  -> quality gate
  -> final render
  -> S3 + DynamoDB + Telegram
```

The scene loop gets four attempts within a 900-second deadline.
Every preview must pass the visual judge before delivery.
No pass means no video.
Telegram receives the paper digest plus the exact failure instead.

## Feedback loop

Telegram exposes two small rating sets:

- COOL / MEH / SKIP records topic taste.
- CLEAR / UNCLEAR records teaching quality.

Selection feedback only acts as a bounded tiebreak inside the baseline top three.
This keeps a small label set from taking over the feed.

## Local setup

```bash
uv sync
uv run pytest
```

The default suite uses offline fakes for AWS calls.
Render tests need Manim plus FFmpeg.

## AWS setup

Terraform lives in [`infra/`](infra/).

Copy the safe example file:

```bash
cp infra/runtime.auto.tfvars.example infra/runtime.auto.tfvars
```

Fill the local file with your email, unique S3 bucket, Telegram chat ID, Bedrock model IDs.
Git ignores this file.

Runtime bot secrets live in AWS Systems Manager Parameter Store.
Terraform state stays local plus ignored.

## Security

Never commit credentials, `.env` files, Terraform state, private keys, local variable files.

The repository ignore rules cover those file types at every directory depth.
The committed example values are fake.

See [`SECURITY.md`](SECURITY.md) before reporting a possible credential leak.

## Main commands

```bash
uv run agentlab worker explain
uv run agentlab worker propose
uv run agentlab worker flush-pings
uv run agentlab cloud submit --help
```

The daily paper-video route uses EventBridge Scheduler plus ECS Fargate.
The paired experiment route uses SQS, EventBridge Pipes, Step Functions, ECS Fargate.

## Status

This is a personal research system.
The code changes quickly because the output teaches me what to fix next.
