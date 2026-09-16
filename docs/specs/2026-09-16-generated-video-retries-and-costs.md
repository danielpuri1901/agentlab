# Generated Video Retries and Costs

## Goal

Keep each learning video visually distinct by letting the model write its Manim scene.
Bound every model and render call so one bad attempt cannot hang the daily task.
Show the estimated model cost for each video and the tagged AgentLab AWS cost for the current month.

## Video loop

The storyboard remains grounded in the paper digest and scene plan.
The scene model writes one complete Manim file.
The AST guard rejects unsafe code before execution.
Each safe file gets a low-quality preview render with a hard timeout.
The pipeline samples three frames from each beat at phone width.
The vision judge checks grounding, visual coverage, legibility, and layout.
The next attempt receives the exact guard, render, timing, or judge failure.
The loop stops after four scene-code attempts or the per-video deadline.

The first passing candidate ends the retry loop.
If no candidate passes, the highest-scoring safe render ships.
The fixed template runs only when no candidate renders safely.
The medium-quality final render can fall back to the selected preview.

## Time limits

Each model call has a 180 second timeout.
Each preview render has a 180 second timeout.
The final render has a 300 second timeout.
One video has a 900 second total deadline.
Every render timeout terminates its subprocess through the existing subprocess seam.

## Prompt caching

The stable system instruction gets a Bedrock cache checkpoint.
Paper text, prior source, and failure feedback remain after that checkpoint.
The checkpoint uses Bedrock's default five-minute cache because LiteLLM cannot infer one-hour TTL support from an application inference profile ARN.
The usage ledger records cache reads and writes, so cache value is measurable.

## Cost ledger

Every successful model call records its stage, requested model, pricing model, input tokens, output tokens, cache-read tokens, cache-write tokens, latency, and estimated USD cost.
The estimate uses LiteLLM's pinned local price map and measured response usage.
The video row stores every call plus the summed model cost.
The Telegram caption shows the video's estimated model cost.

The daily worker queries Cost Explorer once for the current month's `project=agentlab` cost.
The query excludes credits and refunds.
The caption labels this value as tagged AWS month-to-date cost because Cost Explorer data can lag and untagged resources are excluded.
Cost Explorer failure never blocks a video.

## Evidence

The story JSON stores every attempt's status, failure detail, timing, judge result, and generated source path.
The worker uploads each attempt's generated source and sampled judge frames under the video's story prefix.
The selected attempt and whether it passed remain explicit.

## Acceptance

A production run must prove these behaviors:

- A failed guard or render produces a corrected model attempt.
- Four failed scene-code attempts stop.
- A renderable candidate with a `fix` verdict ships when no candidate passes.
- A total deadline stops more work.
- Three frame samples exist for each beat.
- Prompt cache controls reach LiteLLM.
- Measured token and cache usage produces a deterministic cost.
- DynamoDB and Telegram show the per-video cost.
- Cost Explorer month-to-date failure degrades to an omitted total.
