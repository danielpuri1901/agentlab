# Existing systems that overlap AgentLab

Research date: 2026-08-16.
Scope: open-source and commercial systems overlapping AgentLab's planned layers, plus the trace-format standards question.
Every capability claim below carries a source URL.
Claims I could not verify from a primary source are labelled "unverified".

## 1. Summary

Six of AgentLab's eight layers are already well served by mature open source, and adopting them is the right call.
Inspect (UK AISI) is the closest thing to AgentLab's eval core and should be adopted outright, not reimplemented.
Harbor (Terminal-Bench's harness) is the closest thing to AgentLab's environment and task registry, with 12+ cloud sandbox providers pre-integrated.
Inspect Scout already solves the trajectory store, including importing traces from Phoenix, LangSmith, Logfire, MLflow, and W&B Weave.
The hypothesis that AgentLab's novel core is "promotion gates + environment perturbation + cost-per-quality" does not survive contact with the evidence.
All three of those exist today: Braintrust, Harness, and release-gate ship promotion gates; ReliabilityBench and Gaia2 ship environment perturbation; HAL ships cost-quality Pareto frontiers.
What is genuinely unoccupied is statistical decision discipline as a product primitive.
Every shipping gate I found blocks on a fixed threshold or a raw delta, while the measurement literature shows single-run deltas of 2-3 points are indistinguishable from noise.
The second unoccupied area is research-claim ingestion, where the only prior art measures agents reproducing papers rather than compiling claims into runnable experiments.
Recommendation: build a thin decision layer, adopt everything underneath it.

## 2. System-by-system findings

### 2.1 Inspect (UK AI Security Institute)

Repository: https://github.com/UKGovernmentBEIS/inspect_ai (MIT, 2,497 stars, 635 forks at time of access).
Docs: https://inspect.aisi.org.uk/

Inspect is a full evaluation framework, not a tracing tool, and it covers most of AgentLab's eval-engine surface already.

Task model: tasks bring together datasets, solvers, and scorers.
Source: https://inspect.aisi.org.uk/

Scorers: built-in text matching, multiple choice, model grading, math, and perplexity, plus custom scorers, multiple scorers per task, scoring metrics with clustered standard errors, and a deferred scoring workflow (`--no-score` then `inspect score` to re-grade existing logs).
Source: https://inspect.aisi.org.uk/

Sandboxing: Docker sandboxing is built in, with a `SandboxEnvironment` interface exposing `exec()`, `exec_remote()`, `write_file()`, and `read_file()`, per-sample file provisioning, and an extension API for additional sandbox types.
Source: https://inspect.aisi.org.uk/sandboxing.html.md

Eval sets: `inspect eval-set` and `eval_set()` provide automatic retry with configurable strategy, sample re-use across retries so work is not repeated, log cleanup after failed runs, and idempotent re-invocation that picks up where the last run stopped.
Eval sets can be amended with additional tasks, models, or epochs by re-issuing the same command.
Source: https://inspect.aisi.org.uk/eval-sets.html.md

Viz tooling: Inspect View log viewer, a VS Code extension, and a bundling option that emits a standalone static log viewer deployable to S3 or GitHub Pages.
There is also a companion package, Inspect Viz, for building visualisations from eval logs.
Sources: https://inspect.aisi.org.uk/eval-sets.html.md and https://inspect.aisi.org.uk/reference/inspect_ai.hooks.html

Operational features relevant to AgentLab: a control channel (`inspect ctl`) for launching detached evals and observing or directing them from another process, early stopping, dynamic task and sample sources, limits on time, messages, tokens, and cost, and context compaction for long-running agents.
Source: https://inspect.aisi.org.uk/eval-sets.html.md (sidebar index of the Running section)

Agent bridge: Inspect can run agents from OpenAI Agents SDK, LangChain, and Pydantic AI.
Source: https://inspect.aisi.org.uk/eval-sets.html.md (Agents section index)

Log format: Inspect writes `.eval` (binary) or `.json` logs with a typed `EvalLog` model, and provides `read_eval_log`, `write_eval_log`, `list_eval_logs`, incremental sample reading, header-only reads, and S3 conditional writes via ETag.
Source: https://github.com/UKGovernmentBEIS/inspect_ai/blob/b979937677100d7e122936c1f3af7fae94f2052d/src/inspect_ai/log/_file.py

Hooks API: a documented extension point with lifecycle callbacks including `on_eval_set_start`, `on_run_start`, `on_task_start`, `on_sample_init`, `on_sample_event`, `on_model_usage`, and `on_sample_scoring`.
Hook failures are caught and logged rather than failing the eval.
Source: https://github.com/UKGovernmentBEIS/inspect_ai/blob/23146669b9f71f0cf4a94bf65878f38d35085159/src/inspect_ai/hooks/_hooks.py

Inspect does not ship a first-party OpenTelemetry exporter, as far as the sources I reviewed show.
The shipped hook examples target MLflow, both tracking (`examples/hooks/mlflow_tracking.py`) and tracing (`examples/hooks/mlflow_tracing.py`), and a community `inspect-mlflow` PyPI package now bundles both.
Sources: https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/examples/hooks/mlflow_tracking.py and https://github.com/UKGovernmentBEIS/inspect_ai/issues/3547
The absence of an official OTel hook is unverified in the strict sense, since I did not exhaustively enumerate the repository.

Eval library: `inspect_evals` publishes 171 evals, including SWE-bench Verified, GAIA (with level splits), AgentBench, Cybench, SWE-Lancer, and CVE-Bench.
Sources: https://ukgovernmentbeis.github.io/inspect_evals/ and https://github.com/UKGovernmentBEIS/inspect_evals
Note that the inspect_ai README claims "over 200 pre-built evaluations" while the inspect_evals site counts 171; treat the exact number as unverified and cite the site count.

### 2.2 Inspect Scout

Docs: https://meridianlabs-ai.github.io/inspect_scout/

Scout is a transcript analysis tool that directly occupies AgentLab's trajectory-store and trajectory-evaluation layers.

Core model: a Transcript is an LLM conversation such as an agent rollout or an Inspect sample, a Scanner takes input from a Transcript and returns a Result, and the docs describe Scanner as "conceptually very similar to an Inspect Scorer".
It ships `llm_scanner()` (boolean, number, string, single or multi classification, structured JSON answers) and `grep_scanner()` for pattern matching, plus custom scanners.
Source: https://meridianlabs-ai.github.io/inspect_scout/

Stated capabilities include validating scanner accuracy against human-labelled examples, handling multi-agent transcripts, compaction, and context-window chunking, and scaling to thousands of transcripts with parallel processing, batching, and fault tolerance.
Source: https://meridianlabs-ai.github.io/inspect_scout/

Import sources: Inspect logs, Arize Phoenix, LangSmith, Logfire, MLflow, W&B Weave, local Claude Code sessions, a Python Transcript API, Arrow `RecordBatchReader` insertion, and an existing Parquet data lake.
The transcript database can live on S3 (`transcripts_db("s3://my-transcript-db/")`) and supports filtered inserts with a column expression API.
Source: https://meridianlabs-ai.github.io/inspect_scout/db_importing.html

This matters a lot for AgentLab: Scout already provides the vendor-neutral trajectory store plus the trajectory-evaluation engine, and it reads from the observability platforms AgentLab would otherwise have to integrate one by one.

### 2.3 Langfuse

Langfuse covers tracing, datasets, experiments, evaluators, and annotation queues.

Evaluation model: datasets of items (input plus optional expected output), a task function, evaluation methods, scores, and experiment runs.
Scores are the universal object for any quality judgement, whether from human annotation, an LLM judge, a programmatic check, or end-user feedback.
Source: https://langfuse.com/docs/evaluation/core-concepts

Experiments: SDK runner with concurrent execution, automatic tracing, item-level and run-level evaluators, error isolation so individual failures do not stop the run, and dataset integration.
There is also a UI path and a remote-trigger webhook path where Langfuse posts to your endpoint and you ingest scores back as an experiment run.
Source: https://langfuse.com/docs/evaluation/experiments/experiments-via-sdk

Dataset versioning: every add, update, delete, or archive of dataset items produces a new version tracked by timestamp, and experiments can run against a specific version timestamp for reproducibility.
Datasets also support JSON Schema enforcement on `input` and `expectedOutput`.
Source: https://langfuse.com/docs/evaluation/experiments/datasets

Annotation queues: structured human review with score configs, keyboard shortcuts, corrected outputs, and a public API for automating annotation workflows.
Source: https://langfuse.com/docs/evaluation/evaluation-methods/annotation-queues

Self-hosting cost and effort, which was specifically asked about:
The deployment is two application containers (web and worker) plus four storage components: Postgres for transactional data, ClickHouse for traces, observations, and scores, Redis or Valkey for cache and queue, and S3 or blob storage for raw events and multimodal attachments.
Docker Compose on a VM is documented for testing and low-scale only, and explicitly "lacks high-availability, scaling capabilities, and backup functionality".
Production paths are Kubernetes/Helm or Terraform on AWS, Azure, GCP, or Railway.
All infrastructure components must run with timezone set to UTC or queries return incorrect or empty results.
Source: https://langfuse.com/self-hosting

Licensing: the docs state "All core Langfuse features and APIs are available in Langfuse OSS (MIT licensed) without any limits" and that there are no scalability limits between versions.
The Enterprise licence key gates project-level RBAC roles, protected prompt labels, data retention policies, audit logs, server-side data masking, UI customisation, organisation creators, org management API and SCIM, and the instance management API.
Annotation queues are not on that Enterprise list.
Source: https://langfuse.com/self-hosting/license-key
Historical note: a June 2025 issue reported annotation queues disabled in OSS self-hosted, and the maintainers responded "You're right, we will fix this" with a fix shipped in v3.65.3.
Source: https://github.com/langfuse/langfuse/issues/7128
Honest read: self-hosting Langfuse is a real four-stateful-service operational commitment, but the feature set is not crippled in OSS.

### 2.4 LangSmith

LangSmith supports offline evaluation on curated datasets and online evaluation scoring production traffic, with human evaluation via annotation queues, heuristic checks, LLM-as-judge, pairwise comparisons, and custom Python or TypeScript evaluators.
It captures full agent trajectories including steps, tool calls, and reasoning, and supports evaluators that score trajectory behaviour.
It integrates with pytest, Vitest, and GitHub workflows, with thresholds on evaluation metrics that fail pipelines automatically.
Source: https://www.langchain.com/langsmith/evaluation

Third-party assessment, which I flag as opinion rather than vendor fact: LangSmith's tracing is the strongest of the commercial options for LangChain and LangGraph users, while the evaluator layer is described as manual to build.
Source: https://blog.codercops.com/blog/llm-evaluation-langsmith-braintrust-2026

### 2.5 Braintrust

Braintrust's product page states the eval lifecycle explicitly as "Build datasets, define scorers, run experiments, and gate deployments", with "CI/CD quality gates block bad changes".
Source: https://www.braintrust.dev/product/evaluate

Experiments are immutable snapshots that are permanently stored and comparable over time, in contrast to playground runs which overwrite previous results.
Source: https://www.braintrust.dev/docs/evaluate/run-evaluations

The documented cycle is: iterate in playgrounds, promote to an experiment, automate in CI/CD on every pull request, score in production with online scoring rules, and feed production traces back into datasets.
Source: https://www.braintrust.dev/docs/evaluate

Agent-specific claims: SDK wrappers for OpenAI Agents SDK, LangGraph, Mastra, Pydantic AI, LangChain, CrewAI, and Vercel AI SDK; a `BraintrustSpanProcessor` that converts OTEL spans into structured agent traces; nested typed spans for tool calls, reasoning steps, state transitions, and memory operations; and a GitHub Action that posts results to the PR and blocks merges when a change degrades quality.
Source: https://www.braintrust.dev/articles/agent-observability-complete-guide-2026 (vendor marketing content, so treat competitive comparisons in it as unverified)

This is the single most direct commercial overlap with AgentLab's promotion-gate concept.

### 2.6 Arize Phoenix and OpenInference

Phoenix is open source (ELv2 per the vendor page), self-hostable via Docker, Kubernetes/Helm, or cloud templates, and built on OpenTelemetry.
It provides tracing over OTLP, LLM-based and code-based evaluation, human annotations, prompt management and versioning, and datasets and experiments for repeatable offline comparison.
Sources: https://github.com/arize-ai/phoenix and https://arize.com/docs/phoenix

Phoenix ships surfaces specifically for coding agents: a `/mcp` endpoint for MCP clients such as Claude Code and Cursor, a `@arizeai/phoenix-cli` for fetching traces, datasets, and experiments, and packaged agent skills (`phoenix-cli`, `phoenix-evals`, `phoenix-tracing`).
It also runs code evaluators in hosted sandbox providers for kernel-level isolation.
Source: https://github.com/arize-ai/phoenix

OpenInference is a set of conventions and plugins complementary to OpenTelemetry, natively supported by Phoenix and Arize AX but usable with any OTel-compatible backend.
The specification is transport and file-format agnostic.
Source: https://github.com/arize-ai/openinference

OpenInference span kinds are `AGENT`, `LLM`, `TOOL`, `RETRIEVER`, `RERANKER`, `CHAIN`, `GUARDRAIL`, `EVALUATOR`, `EMBEDDING`, and `PROMPT`, and an OpenInference trace remains a valid OTLP trace.
The same source states that in 2026 Arize proposed donating its OpenInference instrumentation libraries to the OpenTelemetry project.
Source: https://arize.com/resources/ai-agent-tracing-evaluation/ (vendor-authored; the donation proposal in particular I would label unverified until confirmed on an OTel repository)

### 2.7 W&B Weave

Weave's core object is an `Evaluation` defining a `Dataset` or list of dicts, one or more scoring functions, and optional input preprocessing, run against a `Model` or any function, producing an evaluation run.
It supports repeated trials directly (`trials=3` in Python, `nTrials` in TypeScript), dataset versioning where each modification creates a new version and evaluations record which version they ran against, and `preprocess_model_input` or `columnMapping` for schema adaptation.
Source: https://docs.wandb.ai/weave/guides/core-types/evaluations

Weave's differentiator is integration with W&B training-side experiment tracking, which matters if AgentLab ever fine-tunes.
Source: https://qaskills.sh/blog/weights-biases-llm-evals-guide (third-party guide, so treat as secondary)

Notably, HAL's harness uses Weave for cost tracking and trace logging, which is a real production endorsement rather than marketing.
Source: https://github.com/princeton-pli/hal-harness

### 2.8 promptfoo

promptfoo has moved considerably further into agentic territory than its prompt-testing origins suggest, and its limits are worth stating precisely.

It ingests OpenTelemetry spans over an OTLP receiver and normalises them into an "agent trajectory", a time-ordered summary of the run.
Trajectory assertions include `trajectory:tool-used`, `trajectory:tool-args-match`, `trajectory:tool-sequence`, `trajectory:step-count`, and `trajectory:goal-success`.
Source: https://www.promptfoo.dev/docs/red-team/agents/

It has a first-class OpenAI Agents SDK provider supporting tools, handoffs, persistent sessions, sandbox runtime, guardrails, `maxTurns`, and mocked tool execution.
Sources: https://www.promptfoo.dev/docs/providers/openai-agents/ and https://www.promptfoo.dev/docs/guides/evaluate-openai-agents-python/

Its coding-agent red-team guidance is unusually rigorous about evidence quality: it distinguishes model behaviour, harness boundary, provider instrumentation, and eval-design failures; it recommends per-row isolated repository copies to prevent cross-row canary contamination; and it recommends SHA-256 hashing of protected files plus sidecar verifiers running outside the agent's writable workspace.
Source: https://www.promptfoo.dev/docs/red-team/coding-agents/

Where promptfoo stops for AgentLab's purposes: it is a test-runner and red-team tool, not an experiment engine.
It has no repeated-trial statistics, no variance decomposition, no cost-quality frontier, and no promotion registry.
Its environment story depends on your provider supplying the sandbox rather than the framework provisioning one.

### 2.9 OpenAI Evals

The open-source `openai/evals` repository is effectively in maintenance: its README redirects users to configuring evals in the OpenAI Dashboard, and it states "we are currently not accepting evals with custom code".
Source: https://github.com/openai/evals

More importantly, the hosted Evals platform is being retired.
The API guide states OpenAI is deprecating the Evals platform, that it becomes read-only for existing users on 2026-10-31, and that it is scheduled to shut down on 2026-11-30.
Source: https://developers.openai.com/api/docs/guides/evals
Graders are being deprecated alongside the evals and fine-tuning workflows they support.
Source: https://developers.openai.com/api/docs/guides/graders

Verdict: do not build on OpenAI Evals.
This is a dead end within three months of today's date.

### 2.10 Agent benchmark harnesses

**SWE-bench.**
Fully containerised Docker evaluation harness since June 2024, built in three image layers (base, ~60 environment images, per-instance images).
Entry point is `swebench.harness.run_evaluation`, with a modern CLI (`swebench eval verified --gold`, `swebench report <run_id> -d verified`, `swebench images build verified -j 8`).
Datasets: full, lite, verified (500 engineer-confirmed solvable problems), multimodal (private test split, cloud-evaluated via `sb-cli`), and multilingual.
Cloud execution on Modal is supported (`--modal`).
Resource requirements are substantial: 120GB free storage, 16GB RAM, 8 CPU cores on x86_64, and `instance` cache level needs roughly 2,000GB.
Critical reproducibility gotcha: results are cached by `run_id` and `instance_id` only, so re-running the same instance with the same `run_id` but a different prediction reuses the stale result.
Sources: https://github.com/swe-bench/SWE-bench, https://www.swebench.com/SWE-bench/guides/docker_setup/, https://www.swebench.com/SWE-bench/reference/harness/

**tau-bench and tau2-bench.**
A simulation framework for customer service agents, where each domain has a policy the agent must follow, a tool set, tasks, and optionally user tools for the simulator.
Domains: mock, airline, retail, telecom, banking_knowledge.
tau2 adds dual control, where the environment is co-owned by agent and simulated user, with solo and interactive modes and a compositional task generator.
Source: https://github.com/sierra-research/tau2-bench and https://sierra.ai/uk/blog/benchmarking-agents-in-collaborative-real-world-scenarios

Reusable machinery for AgentLab: a three-layer runner API (`runner.simulation`, `runner.build`, `runner.batch`) with concurrency, checkpointing, retries, and side effects; explicit `--num-trials`; a `seed` parameter on the Orchestrator; documented ablation modes (no-user solo mode, ground-truth agent, workflow variants); and a Gymnasium-compatible RL interface.
Sources: https://github.com/sierra-research/tau2-bench/blob/main/docs/running_simulations.md and https://github.com/sierra-research/tau2-bench/blob/main/docs/cli-reference.md

**TheAgentCompany.**
175 tasks in a fully self-hosted simulated company: sandboxed Docker local workspace plus a self-hosted intranet (GitLab, OwnCloud, Plane, Rocket.Chat), with simulated colleagues as NPCs.
Tasks decompose into checkpoints with point values, evaluated by deterministic Python evaluators that inspect environment state or agent trajectories, with LLM-based evaluation for unstructured deliverables.
Every task ships as a Docker image with a fixed layout (`/utils/init.sh`, `/utils/eval.py`, `/instruction/task.md`, `/workspace`).
Sources: https://github.com/TheAgentCompany/TheAgentCompany and https://arxiv.org/html/2412.14161
This is the best available model for partial-credit checkpoint scoring, which AgentLab will need for long-horizon tasks.

**OSWorld.**
369 real computer tasks (361 if you exclude 8 Google Drive tasks) across Ubuntu, Windows, and macOS, each with an initial state setup config and a custom execution-based evaluation script, backed by 134 unique execution-based evaluation functions.
It uses a getter-function plus evaluator-function pattern, supports parallel environments on one host and headless operation, and supports vmware, virtualbox, docker, and aws providers.
Sources: https://os-world.github.io/ and https://arxiv.org/html/2404.07972
Caution: an independent audit found 13 of 46 Chrome tasks broken due to website layout, URL, and functionality drift, underestimating a state-of-the-art agent by 28 absolute points.
Source: https://arxiv.org/html/2507.02825

**GAIA and Gaia2 / ARE.**
GAIA is available on Hugging Face with a leaderboard.
Source: https://huggingface.co/gaia-benchmark
Gaia2 is a read-and-write successor with 800 scenarios across 10 universes, run on Meta's Agents Research Environments (ARE), MIT licensed, with the dataset CC-BY-4.0.
Sources: https://github.com/huggingface/blog/blob/main/gaia2.md and https://github.com/facebookresearch/meta-agents-research-environments

ARE is directly relevant as an environment simulator: apps expose APIs, events make the environment evolve over time, and scenarios combine apps, events, and validation logic.
Its judge system is hierarchical, with event-level, tool-level, temporal, and causal validation, a `GraphPerEventJudge` with hard and soft (LLM) tool judges, and a scripted variant that disables LLM soft validation entirely for "predictable, reproducible validation results".
The judge runs on its own LLM engine, independent of the agent's model.
Sources: https://facebookresearch.github.io/meta-agents-research-environments/api_reference/validation.html and https://facebookresearch.github.io/meta-agents-research-environments/user_guide/benchmarking.html

Critically for the novelty question: `gaia2-run` executes three phases, and the third is a "Noise Phase: Robustness evaluation with environment perturbations and tool augmentation".
It also automatically sets `--num_runs 3` to meet leaderboard variance-analysis requirements, and includes a caching system keyed on scenario content plus runner configuration.
Source: https://facebookresearch.github.io/meta-agents-research-environments/user_guide/benchmarking.html

**terminal-bench and Harbor.**
Harbor is described by its authors as "a framework for evaluating and optimizing agents and models in container environments", built after Terminal-Bench was used for custom evals, prompt optimization, RL, SFT trace generation, and CI/CD agent testing.
Source: https://www.harborframework.com/docs

Harbor provides: modular interfaces for environments, agents, and tasks; pre-integrated CLI agents (Terminus-2, Claude Code, Copilot CLI, Codex CLI, Gemini CLI, Grok Build, OpenHands, Antigravity SDK, Mini-SWE-Agent, fx); a registry of benchmarks and datasets (Harbor Hub); integrations with Daytona, Modal, E2B, Runloop, Tensorlake, LangSmith, Blaxel, Novita Sandbox, EC2, Beam, and Hyperbrowser; and integrations with SkyRL and GEPA for optimization.
Sources: https://www.harborframework.com/docs and https://www.harborframework.com/docs/agents

The Harbor task format is the most complete containerised task spec I found.
`task.toml` declares verifier and agent timeouts and OS users, environment resource requirements (cpus, memory_mb, storage_mb, gpus, gpu_types, TPU type and topology), Docker image or Dockerfile, MCP servers, and healthchecks.
Network policy is layered: baselines set at env start and restored between phases, phase overrides applied only during `agent.run()` or `verifier.verify()`, and run-time merges via `--allow-environment-host` and `--allow-agent-host`, with modes `public`, `no-network`, and `allowlist`.
Multi-step tasks are supported, each step with its own instruction, tests, and pre-agent setup.
Source: https://www.harborframework.com/docs/tasks

Terminal-Bench itself is now a continuous benchmark with tagged releases on Harbor Hub, spanning versions 1.0, 2.0, 2.1, and 3, with an oracle-solution smoke test pattern (`--agent oracle -k 5`) for validating a sandbox setup before trusting results.
Sources: https://github.com/harbor-framework/terminal-bench and https://www.tbench.ai/

**HAL (Holistic Agent Leaderboard).**
A standardised, cost-aware, third-party evaluation harness orchestrating parallel evaluations across hundreds of VMs, validated with 21,730 agent rollouts across 9 models and 9 benchmarks at roughly $40,000 in compute.
It integrates Weave for cost tracking and trace logging, is framework agnostic, and requires no changes to agent code.
Sources: https://hal.cs.princeton.edu/about and https://github.com/princeton-pli/hal-harness

HAL's substantive finding is the one that most directly bears on AgentLab's cost hypothesis: it computes accuracy-versus-cost Pareto frontiers automatically for every benchmark, and found that the most expensive model was on the frontier in only 1 of 9 benchmarks, while Gemini 2.0 Flash was on the frontier in 7 of 9.
It also found that higher reasoning effort reduced accuracy in the majority of runs.
Source: https://proceedings.iclr.cc/paper_files/paper/2026/file/a0928f924a344aaebbb7f6cd8d56e34c-Paper-Conference.pdf

### 2.11 Systems that attempt AgentLab's promotion-gate loop

I searched specifically for this and found three, which is the finding that most challenges the project's premise.

**Harness AI Evals** ships what it calls "the first native quality gate for AI in CI/CD": a native pipeline step alongside Build, Test, and Deploy, 50+ evaluation metrics including faithfulness, safety, hallucination, task completion, and tool correctness, eval suites with blocking or advisory pass strategies, and offline plus online evaluation sharing the same metrics and datasets.
It inherits RBAC on every eval, dataset, and suite, OPA policy governance on eval configurations, full audit trails on threshold changes, and a centralised versioned registry for prompts, agents, MCP tools, and skills.
Source: https://www.harness.io/blog/introducing-ai-evals

**release-gate** is an open-source CLI that returns a single PROMOTE / HOLD / BLOCK verdict with CI exit codes 0 / 10 / 1.
It scores six weighted dimensions (safety 30%, cost 20%, access_control 20%, fallback 15%, eval_quality 10%, observability 5%) into a 0-100 score with thresholds PROMOTE >= 90, HOLD 75-89, BLOCK < 75.
It includes a regression gate (`release-gate compare baseline.json candidate.json`) that blocks when safety, fallback, or access_control drop more than 10 points, a trace validator that catches forbidden tool calls, retry storms, token budget overruns, and tool-call loops, and an evidence pack generator producing JSON, Markdown, and HTML audit artefacts.
Sources: https://github.com/VamsiSudhakaran1/release-gate and https://release-gate.com/

**Braintrust** gates deployments via a GitHub Action, as described in 2.5.

There is also a well-developed practitioner pattern literature describing exactly AgentLab's loop: a two-tier gate with cheap deterministic offline evals on every commit and calibrated live LLM-judge evals gating deploys of prompt, model, or retrieval changes, plus a frozen regression baseline that is never re-labelled to turn a red run green.
Source: https://aiarch.dev/patterns/eval-harness-gate
A second write-up specifies five gates (golden dataset offline eval, regression blocks, cost gate, shadow evaluation against replayed production traces, canary rollout with auto-rollback) with concrete thresholds and an explicit warning against auto-advancing the baseline on every green run because it causes "slow-cooking regressions".
Source: https://baeseokjae.github.io/posts/agent-ci-cd-eval-pipeline-integration-guide-2026/

**DSPy and GEPA** occupy the adjacent optimization loop rather than the promotion loop.
GEPA is a reflective prompt optimizer maintaining a candidate population, scoring on a validation set, sampling from a per-example Pareto frontier, and mutating via LLM reflection on low-scoring traces, with `track_stats=True` recording every candidate, every parent in the lineage, and per-example scores.
Sources: https://dspy.ai/diving-deeper/gepa-in-depth/ and https://arxiv.org/abs/2507.19457
GEPA's `optimize_anything` API and Harbor's GEPA integration mean an optimization loop is available off the shelf if AgentLab wants one.
Sources: https://gepa-ai.github.io/gepa/ and https://www.harborframework.com/docs

### 2.12 Prior art on environment perturbation and stochastic trials

This is the second place the novelty hypothesis breaks down.

**ReliabilityBench** evaluates a three-dimensional "Reliability Surface" R(k, epsilon, lambda): consistency under repeated execution (k-trial pass rates), robustness to task perturbations (epsilon levels), and fault tolerance under injected infrastructure failures (lambda levels).
Perturbation levels are discrete and defined (epsilon = 0 baseline, 0.1 synonym substitution, 0.2 adds reordering and distractors, 0.3 adds paraphrase and corrections), correctness is defined by end-state equivalence rather than text similarity ("Action Metamorphic Relations"), and the chaos-engineering framework injects transient timeouts, rate limits, partial responses, and schema drift.
Reported result: agents at 96.9% pass@1 at epsilon=0 drop to 88.1% at epsilon=0.2.
Source: https://arxiv.org/abs/2601.06112v1

**Randomness in agentic evals** measured 60,000 SWE-bench-Verified trajectories across three models and two scaffolds.
Single-run pass@1 estimates vary by 2.2 to 6.0 percentage points depending on which run is selected, with standard deviations above 1.5 points even at temperature 0.
The paper's recommendations are estimating pass@1 from multiple independent runs, using statistical power analysis to determine the number of runs needed for a target effect size, and reporting pass@k and pass^k to characterise the performance envelope.
Source: https://arxiv.org/html/2602.07150v2

**ClawBench** goes further and decomposes benchmark-wide variance into seed noise versus capability signal, reporting that only 52.7% of run-score variance is real capability signal, that 21 of 40 tasks had SNR below 1 (seed noise at or above capability signal), and curating the task set by greedy SNR-preserving elimination.
It also reports pass^k, Taguchi signal-to-noise, and bootstrap confidence intervals from 10,000 resamples.
Source: https://github.com/openclaw/clawbench
Treat ClawBench's specific numbers as a single-project result rather than an established finding.

**stochastic-agent-evals** implements Intraclass Correlation Coefficient analysis to decompose variance into between-query (task difficulty) and within-query (agent inconsistency) components, with ICC sensitivity analysis over trial and question counts.
Source: https://github.com/youdotcom-oss/stochastic-agent-evals

**Agentic Benchmark Checklist (ABC)** is the best available guide for building a defensible task registry, and it documents concrete validity failures: tau-bench has 38% unsolvable airline tasks where a do-nothing agent passes, WebArena substring matching accepts extraneous content, SWE-Lancer lets agents overwrite test files inside a password-protected archive, and KernelBench's fuzzer varies only tensor values so correctness was overestimated by 31%.
Its environment requirements are directly usable as AgentLab requirements: full cleanup of legacy state before each task, complete isolation of agents from ground truth, a frozen and reproducible environment, and an automatic oracle solver to demonstrate task-configuration correctness.
Source: https://arxiv.org/html/2507.02825

### 2.13 Prior art on research-claim ingestion

**PaperBench** evaluates agents replicating 20 ICML 2024 Spotlight and Oral papers from scratch, with rubrics hierarchically decomposing each replication into 8,316 individually gradable tasks, co-developed with the paper authors, plus an LLM judge assessed against a separate judge benchmark.
It runs in three staged containers: agent rollout, reproduction with GPU access, and grading.
Sources: https://openai.com/index/paperbench/ and https://github.com/openai/frontier-evals/tree/main/project/paperbench

**CORE-Bench** covers computational reproducibility with 270 tasks from 90 papers across computer science, social science, and medicine, in three difficulty levels, with an isolated-VM harness reducing evaluation from over 20 days to hours.
Sources: https://arxiv.org/html/2409.11363 and https://github.com/siegelz/core-bench

Important distinction: both measure an agent's ability to reproduce research.
Neither ingests a research claim and compiles it into a runnable, pre-registered experiment against your own agent, which is what AgentLab's research-claim ingestion layer proposes.
I found no system doing the latter.

## 3. Build-vs-adopt table

| Layer | Verdict | Reasoning |
|---|---|---|
| Tracing | Adopt OTel span tree, wrap the attributes | The `invoke_agent` / `create_agent` / `chat` / `execute_tool` span tree is stable and vendor-neutral, while `gen_ai.*` attributes are still Development status and change nearly every release. Own an internal model and map outward. See section 4. |
| Trajectory store | Adopt Inspect Scout, wrap for AgentLab metadata | Scout already gives a Parquet/S3 transcript database with an Arrow insert path, a filter DSL, and importers for Phoenix, LangSmith, Logfire, MLflow, Weave, Inspect logs, and Claude Code sessions. Building this is months of work for a strictly worse result. Wrap it only to attach AgentLab's experiment, arm, seed, and perturbation identifiers. |
| Eval engine | Adopt Inspect | Solvers, scorers, model grading, multi-scorer, deferred re-scoring, clustered standard errors, sandboxing, eval sets with retry and resume, limits, log viewer, and 171 ready evals. Add trajectory scanners via Scout rather than writing a second scorer system. The one thing to build on top is the statistics layer. |
| Experiment engine | Build, thin | This is the real gap. Inspect has epochs, Harbor has `-k`, Weave has `trials`, tau2 has `--num-trials`, ARE has `--num_runs 3`. None of them sizes the run from a target effect size, decomposes seed noise from capability signal, or refuses to report a result whose delta sits inside the noise band. Build this as a small statistical layer over Inspect eval sets, not as a new runner. |
| Environment | Adopt Harbor for containerised work, Inspect sandboxes for eval-native work | Harbor's `task.toml` already covers resource declaration, layered network policy with phase overrides, MCP servers, healthchecks, multi-step tasks, and 12+ cloud sandbox providers. Reimplementing that is pure cost. Use Inspect's Docker sandbox where the task is a normal Inspect eval, and Harbor where you need scale-out across Daytona or Modal. |
| Task registry | Adopt Harbor Hub plus inspect_evals, wrap with own metadata | 171 inspect_evals tasks and the Harbor Hub dataset registry give immediate coverage of SWE-bench, GAIA, Cybench, AgentBench, and terminal-bench. AgentLab's own registry should store what those do not: perturbation families, oracle solvers, per-task SNR, validity audit status, and cost baselines. |
| Evaluation: deterministic | Adopt Inspect scorers | Nothing to gain from a rewrite. |
| Evaluation: trajectory | Adopt Inspect Scout scanners | Scout's scanner model, human-label validation, and compaction and chunking handling are ahead of what a fresh implementation would reach. |
| Evaluation: LLM-as-judge | Adopt, but build calibration | Every platform ships judges. Almost none ship judge calibration against human labels as a gating requirement. Scout supports validating scanner accuracy against human-labelled examples, so build the calibration policy on top of that rather than the judge itself. |
| Evaluation: human | Adopt Langfuse annotation queues | Score configs, corrected outputs, keyboard shortcuts, and a public API, all in the MIT-licensed core. Accept the four-stateful-service self-hosting cost, or use Langfuse Cloud, rather than building a labelling UI. |
| Research-claim ingestion | Build | No prior art found. PaperBench and CORE-Bench measure agents reproducing papers; neither compiles a claim into a pre-registered experiment. This is the least occupied layer. |
| Promotion registry and gates | Build the decision layer, adopt the plumbing | Braintrust, Harness, and release-gate already ship PROMOTE/HOLD/BLOCK on thresholds. Do not rebuild artefact storage, CI wiring, or evidence packs. Build only the part none of them has: a promotion decision that is a statistical claim rather than a threshold comparison. |

## 4. Trace standard recommendation

Recommendation: one canonical internal schema, OTel as the interchange layer, and Scout as the ingestion funnel.

**Canonical internal record: the Inspect EvalLog / Scout transcript schema.**
This is the only format in the field that carries what AgentLab actually needs to make a promotion decision: task identity and version, model and generate config, per-sample epochs, scores with explanations, sandbox state, token usage, timing, and errors, all in a typed Pydantic model with S3 conditional writes.
Source: https://github.com/UKGovernmentBEIS/inspect_ai/blob/b979937677100d7e122936c1f3af7fae94f2052d/src/inspect_ai/log/_file.py
OTel spans do not carry scores natively, and the GenAI evaluation event that would carry them is itself Development status.

**Interchange layer: OpenTelemetry GenAI conventions, instrumented against the span tree, not the attribute names.**
The GenAI conventions moved to a dedicated repository, `open-telemetry/semantic-conventions-genai`, and the main semantic-conventions repository deprecated and moved all `gen_ai.*` content in v1.42.0 (June 2026).
Source: https://github.com/open-telemetry/semantic-conventions-genai and https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/

As of July 2026, no GenAI-specific span, event, metric, or attribute in that repository is marked Stable; the conventions remain Development, and the dedicated repository has no releases or tags and its schema-URL section is still a TODO.
Source: https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/
The last versioned cut is main-repo v1.42.0.

What is stable enough to build on is the span tree: a top-level `invoke_agent`, a `create_agent` lifecycle span, a `chat` span per model call, and an `execute_tool` span per tool invocation including MCP tools.
The one required GenAI metric is `gen_ai.client.operation.duration`, with `gen_ai.client.token.usage` recommended and present in practice.
Source: https://dreaming.press/posts/opentelemetry-genai-agent-observability.html

Concrete rules for AgentLab:

1. Set `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental` process-wide and pin it like a dependency.
Instrumentations default to frozen v1.36-era behaviour, so a process pulling in two instrumented SDKs written against different convention versions can emit two attribute shapes for the same concept and silently match half your queries.
Source: https://dreaming.press/posts/opentelemetry-genai-agent-observability.html

2. Coalesce attribute generations rather than summing them.
Query both `gen_ai.system` and `gen_ai.provider.name`, and both `gen_ai.usage.prompt_tokens`/`input_tokens` and `completion_tokens`/`output_tokens`, because frameworks that duplicate for compatibility emit the same value under both names.
Source: https://john-hodge.com/blog/opentelemetry-genai-semantic-conventions/

3. Centralise every `gen_ai.*` attribute key in one constants file, and never hard-code an attribute key into a gate condition until the conventions graduate.
Gate conditions should read from AgentLab's internal model, which is versioned by AgentLab.

4. Emit OTel from an Inspect Hook.
The Hooks API is the documented extension point and already has a working precedent in the MLflow tracing hook, which maps Inspect `ModelEvent`, `ToolEvent`, `ScoreEvent`, and `SpanBeginEvent`/`SpanEndEvent` onto a span hierarchy.
Source: https://github.com/UKGovernmentBEIS/inspect_ai/blob/main/examples/hooks/mlflow_tracing.py
An OTel hook is the same shape of work and makes Langfuse, Phoenix, Braintrust, and any OTLP backend pluggable at once.

5. Use OpenInference span kinds as the enrichment vocabulary where OTel GenAI has no equivalent, specifically `RETRIEVER`, `RERANKER`, `GUARDRAIL`, and `EVALUATOR`.
OpenInference traces remain valid OTLP traces, so this costs nothing in portability.
Source: https://github.com/arize-ai/openinference

Vendor pluggability, confirmed in both directions:
Langfuse ingests OTLP over HTTP at `/api/public/otel` with JSON or protobuf, requires the `x-langfuse-ingestion-version: 4` header for real-time v4 ingestion, and does not yet support gRPC.
Source: https://langfuse.com/integrations/native/opentelemetry
Phoenix accepts traces over OTLP.
Source: https://arize.com/docs/phoenix
Braintrust converts OTEL spans via `BraintrustSpanProcessor`.
Source: https://www.braintrust.dev/articles/agent-observability-complete-guide-2026
promptfoo runs an OTLP receiver and normalises spans into trajectories.
Source: https://www.promptfoo.dev/docs/red-team/agents/
And Scout imports back out of Phoenix, LangSmith, Logfire, MLflow, and Weave.
Source: https://meridianlabs-ai.github.io/inspect_scout/db_importing.html

That combination means AgentLab can emit once and let the team use whichever UI they prefer, while keeping the promotion decision computed from AgentLab's own record.

## 5. What AgentLab uniquely adds

I was asked to challenge the hypothesis that the novel core is the promotion-gate loop, reproducible environment perturbation, and cost-per-quality accounting.
The evidence disagrees with all three, and I think it is worth saying that plainly before saying what does survive.

**Promotion gates are not novel.**
Braintrust's product page literally lists "Gate deployments" as a step in its eval lifecycle.
Harness ships eval suites as a blocking pipeline step with RBAC, OPA policy governance, and audit trails on threshold changes.
release-gate emits PROMOTE / HOLD / BLOCK with CI exit codes and a regression gate that blocks on dimension drops greater than 10 points.
Sources: https://www.braintrust.dev/product/evaluate, https://www.harness.io/blog/introducing-ai-evals, https://github.com/VamsiSudhakaran1/release-gate

**Reproducible environment perturbation is not novel.**
ReliabilityBench formalises a perturbation axis with defined levels and end-state-equivalence correctness, plus systematic fault injection.
Gaia2's standard leaderboard pipeline includes a Noise Phase with environment perturbations and tool augmentation, and forces 3 runs per scenario.
Sources: https://arxiv.org/abs/2601.06112v1 and https://facebookresearch.github.io/meta-agents-research-environments/user_guide/benchmarking.html

**Cost-per-quality accounting is not novel.**
HAL computes accuracy-versus-cost Pareto frontiers automatically for every benchmark and published the finding that the most expensive model reaches the frontier in only 1 of 9 benchmarks.
The CLEAR framework's cost-normalized accuracy covers the same ground.
Sources: https://proceedings.iclr.cc/paper_files/paper/2026/file/a0928f924a344aaebbb7f6cd8d56e34c-Paper-Conference.pdf and https://dreaming.press/posts/cost-aware-agent-evaluation.html

What does survive, and what I would build the project's identity on:

**(a) The promotion decision as a statistical claim rather than a threshold comparison.**
This is the strongest defensible gap and it is narrow enough to actually ship.
Every gate I found blocks on a fixed threshold or a raw delta: release-gate blocks on a 10-point dimension drop, the CI pattern literature suggests blocking on a 5-point completion-rate drop or a 15% cost increase.
Meanwhile the measurement work shows single-run pass@1 varies by 2.2 to 6.0 points on SWE-bench-Verified with standard deviations above 1.5 points at temperature 0, and that roughly 47% of one benchmark's run-score variance was seed noise rather than capability signal.
Sources: https://arxiv.org/html/2602.07150v2 and https://github.com/openclaw/clawbench
So today's gates are, in the common case, blocking or promoting on noise, and they cannot tell you which.
A gate that sizes its own run count from a target effect size via power analysis, reports a confidence interval on the delta, and returns INCONCLUSIVE rather than PROMOTE when the interval straddles zero, is a thing no shipping product does.
It is also cheap to build, because Inspect eval sets already give resumable repeated runs and Inspect scorers already compute clustered standard errors.

**(b) One decision object spanning quality, robustness, cost, and confidence.**
The four axes exist in four different places today: quality in Braintrust and Langfuse, robustness in ReliabilityBench and Gaia2, cost in HAL, confidence in academic papers.
Joining them so that a single promotion record answers "better, more robust, cheaper, and are we sure" is genuine integration value, though it is engineering value rather than research novelty, and it should be claimed as such.

**(c) Research-claim ingestion.**
This is the least occupied layer and I could not find prior art.
PaperBench and CORE-Bench measure agents reproducing papers; nothing compiles a published claim into a pre-registered experiment with a stated hypothesis, a sized run count, and a falsification criterion, run against your own agent.
If AgentLab wants a defensible research contribution rather than a defensible product, this is where it is.

**(d) A perturbation family as first-class registry metadata.**
ReliabilityBench and Gaia2 apply perturbations, but as a phase of a benchmark run rather than as versioned, reusable, task-attached artefacts.
Making a perturbation family something you register, version, and reuse across tasks, so "robust under paraphrase-level-2 and rate-limit fault injection" becomes a reusable gate condition, is a modest but real extension.

What I would drop: any ambition to build tracing, a trajectory store, a scorer framework, a sandbox provisioner, or a task container format.
Those are solved, and solved better than a new project will manage.

## 6. Open questions

1. Does Inspect ship or plan a first-party OpenTelemetry hook?
I found MLflow tracking and tracing hooks and a community `inspect-mlflow` package but no OTel equivalent, and I did not exhaustively enumerate the repository, so treat the absence as unverified.
If one is planned, AgentLab should contribute to it rather than fork.

2. What is the actual licence of Inspect Scout, and is Meridian Labs' governance stable enough to depend on?
The docs site does not state a licence on the pages I read, and Scout sits at the centre of the recommended architecture.
This needs checking before committing.

3. Can Scout's transcript database carry AgentLab's experiment dimensions (arm, seed, perturbation level, cost) as first-class filterable columns, or only as opaque metadata?
The filter DSL example (`c.task_set`, `c.model.like(...)`) suggests a fixed schema, and the docs point to a "Database Schema" page I did not read.
This determines whether Scout is the store or just an importer.

4. What does statistical power actually cost in dollars for AgentLab's target tasks?
The randomness paper gives required run counts per detectable effect size, and HAL reports per-benchmark costs ranging from $13 to figures where a single benchmark was skipped because it would cost about $20,000.
If a statistically valid gate costs more per merge than the team will tolerate, the whole thesis needs a cheap-tier design (cassette replay, stratified sampling, tiered judges) from day one rather than as an optimisation.

5. Is Harbor or Inspect the primary runner, and how do they compose?
Both provide sandboxing, agent integration, and task definition, and running both means two task formats and two log formats.
A concrete spike comparing "Inspect task with docker sandbox" against "Harbor task run via Inspect agent bridge" would settle this faster than more reading.

6. How should judge calibration be enforced?
The gate-pattern literature is explicit that a judge should be validated against human agreement before it is allowed to block anything, and that you should not use the same model to judge that generated the output.
Source: https://aiarch.dev/patterns/eval-harness-gate
Scout supports validating scanner accuracy against human-labelled examples, but the policy question of what agreement level licences a judge to block a promotion is AgentLab's to answer.

7. Which existing benchmarks are safe to adopt as AgentLab workloads given documented validity defects?
tau-bench's unsolvable-task scoring, WebArena's substring matching, SWE-Lancer's writable test archive, KernelBench's weak fuzzer, and OSWorld's 13-of-46 broken Chrome tasks are all documented.
Source: https://arxiv.org/html/2507.02825
Adopting any of these without an audit pass imports their defects into every promotion decision AgentLab makes.

8. Does the OpenInference donation to OpenTelemetry actually proceed?
An Arize-authored page states the proposal was made in 2026, which I could not confirm on an OpenTelemetry repository.
If it lands, the OpenInference-versus-GenAI-conventions choice in section 4 collapses into one vocabulary and simplifies the recommendation.
