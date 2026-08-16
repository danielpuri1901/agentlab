AgentLab — Engineering & Research Handoff
Version 1.0 • Research-first blueprint • AWS-native prototype • Prepared for Cloud Code
1. Executive Summary
AgentLab is an experimentation platform for autonomous agents. Its purpose is not to build one supposedly optimal agent, but to create a reproducible system that can continuously test ideas from frontier research and production systems, measure whether they work, and promote only changes that clear explicit evaluation gates.
The central loop is: observe a claim → formulate a hypothesis → implement a candidate → run controlled experiments → evaluate against a baseline → analyze cost/reliability/quality → promote, reject, or queue for more research. AgentLab should become a laboratory for agent engineering.
The project also serves as a serious AWS/distributed-systems learning environment. AWS usage should be intentional: spend credits on experiments that teach orchestration, queues, state, isolation, observability, failure recovery, deployment, and cost management.
2. North Star
Build a cloud-native system that can answer, with reproducible evidence: “Does this agent technique actually improve our workloads enough to justify its complexity and cost?”
Every agent execution is observable and replayable.
Every experiment has a declared hypothesis, baseline, variables, metrics, and promotion criteria.
Agent implementations are replaceable: models, harnesses, planners, memory systems, tools, evaluators, and runtimes should be adapters behind stable interfaces.
Research claims are treated as hypotheses, not facts.
Production-system patterns are treated as evidence and inspiration, not unquestioned architecture.
Changes to production behavior require evaluation gates and explicit policy.
Research code and production infrastructure remain separated.
3. Important Scope Correction
Do not attempt to build a universal Agent OS before proving the experimentation loop. The initial product is AgentLab: execution + telemetry + evaluation + controlled experimentation. A broader Agent OS can emerge from the interfaces that prove useful.
Do not build a custom agent harness immediately. Define an Agent Harness interface and initially integrate existing harnesses such as Claude Code-compatible workflows and other open-source/production harnesses. Build a custom harness only when experiments demonstrate a concrete gap.
4. Core Architecture
Conceptual pipeline:
Task → Environment → Agent Runtime/Harness → Tools → State/Memory → Trajectory → Evaluator → Experiment Analyzer → Promotion Gate → Registry → Next Experiment
Task Registry: canonical tasks, variants, expected conditions, difficulty, and metadata.
Environment Simulator: mutable world state, external APIs, files, databases, failures, latency, and stochastic events.
Agent Runtime: executes an agent/harness inside an isolated runtime.
Tool Layer: MCP and ordinary APIs exposed through explicit permissions.
State Layer: durable task/agent state plus object storage for large artifacts.
Trajectory Store: model calls, tool calls, observations, state transitions, timing, errors, costs, and outputs.
Evaluation Engine: deterministic checks, rubric checks, trajectory checks, LLM-as-judge, and human review.
Experiment Engine: A/B tests, parameter sweeps, ablations, and repeated stochastic trials.
Research Engine: ingest papers/production reports, extract claims, create candidate hypotheses, and queue experiments.
Promotion Registry: records which configurations are approved, rejected, experimental, or deprecated.
5. AWS Mapping
5.1 Compute
Use ECS on Fargate for the first agent-runtime implementation. Each execution can be an isolated ECS task/container with explicit CPU, memory, network, IAM role, image, and environment configuration. AWS Step Functions can start and manage ECS/Fargate jobs, including synchronous job patterns.
Use Lambda for short-lived glue operations: API handlers, event transforms, lightweight validation, webhooks, and small orchestration steps. Do not force long-running agent execution into Lambda.
Use EC2 later when sustained utilization, specialized hardware, GPU workloads, or cost optimization justify owning more of the compute layer.
Use EKS only when Kubernetes itself becomes a learning objective or when the workload demonstrates a real need for Kubernetes primitives. EKS is an orchestration platform, not simply “more Fargate.”
5.2 Orchestration
Start with Step Functions for explicit state-machine workflows where durable orchestration, retries, timeouts, human approval, and branching matter. Use EventBridge for event routing and SQS for durable work queues/backpressure.
Do not assume every agent state belongs in Step Functions. Keep application state in a dedicated state store and use workflow orchestration where it provides clear value.
5.3 State and Storage
DynamoDB: task metadata, execution state, experiment metadata, evaluator results, promotion status, locks/idempotency records.
S3: trajectories, logs/artifacts, datasets, environment snapshots, generated reports, research documents, replay bundles.
Optional relational database later: experiment analytics, complex queries, joins, and research analysis.
Do not introduce a vector database until a measured retrieval problem exists.
5.4 AI/Model Layer
Use Amazon Bedrock or external model APIs behind a ModelProvider interface. Do not hard-code AgentLab to one model vendor.
AWS currently provides Bedrock evaluation capabilities, including agent-trace evaluation and custom evaluators. AgentLab should evaluate whether those managed capabilities are sufficient before building redundant infrastructure. AWS documentation states that AgentCore Evaluations can evaluate traces from supported frameworks and custom evaluators, and the CLI/API can score traces and compare against reference inputs.
IMPORTANT RESEARCH TASK: verify current Bedrock AgentCore availability, pricing, quotas, supported runtimes/frameworks, and whether it should be used directly or only as an optional evaluator adapter.
5.5 Observability
CloudWatch: logs, metrics, alarms, operational dashboards.
OpenTelemetry/OpenInference: standardized agent traces where supported.
Correlation IDs: task_id, experiment_id, run_id, agent_version, environment_version.
Cost telemetry: model token usage, runtime duration, compute cost estimates, evaluator cost, storage cost.
6. Agent State Machine
Minimum execution states:
CREATED
QUEUED
STARTING
RUNNING
WAITING_FOR_TOOL
WAITING_FOR_HUMAN
RETRYING
COMPLETED
FAILED
CANCELLED
EVALUATING
PROMOTION_PENDING
Every transition should be explicit, timestamped, attributable, and idempotent. The system must tolerate duplicate events and retries.
7. Agent Harness Interface
Define a stable contract roughly equivalent to:
start(task, environment, configuration)
execute()
emit_event(event)
request_tool(tool_request)
request_human(request)
checkpoint(state)
resume(checkpoint)
stop(reason)
The exact interface is intentionally a research item. Cloud Code must compare existing harnesses and determine what abstractions are genuinely common rather than designing an elaborate interface prematurely.
8. Environment Simulator
The environment is critical. Static benchmarks are insufficient because they encourage agents to optimize for known task patterns.
Mutable world state.
Deterministic seed for replay.
Stochastic mode for robustness testing.
Mock APIs with configurable latency, errors, rate limits, and schema changes.
Filesystem and database state.
Human events and ambiguous instructions.
External events that can arrive asynchronously.
Time progression where appropriate.
The simulator should expose the same kind of tools an agent would encounter in a real system, while keeping the environment fully reproducible.
9. Canonical Workloads
Do not start with fake conversations alone. Choose tasks that require meaningful work and produce measurable outcomes.
Software engineering: issue → inspect repository → modify code → run tests → diagnose failures → submit patch.
Research: question → search approved corpus → collect evidence → synthesize report → cite evidence → identify uncertainty.
Business operations: ticket → inspect customer/account state → apply policy → update mock CRM → escalate ambiguous cases.
Infrastructure operations: diagnose simulated service failure → inspect logs/metrics → propose and execute recovery → verify health.
Initial recommendation: software engineering plus a simulated operations environment. These naturally generate tool use, state transitions, failures, retries, ambiguity, and objective evaluation.
10. Evaluation System
Evaluation is the central safety and scientific layer. AgentLab should never allow “the agent says it improved” to be the promotion criterion.
Outcome correctness.
Task completion rate.
Constraint adherence.
Tool-call correctness.
Recovery from failures.
Human intervention rate.
Time to completion.
Model/token cost.
Compute cost.
Number of tool calls.
Number of retries.
Unnecessary actions.
Robustness across environment variants.
Regression against historical tasks.
Use multiple evaluator types: deterministic assertions where possible; trajectory evaluators for tool behavior; model-based judges for subjective quality; and human review for high-impact or ambiguous cases.
IMPORTANT: avoid correlated evaluator failure. An evaluator using the same model, prompt, or assumptions as the target agent can produce false confidence. Cloud Code must research evaluator independence and calibration.
11. Self-Evaluation and Drift
Agents may invoke a CLI/API such as `agentlab eval`, `agentlab replay`, or `agentlab drift`, but evaluation authority should remain outside the agent. An agent may request an evaluation; it should not be able to rewrite its own score or promote itself.
Drift should cover both task performance and system behavior: success rate, cost, latency, tool-selection distribution, failure classes, evaluator disagreement, and performance across environment variants.
A drift alert should create an investigation task rather than automatically mutate production behavior.
12. Experiment Engine
Every experiment must contain:
Hypothesis.
Baseline configuration.
Candidate configuration.
Independent variables.
Controlled variables.
Dataset/task set.
Environment version.
Model versions.
Number of trials.
Random seeds or stochastic policy.
Primary metrics.
Secondary metrics.
Cost budget.
Promotion threshold.
Statistical/uncertainty treatment.
Examples: reflection vs no reflection; planner A vs planner B; memory enabled vs disabled; different tool-selection strategies; different harnesses; different context compression strategies; paper-derived technique vs baseline.
13. Research Engine
Research ingestion should collect papers, technical reports, engineering blogs, benchmark results, and production case studies. For each source, extract:
Claim.
Problem addressed.
Assumptions.
Method.
Reported improvement.
Evaluation setup.
Known limitations.
Implementation complexity.
Infrastructure requirements.
What would falsify the claim in AgentLab.
The system should turn a research claim into a candidate experiment rather than treating the paper as an implementation specification.
14. Production Case Studies
Kavak is a particularly relevant reference because a16z describes its shift from copilots to agents and reports that AI agents handle roughly 90–95% of customer interactions. The key lesson for AgentLab is not to copy Kavak's architecture, but to study the organizational and feedback-loop implications of deploying agents in real workflows.
Research task: identify additional production systems with public technical detail, especially systems involving long-running agents, sandboxing, tool use, human escalation, evaluation, memory, and continuous improvement.
15. AWS Learning Objectives
ECS/Fargate: container lifecycle, isolation, networking, task definitions, IAM.
SQS: durable queues, visibility timeout, retries, dead-letter queues, backpressure.
Step Functions: explicit state machines, retries, compensation, human approval.
DynamoDB: partitioning, conditional writes, idempotency, consistency trade-offs.
S3: immutable artifacts, versioning, lifecycle management.
EventBridge: event-driven architecture and decoupling.
CloudWatch/OpenTelemetry: distributed tracing and operational observability.
IAM: least-privilege agent/tool permissions.
Bedrock: model abstraction and managed evaluation experiments.
EKS: optional advanced phase, only after ECS/Fargate is understood.
16. Cost Strategy
AWS credits are finite. Do not keep expensive infrastructure running merely because credits exist.
Develop locally whenever possible.
Use short-lived Fargate tasks for experiments.
Automatically stop/delete idle resources.
Tag every resource with experiment_id and owner.
Set budgets and alarms before large experiments.
Record cost per successful task.
Prefer small repeated experiments over one enormous benchmark.
Reserve a meaningful portion of the credits for later experiments after the architecture is understood.
Cloud Code must research current AWS pricing rather than hard-coding cost assumptions.
17. Security and Isolation
Agents are untrusted workloads by default.
Separate IAM roles per runtime where practical.
No unrestricted AWS credentials inside agent containers.
Explicit allowlists for tools and network access.
Per-task filesystem/workspace isolation.
Secrets never stored in prompts, logs, or S3 artifacts.
Resource limits and execution timeouts.
Human approval for destructive actions.
Audit every privileged action.
18. Reproducibility
Every run must be replayable from a recorded bundle containing task definition, environment version, agent configuration, harness version, model identifier, tool versions, random seed, relevant inputs, and trajectory.
If exact replay is impossible because an external dependency is nondeterministic, record enough information to reproduce the dependency or explicitly mark the run as non-replayable.
19. Promotion Policy
Candidate improvements must pass gates before becoming the default.
No statistically or practically meaningful regression on protected tasks.
Primary metric exceeds the defined threshold.
Cost increase is within the allowed budget or justified by a larger quality gain.
Robustness is acceptable across environment variants.
Security/policy checks pass.
Evaluation confidence is sufficient.
Human approval is required for high-impact changes.
Promotion should create a new immutable version rather than mutate the old version in place.
20. Minimal Vertical Slice
Before building the full research engine, implement exactly one complete path:
Submit task → queue → launch isolated Fargate agent → agent uses tools → capture trajectory → persist state/artifacts → evaluate → compare to baseline → display result → store experiment record.
No autonomous self-modification in this phase.
21. Phased Roadmap
Phase 0 — Research
Survey existing agent harnesses.
Survey agent evaluation systems.
Survey long-running agent architectures.
Survey self-improvement literature.
Survey production examples.
Compare AWS-native services and managed agent capabilities.
Create architecture decision records.
Create open-questions register.
Phase 1 — Architecture
Define task, agent, environment, trajectory, evaluator, experiment, and promotion schemas.
Define harness/model/tool interfaces.
Define state machine.
Define event contracts.
Define security model.
Define cost model.
Phase 2 — Vertical Slice
Local development environment.
Containerized agent runtime.
SQS + Fargate execution.
DynamoDB state.
S3 artifacts.
CloudWatch/OpenTelemetry telemetry.
One evaluator.
One dashboard/report.
Phase 3 — Controlled Experiments
Baseline vs candidate.
Multiple harnesses.
Multiple models.
Environment perturbations.
Repeated stochastic trials.
Cost-quality analysis.
Phase 4 — Research-to-Experiment Loop
Research ingestion.
Claim extraction.
Hypothesis generation.
Candidate implementation generation.
Automated experiment execution.
Evaluation gates.
Evidence report.
Phase 5 — Continuous Improvement
Drift detection.
Automated regression suites.
Candidate configuration generation.
Human approval workflows.
Promotion registry.
Continuous research queue.
22. What NOT to Build Yet
A foundation model.
A custom vector database.
A universal agent framework.
Kubernetes infrastructure without a demonstrated need.
Automatic production self-modification.
Large-scale fine-tuning before evaluation is trustworthy.
A massive multi-agent simulation before one agent loop is measurable.
A UI-heavy product before the underlying data model and experiment loop work.
23. Research Questions Cloud Code MUST Resolve
Do not guess. For each item, research current primary sources and record evidence.
What is the best current AWS architecture for thousands of short/long-lived isolated agent jobs?
When should ECS/Fargate give way to EC2 or EKS for this workload?
What are current Fargate, ECS, Step Functions, SQS, DynamoDB, S3, Bedrock AgentCore, and observability costs and quotas?
Which AWS managed evaluation capabilities overlap with AgentLab?
What telemetry standard should be the canonical trace format?
Which agent harness abstractions are actually shared across current systems?
What are the strongest recent methods for agent self-improvement, reflection, memory, planning, and tool learning?
How should stochastic environment generation avoid benchmark overfitting and distribution collapse?
How should LLM-as-judge evaluators be calibrated and audited?
What statistical methodology is appropriate for comparing agent trajectories?
What existing open-source systems already implement significant portions of AgentLab?
Which production systems have publicly documented continuous agent-improvement loops?
What security model is appropriate for agents that can execute code and access tools?
24. Research Sources and Starting Points
AWS documentation confirms that Step Functions can run ECS/Fargate tasks and manage their lifecycle. AWS also documents current Bedrock AgentCore evaluation capabilities, including trace-based evaluation, custom evaluators, and reference inputs.
AWS Labs also maintains an Agent Evaluation framework that can orchestrate concurrent multi-turn conversations with target agents and integrate testing into CI/CD. This is directly relevant and must be evaluated before duplicating that functionality.
The a16z Kavak episode is a production case study to analyze, not an architecture to copy.
25. Definition of Done for v0.1
A task can be submitted through an API/CLI.
The task is durably queued.
An isolated agent execution starts.
The agent can use at least one controlled tool.
State transitions are persisted.
Trajectory is captured.
Execution artifacts are stored.
An evaluator scores the run.
A baseline can be compared with a candidate.
The result includes quality, latency, cost, and failure information.
The entire run is replayable or explicitly marked non-replayable.
AWS costs are attributable to the experiment.
26. Operating Principle
The most important principle is: AgentLab should make it easier to discover that an idea does NOT work. A platform that only produces impressive demos will optimize itself toward confirmation. A scientific experimentation platform must make negative results cheap, visible, reproducible, and useful.
27. Final Instruction to Cloud Code
Do not begin by writing a large codebase. Begin by producing a research report and architecture proposal. Cite primary sources. Explicitly distinguish verified facts, reasonable engineering assumptions, and unresolved questions. Where current AWS capabilities or pricing may have changed, verify them against current AWS documentation.
After research, propose the smallest vertical slice that exercises the highest-value distributed-systems concepts while consuming minimal AWS credits. Then implement incrementally, with infrastructure-as-code, automated tests, observability, cost controls, and an ADR for every significant architectural decision.
The goal is not to build the biggest agent platform. The goal is to build a system that can experimentally determine what makes autonomous agents better, more reliable, cheaper, and more adaptable—and to learn distributed systems engineering by building that system for real.
Appendix A — Suggested Initial Repository Layout
agentlab/   docs/     research/     adr/     architecture/     experiments/   infra/     terraform/   packages/     core/     schemas/     harness/     models/     tools/     evaluation/     experiments/     research/   services/     api/     scheduler/     worker/     evaluator/     research-engine/   environments/     simulator/     fixtures/   cli/   tests/     unit/     integration/     evaluation/   dashboards/   scripts/
Appendix B — Initial CLI Concept
agentlab task submit <task> agentlab task status <run-id> agentlab run replay <run-id> agentlab eval run <run-id> agentlab eval compare <baseline> <candidate> agentlab drift report <agent-version> agentlab experiment create <spec> agentlab experiment run <experiment-id> agentlab research ingest <source> agentlab research claims <source> agentlab research propose <claim-id>
Appendix C — Evidence Note
This document intentionally does not claim that every proposed component is the optimal production choice. It is a hypothesis-driven blueprint. Cloud Code is expected to challenge it, research alternatives, and update the architecture when evidence warrants it.
