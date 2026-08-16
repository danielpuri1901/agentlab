# Phase 0 synthesis: what the research changes and what we build

Date: 2026-08-16.
Inputs: the eight reports in `docs/research/`, each with primary-source citations.
Status: proposed, awaiting Daniel's ratification of the decisions in section 6.
Rule of thumb used throughout: facts below are verified in the linked reports; anything still uncertain is in section 8.

## 1. The headline: the blueprint survives, but its identity moves

The handoff blueprint (`docs/handoff-v1.0.md`) proposed building execution, telemetry, evaluation, and experimentation layers.
The research shows six of those eight layers are already well served by mature open source, and that the blueprint's implied novelty (promotion gates, environment perturbation, cost-per-quality) already ships in existing products.
See `research/existing-systems-overlap.md` sections 3 and 5.

What is genuinely unoccupied, and what AgentLab's identity should be:

1. The promotion decision as a statistical claim.
Every shipping gate blocks on a fixed threshold or raw delta, while measured run-to-run noise on SWE-bench Verified is 2.2 to 6.0 points even at temperature 0.
A gate that sizes its own trial count via power analysis, reports a confidence interval on the delta, and returns INCONCLUSIVE when the interval straddles zero exists nowhere as a product.
2. Research-claim ingestion: compiling a published claim into a pre-registered, sized, falsifiable experiment.
No prior art found.
3. Perturbation families as versioned, reusable, task-attached registry artifacts rather than a benchmark phase.

So AgentLab = a thin, statistically honest decision layer plus a claim-to-experiment compiler, running on adopted open-source plumbing, on AWS infrastructure we build ourselves for the learning goal.

## 2. Verified cost model

From `research/aws-infra-pricing.md` and `research/bedrock-model-pricing.md`.

Infrastructure:

- Region: eu-west-1 (Ireland). Fargate priced identically to us-east-1; Frankfurt is ~15% more.
- ARM/Graviton Fargate is 20% cheaper than x86.
- A realistic dev month (200 task-runs of 10 min at 1 vCPU/2GB) is about $12.90.
- Mandatory cost hygiene: no NAT gateway ($35/month idle), public subnet with `assignPublicIp=ENABLED` plus free gateway endpoints for S3 and DynamoDB, no idle load balancers, log retention policies, and never use `experiment_id` as a CloudWatch metric dimension ($0.30 per metric-month explodes).
- Default Fargate quota is 6 On-Demand vCPUs per region: request an increase early or fan-out caps at 6 concurrent 1-vCPU tasks.
- Step Functions Standard, not Express: only Standard supports the ECS `.sync` run-a-job pattern, and Express caps at 5 minutes.

Models (all prices per million tokens, global endpoint, which carries no premium from the Netherlands):

| Model | Input | Output | 60-run experiment, medium cached profile |
|---|---|---|---|
| Nova Lite | $0.06 | $0.24 | ~$1.15 |
| Haiku 4.5 | $1 | $5 | ~$18.90 |
| Sonnet 5 | $2 | $10 | ~$37.80 |
| Opus 5 | $5 | $25 | ~$94.50 |
| Fable 5 | $10 | $50 | ~$189.00 |

- Prompt caching cuts a medium run by ~51% at 80% cache-read, and cache reads do not count against rate quotas: caching is mandatory, not optional.
- Batch inference is 50% off but unavailable for the Claude 5 family and does not stack with caching: batch is a judging lever, not an agent-loop lever.
- Claude 4.7+ tokenizer emits ~30% more tokens for the same text, and the Claude rows in the table above do NOT include that adjustment: apply +30% to every Claude figure when budgeting.

The credit trap:

- Claude on Bedrock bills through AWS Marketplace, and AWS promotional credit terms exclude Marketplace charges.
- Amazon Nova is first-party and certain to draw from credits; Claude coverage is unverified and plausibly excluded.
- First AWS action, before any architecture commitment: a ~$5 smoke test per model family, then check which billing bucket the charge lands in.

Proposed budget allocation of the $1,000 (assuming the smoke test passes; see section 6 if it does not):

- $150 infrastructure (covers ~10 dev months at measured burn plus quota headroom).
- $600 experiments (roughly: pipeline debugging on Nova Lite for cents, main experiments on Haiku/Sonnet, selective frontier confirmation passes).
- $250 reserve, untouched until Phase 3 results justify spending it.

## 3. Statistics: the science layer that is AgentLab's core

From `research/evaluation-methods.md`.

- Design: paired (baseline and candidate on identical tasks and seeds), task-clustered standard errors.
- Detecting a 10-point effect on binary success needs ~60 tasks x 5 repeats x 2 configs = 600 trials ($300 to $600 on mid-tier models).
- Caution: those counts derive from variance parameters the source report explicitly labels assumed and illustrative; if the paired correlation is 0.5 instead of the assumed 0.8, required task counts roughly double. A cheap local variance pilot (~20 tasks x 5 repeats on a cheap model, a few dollars) must replace these assumptions with measured values before any budget commitment.
- With 20 to 50 paired trials we can detect 20 to 30 point effects, not 5-point ones.
- Consequence: first experiments target large claimed effects or continuous metrics (tokens, steps, information recall), where small n genuinely suffices.
- Repeats saturate past ~5 per task; distinct tasks are the binding constraint.
- Metrics: pass@1 for capability claims, pass^k for reliability claims; they diverge by up to 25 points.
- Judges: different model family than the agent (same-family self-preference is measured), calibrated against ~100 human-labeled traces, gated on TPR and TNR above 0.90, audited on a cadence.
- Promotion rule: superiority on one primary metric plus non-inferiority on every protected metric, with the INCONCLUSIVE verdict as a first-class outcome.

## 4. Architecture: build-vs-adopt

From `research/existing-systems-overlap.md`, `research/harness-landscape.md`, `research/agentcore-and-managed-evals.md`, `research/production-case-studies-and-isolation.md`.

Adopt:

- Inspect (UK AISI, MIT): eval core: tasks, solvers, scorers, model grading, eval sets with retry/resume, limits, log viewer, 171 ready evals.
- Inspect Scout (MIT, young: pin the version, wrap thinly): trajectory store with Parquet/S3 backend and importers for Phoenix, LangSmith, MLflow, Weave, and Claude Code sessions.
- Harbor (Terminal-Bench): containerized task/environment format with network policy, MCP servers, and 12+ sandbox providers, for scale-out workloads.
- Langfuse annotation queues for human review, when human evaluation starts (not v0.1).
- OTel GenAI as the trace interchange layer, emitted from an Inspect hook; Inspect's EvalLog stays the canonical record; never hard-code unstable `gen_ai.*` attribute names into gate logic.

Build (thin):

- The statistical decision layer over Inspect eval sets (power sizing, paired CIs, INCONCLUSIVE verdicts, promotion records).
- The claim-to-experiment compiler (Phase 4, no prior art).
- The perturbation-family registry (modest extension over ReliabilityBench/Gaia2 ideas).
- The AWS execution fabric: SQS queueing, Step Functions Standard orchestration, Fargate isolation, DynamoDB state, S3 artifacts.
This is deliberately built rather than adopted because distributed-systems learning is a co-goal of the project.

Wrap or skip:

- AgentCore Evaluations: wrap behind an evaluator adapter only; at ~$31 per experiment run of built-in evaluator cost it cannot be the default.
- AgentCore Runtime: genuine alternative (per-session microVM, memory sanitization) but skip for v0.1; owning the Fargate runtime is the learning goal.
Watch AgentCore Optimization: AWS now ships managed A/B testing over Evaluations, which overlaps AgentLab's purpose.
- Bedrock Agents Classic: closed to new customers 2026-07-30, off the table.
- awslabs/agent-evaluation: dormant, ignore.

Harness strategy:

- Only four abstractions are genuinely common across all nine surveyed harnesses: task entry point, JSON-Schema tools, ordered event stream, terminal stop with reason.
The interface promises only these; checkpoint/resume and human-in-loop become declared optional capabilities.
- First two harnesses: AWS Strands Agents (works with any Bedrock model including Nova, so it is the cheap-iteration workhorse) and Claude Agent SDK (Claude-native, opposite end of the opacity axis, scientifically interesting contrast).
- Instrument the environment and the model proxy, not the harness internals: the proven pattern from Inspect and SWE-bench adapters.
- Justification for the whole project: published evidence says harness choice moves benchmark scores about as much as model choice.

Security model (v0.1):

- Fargate task as the isolation boundary (AWS documents the task, not the container, as a security boundary).
- Agent task role gets effectively zero AWS permissions.
- Every privileged action, including Bedrock invocation, brokered through a control-plane service.
The broker doubles as the model proxy observation point and the token-cost meter: three requirements, one component.
- Sandbox enforces the boundary, never the prompt (the Replit July 2025 incident is the canonical counterexample).
- Standards to map against: OWASP GenAI LLM Top 10 2026 and OWASP Top 10 for Agentic Applications 2026.

## 5. Revised vertical slice (v0.1)

One complete paired experiment, end to end:

1. `agentlab experiment run <spec>` submits a spec: hypothesis, baseline config, candidate config, task set, trial count (sized by the stats module), budget cap.
2. Runs are enqueued on SQS; a Step Functions Standard state machine launches each run as an ARM Fargate task (public subnet, zero-permission task role) via the ECS `.sync` pattern.
3. Inside the task: a harness adapter (Strands first) executes the task; all model calls go through the broker to Bedrock (Nova Lite as debug workhorse).
4. Trajectory captured as Inspect EvalLog to S3; run state transitions persisted in DynamoDB; logs to CloudWatch with retention set.
5. One deterministic scorer evaluates outcomes; Scout ingests trajectories for querying.
6. The stats module computes the paired delta with CI and emits a verdict: PROMOTE, HOLD, or INCONCLUSIVE, stored as an immutable experiment record with full cost attribution (tokens from the broker, infra from cost tags).
7. A report renders quality, reliability, latency, cost, and failure classes.

Explicitly deferred from the slice: environment perturbation, LLM-as-judge, human review, research ingestion, drift detection, dashboards beyond one report.

Local-first rule: the whole slice must also run locally (Docker instead of Fargate, local queue, LocalStack or real cheap AWS for DynamoDB/S3) so AWS spend only starts when a run is real.

## 6. Decisions (ratified by Daniel, 2026-08-16, except where noted)

1. RATIFIED: adopt-heavy stack: Inspect + Scout + Harbor + OTel-via-hook; AgentLab builds only the decision layer, claim compiler, perturbation registry, and AWS fabric.
Daniel's framing: production is not hand-rolling everything; prefer well-maintained libraries and clean abstractions.
2. RATIFIED: runner ownership: AgentLab's own SQS/Step Functions/Fargate/DynamoDB fabric drives execution, Inspect runs in-task for scoring and logging, plain local Inspect for development iteration.
3. RATIFIED: Claude credit fallback: if the smoke test shows Claude-on-Bedrock is not credit-covered, run Nova-first and treat Claude as a paid confirmation tier for findings that survive.
4. RATIFIED: first experiment: compaction summarizer quality; second: reflection vs equal-token repeated sampling.
5. RATIFIED (direction): the repo is agent-first: agents are a primary audience for code and docs.
Adopt LangChain OpenWiki (`langchain-ai/openwiki`, npm CLI) to generate and maintain the repository wiki, discovered by agents through AGENTS.md, refreshed via its scheduled-update workflow that opens a PR when the wiki changes.
Caveat: OpenWiki's built-in providers are Anthropic, OpenAI, OpenRouter, Fireworks, and Baseten (no Bedrock listed), so wiki generation bills a small amount outside the AWS credits.
6. Default (not separately ratified, standing recommendation): region eu-west-1, ARM Fargate, public-subnet networking, Step Functions Standard, early Fargate quota increase request.
7. Recommendation pending: first harnesses Strands (cheap workhorse) then Claude Agent SDK.

### Post-review revisions (accepted 2026-08-16 after independent step-back review)

An independent cold review found five issues; Daniel accepted the direction change ("go").

1. v0.1 is redefined: a defensible statistical verdict produced locally (Track A) before any AWS build.
v0.2 reproduces that same verdict on the AWS fabric (Track B), which doubles as a genuine reproducibility test of the platform.
The AWS learning goal is unchanged; it is resequenced so it is no longer a single point of failure for the science goal.
2. Experiment one respecified: compaction quality measured by information retained across the compaction boundary (continuous, low-noise, real at 20 to 50 trials on cheap models).
The binary-success version needed ~600 trials and would cost more than the entire budget on Sonnet-class models with the tokenizer adjustment applied; it was unfundable as ratified.
3. No number in the master plan is trusted until two measurements land: the $5 Bedrock credit smoke test per model family, and a local variance pilot (~20 tasks x 5 repeats, cheap model, a few dollars) that replaces the assumed variance parameters with measured sigma, omega, and paired correlation.
4. Novelty is dropped as an organizing principle.
Scope is ranked by service to the two real goals: honest answers and AWS learning.
The claim-to-experiment compiler moves to the someday list.
5. v0.1 adopts exactly Inspect.
Scout, Harbor, Langfuse, and OpenWiki move to a trigger list with a written adoption condition each (OpenWiki's trigger: the repo contains real code).
Correction verified 2026-08-16 against docs.langchain.com: LangSmith now ships managed Sandboxes, and Harbor can run each trial on a LangSmith sandbox with results recorded as LangSmith experiments.
When the sandbox trigger fires (first code-executing workload, around v0.3), evaluate Harbor + LangSmith sandboxes head-to-head against Inspect's Docker sandboxes and our own Fargate before choosing.
The broker service leaves the v0.1 critical path; v0.2 uses a scoped Bedrock-only task role, and a broker returns only on measured need.

## 7. Production lessons encoded into the design

- Kavak (a16z's own page states 90 to 95% of interactions; the 96% figure comes from an approximate auto-generated transcript of the August 2026 session, so treat it as unverified): spend roughly as much on evals as on agents; treat evals as brakes that let you go faster.
- Sierra: production conversations become permanent regression tests; releases gate on re-running the suites; this is the promotion registry's template.
- Anthropic: eval trials start from clean isolated environments; infrastructure resourcing alone moved Terminal-Bench 2.0 by 6 points, so environment version pinning is a schema-level requirement.
- Intercom Fin: metric definitions are versioned artifacts; when a metric changes, ship old and new side by side.
- OpenAI retiring SWE-bench Verified (Feb 2026, contamination): static suites rot, which is the argument for the perturbation registry.

## 8. Open questions register

1. Does the $1,000 credit cover Claude-on-Bedrock Marketplace charges? (Smoke test resolves; decision 6.2 covers the contingency.)
2. Does Inspect ship or plan a first-party OTel hook? (If planned, contribute rather than fork; MLflow hook exists as precedent either way.)
3. Scout maturity: MIT-licensed and led by Inspect's own creator (verified 2026-08-16), but young (Sept 2025) with no stable PyPI release; pin a git SHA and keep the wrapper swappable.
4. Harbor's exact license/governance and whether its task format is stable enough to pin (not yet verified to the same depth as Inspect).
5. MCP spec revision 2026-07-28 is a large breaking change: pin to what harnesses actually speak; avoid sampling, roots, and elicitation features for now.
6. Which task suite seeds the first experiments: inspect_evals' existing SWE/terminal tasks vs a small hand-built ops environment (Phase 1 decision, after the compaction experiment's needs are concrete).
