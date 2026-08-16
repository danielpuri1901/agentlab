# Amazon Bedrock AgentCore and AWS Managed Agent Evaluation

Research date: 2026-08-16.
All claims carry a source URL.
Anything not confirmed against a primary source is labelled "unverified".

## 1. Summary

The blueprint's core claim is true: AgentCore Evaluations exists, is GA since March 2026, and does trace-based evaluation with custom evaluators.
It is also better than the blueprint claims, with four run modes (online, on-demand, batch, dataset) and Lambda-backed code evaluators alongside LLM-as-a-judge.
AgentCore itself is not one service, it is 13 separately metered services, GA since October 2025, that you can adopt one at a time.
Runtime hosts any framework in a per-session microVM, capped at 2 vCPU / 8 GB and 8 hours, billed only for active CPU.
The trace contract is the important part: Evaluations reads plain OpenTelemetry GenAI or OpenInference semantic conventions from any agent, not just Strands and LangGraph.
The trap is price: built-in evaluators cost $2.40 per million input tokens, and AWS's own worked example lands at $1,804 per month.
At AgentLab scale that is roughly $31 per experiment run, about 3% of the total $1,000 budget for a single run.
Bedrock Agents Classic closed to new customers on 2026-07-30, so a fresh account cannot use it at all.
The awslabs/agent-evaluation OSS framework is effectively dormant, with no release since 2025-03-31 and no commit since 2025-12-15.
Verdict: adopt the OTel trace standard now, wrap Evaluations behind an adapter, keep Fargate, and skip Memory and Gateway.

## 2. Verified findings per component

### 2.1 What AgentCore actually is today

AgentCore was announced in preview on 2025-07-16 and reached general availability on 2025-10-13.
Source: https://aws.amazon.com/blogs/aws/introducing-amazon-bedrock-agentcore-securely-deploy-and-operate-ai-agents-at-any-scale/
Source: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/

It is not a single runtime product, it is a set of modular services that are billed separately and can be used independently or together.
The current service list in the developer guide is Harness, Runtime, Memory, Gateway, Identity, Code Interpreter, Browser, Observability, Payments, Evaluations, Optimization, Policy, and Registry.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html

The pricing page confirms 13 separately metered features, and adds Web Search as its own meter.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

Two services have appeared since the original blueprint was likely written and are relevant to AgentLab.
Harness is a managed agent loop where you declare model, system prompt, and tools inline, and AWS runs the orchestration.
Optimization generates configuration change recommendations and runs A/B tests on top of Evaluations, which overlaps directly with AgentLab's stated purpose.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html

### 2.2 AgentCore Runtime

Runtime is framework agnostic by design.
The docs name LangGraph, Strands, CrewAI, LlamaIndex, Google ADK, and the OpenAI Agents SDK, plus custom agents that use no framework at all.
It works with any model, inside or outside Bedrock, including Claude, Gemini, OpenAI, Nova, Llama, and Mistral.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html

Session isolation is genuine, not logical.
Each user session runs in a dedicated microVM with isolated CPU, memory, and filesystem.
After the session ends the entire microVM is terminated and memory is sanitised, and a later request with the same session ID creates a fresh execution environment.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agents-tools-runtime.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-how-it-works.html

This is a reproducibility feature, and it is stronger than what a reused Fargate task gives you by default.

There are two compute types with materially different properties.

| Characteristic | microVMs | Instances |
| --- | --- | --- |
| Max session duration | 8 hours | 14 days |
| Management | Serverless, AWS managed | AWS managed EC2 in your own account |
| Operating systems | Linux containers, `arm64` only | Linux, `x86_64` and `arm64` |
| Agents per session | 1:1 | 1:N, up to 20 |
| GPU | Not supported | Supported |
| Pricing | Consumption based, billed by AgentCore | EC2 in your account plus a management fee |

Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-instances-how-it-works.html

The hard limits that matter for AgentLab are these.
Maximum hardware allocation per session is 2 vCPU and 8 GB, and it is not adjustable.
Synchronous request timeout is 15 minutes, asynchronous jobs run up to 8 hours, and streaming connections cap at 60 minutes.
Idle sessions terminate after 15 minutes of inactivity, adjustable through `idleRuntimeSessionTimeout`, and maximum lifetime is adjustable through `maxLifetime`.
Container images are capped at 2 GB, direct code deployment at 250 MB compressed and 750 MB uncompressed.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html

Default concurrency quotas were raised in July 2026 to 5,000 active concurrent sessions in N. Virginia and Oregon and 2,500 elsewhere, with 200 agent interactions per second and 25 new sessions per second in every region.
Source: https://aws.amazon.com/about-aws/whats-new/2026/07/amazon-bedrock-agentcore-increases-default-runtime-quota-limits/

The service contract is simple enough to implement by hand.
An HTTP agent listens on port 8080 and serves `/invocations`, MCP servers listen on 8000 at `/mcp`, A2A on 9000, and AG-UI on 8080.
Session IDs must be at least 33 characters, which is why a UUID4 works and a short string does not.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/runtime-service-contract.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html

The billing model is the real differentiator against Fargate.
Runtime bills active CPU consumption only, so I/O wait while the agent waits on an LLM response or a tool call incurs no CPU charge.
Memory is billed on peak consumed up to each second, with a 128 MB floor and a 1 second minimum.
AWS states agentic workloads spend 30 to 70 percent of session time in I/O wait.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

That property matters for AgentLab, because agent experiment trajectories are almost entirely I/O wait.
Under Fargate you pay for the allocated vCPU for the full task duration including that wait.
The published cold start figure is not stated numerically anywhere I could find, only the marketing phrase "fast cold starts", so the cold start number is unverified.

### 2.3 AgentCore Memory

Memory provides short-term memory for turn-by-turn context within a session and long-term memory that extracts facts, preferences, and summaries across sessions.
Extraction strategies come in three forms: built-in, built-in with overrides, and self-managed, where self-managed runs in your account with your own model and prompt.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/memory.html
Source: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/

Long-term memory storage is billed per stored record per month, which means it is a recurring charge that grows with every experiment run unless a retention policy trims it.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

### 2.4 AgentCore Gateway

Gateway converts APIs, Lambda functions, and existing MCP servers into MCP-compatible tools behind a single endpoint, with semantic tool search and IAM or OAuth authorisation.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html
Source: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/

It is billed per invocation, per semantic search, and per tool indexed per month, and data egress to a customer VPC carries a $0.006 per GB processing charge.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

### 2.5 AgentCore Identity

Identity handles inbound authentication into agents and outbound OAuth or API key flows to third-party services, and it integrates with Cognito, Okta, Entra ID, and Auth0.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/what-is-bedrock-agentcore.html

Notably, Identity incurs no additional charge when used through Runtime or Gateway, and is only metered when called standalone.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

### 2.6 AgentCore Observability

Observability emits telemetry in standardised OpenTelemetry-compatible format, ingested and stored in Amazon CloudWatch in your own account and billed at CloudWatch rates.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

Traces are vendor-neutral and exportable.
The GA announcement explicitly names Dynatrace, Datadog, Arize Phoenix, LangSmith, and Langfuse as supported external destinations.
Source: https://aws.amazon.com/about-aws/whats-new/2025/10/amazon-bedrock-agentcore-available/

The data model is a three-level hierarchy of sessions, then traces, then spans, which maps cleanly onto AgentLab's trajectory concept.
By default AgentCore only emits span data for memory resources, and session-level metrics for runtime.
To get agent-level spans you must instrument your own code with the AWS Distro for OpenTelemetry.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-telemetry.html

This is the key structural finding: the interesting telemetry is something you emit, not something the platform generates for you.
That means AgentLab can produce compliant traces without adopting any AgentCore service at all.

### 2.7 AgentCore Evaluations

The blueprint's claim is verified.
Evaluations was announced in preview at re:Invent on 2025-12-02 and became generally available in March 2026.
Source: https://aws.amazon.com/blogs/aws/amazon-bedrock-agentcore-adds-quality-evaluations-and-policy-controls-for-deploying-trusted-ai-agents/
Source: https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available/

Trace format accepted.
Evaluations reads two open standards: the OpenTelemetry generative AI semantic conventions and the OpenInference semantic conventions.
Named framework support covers Strands Agents, LangGraph, OpenAI Agents, LlamaIndex, Google ADK, and the Claude Agent SDK, Python libraries only.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks.html

Critically, there is generic framework support for any agent at all.
The service selects its reader from the span's `scope.name`, which must begin with `opentelemetry.instrumentation.` or `openinference.instrumentation.`.
Each span must carry an identifying attribute: `gen_ai.operation.name` set to `invoke_agent`, `execute_tool`, or `chat` for the OTel convention, or `openinference.span.kind` set to `AGENT`, `CHAIN`, `TOOL`, or `LLM` for OpenInference.
If you cannot set convention attributes at all, you can still evaluate the top-level turn by setting `agentcore.invocation.user_prompt` and `agentcore.invocation.agent_response`, which the service reads from any scope.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/supported-frameworks-generic.html

So the blueprint understated this. A custom AgentLab agent that emits correct OTel GenAI spans is evaluable without adopting Strands or LangGraph.

Evaluator types.
There are three: built-in LLM-as-a-judge, custom LLM-as-a-judge with your own prompt, model, and scoring scale, and custom code-based evaluators backed by an AWS Lambda function.
Every evaluator is registered at one of three levels: `SESSION`, `TRACE`, or `TOOL_CALL`.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/custom-evaluators.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-evaluator.html

There are 13 built-in evaluators.
The response-quality set named verbatim in the AWS blog is Helpfulness, Correctness, Coherence, Conciseness, Faithfulness, Harmfulness, Instruction Following, Response Relevance, Context Relevance, Refusal, and Stereotyping.
The launch posts additionally name goal success rate and tool selection accuracy, which account for the session and tool levels.
Source: https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/
Source: https://aws.amazon.com/blogs/aws/amazon-bedrock-agentcore-adds-quality-evaluations-and-policy-controls-for-deploying-trusted-ai-agents/

Built-in evaluator configurations, including their models and prompt templates, cannot be modified.
They use cross-region inference to select compute across your geography, while keeping data stored in the originating region.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/built-in-evaluators-overview.html
Source: https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/

Four run modes exist, and the differences matter for an experimentation platform.

| Mode | Trigger | Session source | Ground truth | Use case |
| --- | --- | --- | --- | --- |
| On-demand | Caller-initiated, synchronous | Caller provides spans inline | Yes, via `evaluationReferenceInputs` | Dev spot checks, CI/CD gates |
| Online | Continuous, event-driven | Watches a CloudWatch log group | Not supported | Production monitoring |
| Batch | Caller-initiated, asynchronous | Service discovers from CloudWatch Logs | Yes, via `sessionMetadata` | Baseline, pre/post comparison, regression |
| Dataset (preview) | SDK runner | Runs the agent, then evaluates | Yes | Regression suites, benchmarks |

Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/batch-evaluations.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/on-demand-evaluations.html

Batch evaluation is the mode that matches AgentLab's workload, because it discovers sessions server-side, runs asynchronously, supports ground truth, and returns aggregate per-evaluator averages.
Dataset evaluation is still public preview, so APIs may change.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations.html

Ground truth support covers reference answers, behavioural assertions for session-level goals, and expected tool execution sequences, exposed as the `expected_response`, `assertions`, and `expected_tool_trajectory` placeholders.
Evaluators that use ground truth placeholders cannot be used in online configurations, and the service enforces this at evaluator creation time.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-evaluator.html
Source: https://aws.amazon.com/about-aws/whats-new/2026/03/agentcore-evaluations-generally-available/

The code-based evaluator contract is worth reproducing, because it is the budget-safe path and AgentLab can target it directly.
AgentCore assumes an IAM role in your account and invokes your Lambda with this payload shape.

```json
{
    "schemaVersion": "1.0",
    "evaluatorId": "my-evaluator-abc1234567",
    "evaluatorName": "MyCodeEvaluator",
    "evaluationLevel": "TRACE",
    "evaluationInput": { "sessionSpans": [] },
    "evaluationReferenceInputs": [],
    "evaluationTarget": { "traceIds": ["trace123"], "spanIds": ["span123"] }
}
```

`sessionSpans` may be truncated if the payload exceeds 6 MB, which is a real constraint for long trajectories.
The Lambda must return a `label` on success, with optional `score` between 0.0 and 1.0 and optional `explanation`, or an `errorCode` and `errorMessage` on failure.
Lambda timeout is configurable from 1 to 300 seconds, defaulting to 60.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-based-evaluators.html

API and CLI surface.
Control plane operations include `CreateEvaluator`, `CreateOnlineEvaluationConfig`, and `StartBatchEvaluation`, and the data plane operation is `Evaluate`.
There is an `agentcore` CLI with `agentcore add evaluator`, `agentcore add online-eval`, `agentcore run eval`, and `agentcore deploy`, plus a Python `Evaluation` class in `bedrock_agentcore_starter_toolkit`, plus `aws bedrock-agentcore-control create-evaluator` in the AWS CLI.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/create-evaluator.html
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-based-evaluators.html
Source: https://github.com/aws/agentcore-cli/blob/HEAD/docs/evals.md

Operational constraints.
A single on-demand call may reference up to 10 evaluators and returns at most 10 evaluations.
When an online evaluation configuration referencing an evaluator is enabled, that evaluator is locked and cannot be modified or deleted until the configuration is disabled.
Source: https://aws.amazon.com/blogs/machine-learning/build-reliable-ai-agents-with-amazon-bedrock-agentcore-evaluations/
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/code-based-evaluators.html

That locking behaviour is a reproducibility hazard worth noting: it means an evaluator version cannot drift while in use, which is good, but it also means AgentLab must clone rather than mutate evaluators between experiment generations.

### 2.8 awslabs/agent-evaluation

The repository is live and Apache 2.0 licensed, but it is effectively dormant.
It has roughly 370 stars, 49 to 51 forks, and 23 open issues, and it is not archived.
The last commit to `main` is dated 2025-12-15.
The last PyPI release is version 0.4.1 dated 2025-03-31, and the package is still classified "4 - Beta" with about 232 downloads per month.
Source: https://github.com/awslabs/agent-evaluation
Source: https://pypi.org/project/agent-evaluation/

What it does is close to one slice of AgentLab.
It implements an LLM evaluator agent that orchestrates concurrent, multi-turn conversations against a target agent and judges the responses as the conversation proceeds.
Test plans are YAML, with `steps`, `expected_results`, `initial_prompt`, `max_turns` defaulting to 2, and a `hook` module path for integration assertions.
It ships a CLI (`agenteval run`), emits `agenteval_summary.md`, writes per-test trace files, and is designed to drop into GitHub Actions.
Built-in targets are Bedrock Agents, Q Business, and SageMaker, with a custom target extension point.
Source: https://awslabs.github.io/agent-evaluation/
Source: https://awslabs.github.io/agent-evaluation/user_guide/
Source: https://awslabs.github.io/agent-evaluation/reference/test/
Source: https://aws.amazon.com/blogs/machine-learning/evaluate-conversational-ai-agents-with-amazon-bedrock/

The overlap with AgentLab's evaluation engine is direct but the dependency is not advisable.
Its primary built-in target, Bedrock Agents, is now in maintenance mode, its release cadence stopped 17 months ago, and AgentCore Evaluations covers the same ground with active investment.
The test plan schema is worth copying as a design reference. The code is not worth depending on.

### 2.9 Amazon Bedrock model evaluation jobs

This is a separate, older feature that still exists and is not deprecated.
It covers programmatic evaluation jobs, human-worker evaluation, LLM-as-a-judge model evaluation, and RAG evaluation against Knowledge Bases.
LLM-as-a-judge and RAG evaluation went GA around 2025-03-20 and gained "bring your own inference responses", so you can evaluate a model or RAG system hosted anywhere by supplying outputs in the required JSONL format.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/evaluation.html
Source: https://aws.amazon.com/blogs/machine-learning/evaluate-models-or-rag-systems-using-amazon-bedrock-evaluations-now-generally-available/

Its pricing model is materially different and cheaper.
There is no charge for the evaluation job itself, you pay only standard on-demand model inference for the judge and generator, plus $0.21 per completed human task.
Judge prompt templates are published in the documentation.
Source: https://aws.amazon.com/bedrock/pricing/
Source: https://aws.amazon.com/blogs/aws/new-rag-evaluation-and-llm-as-a-judge-capabilities-in-amazon-bedrock/

Relevance to AgentLab is limited but not zero.
It operates on prompt and response pairs and RAG retrievals, not on agent trajectories with tool calls, so it is the wrong altitude for trajectory evaluation.
Its value is as a price anchor: it proves AWS will run LLM-as-a-judge at raw token cost, which makes the AgentCore Evaluations markup a deliberate convenience premium rather than an unavoidable cost.

### 2.10 Bedrock Agents (pre-AgentCore)

Bedrock Agents, launched November 2023, is now named Amazon Bedrock Agents Classic and entered maintenance mode.
It closed to new customers on 2026-07-30, and accounts with no prior service usage are refused at `CreateAgent`.
Existing customers keep running with no announced end-of-life date, and no new features are planned.
The model catalogue is frozen as of that date, so models released afterwards are only available through AgentCore.
Source: https://docs.aws.amazon.com/bedrock/latest/userguide/agents-classic-maintenance-mode.html

For AgentLab this is decisive rather than merely advisory.
A new AWS account created for this project cannot create a Bedrock Agents Classic agent at all.
It is not a build option, so no adapter or abstraction needs to accommodate it.

## 3. Pricing and quota table

All prices are AWS list rates from https://aws.amazon.com/bedrock/agentcore/pricing/ read on 2026-08-16.
Foundation model inference is billed separately through Bedrock and is not included in any line below.

| Component | Unit | Price | Key quota |
| --- | --- | --- | --- |
| Runtime microVM, CPU | vCPU-hour, active only | $0.0895 | 2 vCPU / 8 GB max per session, not adjustable |
| Runtime microVM, memory | GB-hour, peak | $0.00945 | 128 MB minimum billed, 1 second minimum |
| Runtime Instances | EC2 in your account | EC2 price plus a management fee as a percentage of EC2 On-Demand | Sessions up to 14 days, 20 agents per session |
| Runtime sessions | n/a | n/a | 5,000 concurrent in us-east-1 and us-west-2, 2,500 elsewhere; 25 new sessions/sec; 1,000 TPS data plane |
| Browser | vCPU-hour and GB-hour | $0.0895 and $0.00945 | Same active-consumption model |
| Code Interpreter | vCPU-hour and GB-hour | $0.0895 and $0.00945 | Same active-consumption model |
| Gateway | API invocations | $0.005 per 1,000 | Egress to customer VPC $0.006/GB |
| Gateway | Search API | $0.025 per 1,000 | |
| Gateway | Tool indexing | $0.02 per 100 tools per month | |
| Memory | Short-term events | $0.25 per 1,000 new events | |
| Memory | Long-term storage, built-in strategy | $0.75 per 1,000 records per month | Billed hourly on a 31-day month |
| Memory | Long-term storage, override or self-managed | $0.25 per 1,000 records per month | |
| Memory | Long-term retrieval | $0.50 per 1,000 retrievals | |
| Identity | OAuth token or API key requests | No charge when used via Runtime or Gateway | |
| Web Search | Query | $7.00 per 1,000 | |
| Evaluations, built-in | Input tokens | $0.0024 per 1,000, that is $2.40 per 1M | Model usage included |
| Evaluations, built-in | Output tokens | $0.012 per 1,000, that is $12.00 per 1M | |
| Evaluations, batch | Input tokens | $0.0018 per 1,000, that is $1.80 per 1M | 25% cheaper than online and on-demand |
| Evaluations, batch | Output tokens | $0.009 per 1,000, that is $9.00 per 1M | |
| Evaluations, custom | Per evaluation | $1.50 per 1,000, model billed separately | Up to 10 evaluators per on-demand call |
| Evaluations | n/a | n/a | 1,000 eval configurations per region per account; 1M input+output tokens per minute per account |
| Observability | Telemetry | CloudWatch rates, ingestion, storage, query, PII masking | Billed in your own account |
| Harness | n/a | No extra charge, you pay underlying resources | |
| Agent Registry (preview) | n/a | No charge during preview | |

Evaluations quota source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/evaluations.html
Runtime quota source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/bedrock-agentcore-limits.html

### The budget arithmetic that matters

AWS publishes its own worked example on the pricing page.
For 15,000 interactions per month with 3 built-in evaluators plus 1 custom evaluator, the total is $1,804.50 per month.
That is 1.8 times AgentLab's entire $1,000 budget, for one month of evaluation alone.
Source: https://aws.amazon.com/bedrock/agentcore/pricing/

Scaled down to a plausible AgentLab experiment, the numbers are still uncomfortable.
Assume one experiment run is 200 trajectories, each serialising to roughly 20,000 input tokens for a session-level judge, producing 300 output tokens, scored by 3 built-in evaluators.
That is 600 evaluations, 12M input tokens, and 180,000 output tokens.
At on-demand rates that is $28.80 plus $2.16, roughly $31 per experiment run.
At batch rates it is roughly $23 per run.
Either way, a single experiment run consumes 2 to 3 percent of the total budget before any agent inference cost is counted.
Roughly 32 experiment runs would exhaust the budget on judging alone.

The escape hatch is code-based evaluators, which consume no judge tokens and only invoke a Lambda.
Deterministic gates such as schema validation, tool sequence assertions, and exact-value checks belong there, not in an LLM judge.

### Region availability

AgentCore reached GA in 9 regions and has since expanded.
The current feature-by-region table shows Runtime microVMs, Gateway, Identity, Built-in Tools, Observability, and Policy available in all 20 listed regions including GovCloud.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html

For EU specifically, Evaluations and Memory are available in Frankfurt, Ireland, London, Paris, and Stockholm, and are not available in Milan or Spain.
Runtime Instances in the EU are limited to Frankfurt and Ireland.
Source: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-regions.html

Practical consequence: eu-west-1 (Ireland) or eu-central-1 (Frankfurt) support every AgentCore service AgentLab could plausibly want.
Region choice does not constrain the architecture.

## 4. Use, wrap, or skip verdicts

| Component | Verdict | Reasoning |
| --- | --- | --- |
| Runtime | Integrate behind an adapter, keep Fargate as default | Two of AgentLab's goals collide here. The learning goal argues for Fargate, because Runtime hides exactly the scheduling, isolation, and lifecycle mechanics you are trying to build and understand. The budget goal argues for Runtime, because active-CPU-only billing is genuinely favourable for I/O-bound agent trajectories, and per-session microVM teardown gives stronger reproducibility than a reused task. Resolve it by defining an `ExecutionBackend` interface with a Fargate implementation first, then benchmarking one real experiment on Runtime. Watch the 2 vCPU / 8 GB cap, the `arm64`-only microVM constraint, and the 8 hour ceiling. |
| Memory | Skip | Wrong workload. AgentLab needs deterministic, versioned starting state so runs are comparable, and a service whose job is to accumulate learned facts across sessions works against that. Long-term storage also bills per record per month forever, which is a slow leak against a fixed budget with no revenue. DynamoDB and S3 already cover the state AgentLab actually needs. |
| Gateway | Skip | Its value is governance of a shared tool surface across many teams, which AgentLab does not have. It would turn the tool set from a versioned artifact into a cloud resource, which hurts reproducibility, add a network hop, and add per-invocation cost. Define tools in code, version them with the experiment config. |
| Evaluations | Integrate behind an adapter, with the judge swappable | The blueprint's claim is verified and the capability is real, well-designed, and better than described. The problem is unit economics: $2.40 per 1M input tokens on trajectory-sized inputs will eat the budget. So build AgentLab's `Evaluator` interface with two implementations from the start. A default that calls Bedrock Converse directly with your own judge prompt, giving raw token cost and full prompt control, which is what reproducible research needs anyway. And an AgentCore Evaluations implementation used in batch mode for calibration, so you can check your own judge against the 13 built-ins periodically rather than continuously. Use code-based Lambda evaluators for every deterministic gate, since they cost no judge tokens. |
| Observability | Use the standard directly, treat the service as an optional sink | This is the highest-value, lowest-cost finding. Adopt OpenTelemetry GenAI semantic conventions plus OpenInference as AgentLab's native trace format now. It costs nothing, it is vendor-neutral, it is the exact contract AgentCore Evaluations reads through generic framework support, and it keeps Langfuse, Phoenix, and Datadog available as alternatives. Write the durable trajectory record to S3, which is cheap and fully under your control, and treat CloudWatch ingestion as a switchable second sink rather than the system of record. |
| Harness | Skip | A managed agent loop is the opposite of what an agent experimentation platform needs. AgentLab's entire purpose is varying the loop and measuring the effect, which requires owning it. |
| Optimization | Watch, do not adopt | It is the closest commercial overlap with AgentLab's stated purpose, since it generates configuration recommendations and runs A/B tests over traces. Worth reading as prior art and as a check on whether AgentLab's design is differentiated. Adopting it would mean outsourcing the core thesis. |
| Bedrock Agents Classic | Not an option | Closed to new customers as of 2026-07-30. A fresh account is refused at `CreateAgent`. |
| Bedrock model evaluation jobs | Skip for trajectories, keep as a price anchor | Operates on prompt/response pairs and RAG, not agent trajectories with tool calls. Useful only as evidence that raw-token judging is available, which justifies making AgentLab's judge swappable. |
| awslabs/agent-evaluation | Read for design, do not depend | Dormant since 2025-12-15, no release since 2025-03-31, still beta, and its main target is a maintenance-mode service. The YAML test plan schema with `steps`, `expected_results`, and `max_turns` is a good reference for AgentLab's scenario format. |

## 5. Open questions

Are code-based Lambda evaluators billed at the custom evaluator rate of $1.50 per 1,000 evaluations, or are they free of AgentCore charges because no judge model is invoked?
The pricing page groups all custom evaluators under one line and the docs do not clarify.
This is material, because it is the difference between roughly $0.90 and $0.00 per 600-evaluation experiment run, and it determines how aggressively AgentLab should push gates into Lambda.

What is the actual AgentCore Runtime cold start latency?
The docs say "fast cold starts" but publish no number, so any comparison against Fargate task startup is currently unverified.
This needs a measurement, not a document.

How does the Runtime active-CPU rate of $0.0895 per vCPU-hour compare in practice to Fargate for a real AgentLab trajectory?
The rates are not directly comparable because Runtime bills active CPU only and Fargate bills allocated vCPU for the whole task.
The aws-costs teammate should model this against a concrete trajectory shape rather than comparing list rates.

What does CloudWatch Transaction Search cost at AgentLab's trace volume?
Evaluations requires it for telemetry delivery when the agent is not hosted on AgentCore Runtime, and Observability bills through CloudWatch rather than AgentCore.
This is an unpriced dependency in the current plan.
Source for the requirement: https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations.html

Does the 6 MB `sessionSpans` truncation limit in code-based evaluators bite for long agent trajectories?
A multi-hour session with many tool calls could exceed it, and the docs only say spans "may be truncated" without describing the truncation strategy.
If it truncates from one end, session-level assertions about tool ordering could silently become wrong rather than fail loudly.

Is AgentCore Evaluations' built-in evaluator set version-pinned across time?
Built-in configurations "cannot be modified" by the customer, but the docs do not say whether AWS revises the prompt templates or judge models underneath.
For reproducible experiments this matters, since a silently updated judge would break comparability of results across months.
This argues on its own for AgentLab owning its primary judge and using built-ins only as a periodic calibration reference.
