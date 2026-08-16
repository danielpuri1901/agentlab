# Agent Harness Landscape and the Minimal Common Interface

Research date: 2026-08-16.
Scope: which agent harnesses AgentLab should integrate first, and what an Agent Harness interface can honestly promise.

## 1. Summary

Only four things are genuinely common across every harness surveyed: a task/prompt entry point, a JSON-Schema tool definition, an ordered event or item stream, and a terminal stop with a reason.
Checkpoint and resume are NOT common, and the proposed `checkpoint`/`resume` methods should be demoted to an optional declared capability.
Every harness means something different by "checkpoint": LangGraph snapshots graph state per super-step, OpenAI serializes a `RunState` only at approval boundaries, Strands emits an opaque cycle marker that explicitly excludes conversation state, and the Claude Agent SDK resumes a conversation transcript but not the filesystem.
`request_human` is only common in the narrow form of tool-call approval, and `emit_event` is a pull-based stream everywhere, never a push callback the harness owes you.
MCP is safe as the tool layer, with one caveat: the current spec revision `2026-07-28` is the largest breaking change since launch, so pin to what harnesses actually speak and avoid sampling, roots, and elicitation.
Recommended first two integrations: the Claude Agent SDK and AWS Strands Agents.
Both run headless in a container and both reach Bedrock natively with no adapter layer, but they sit at opposite ends of the opacity axis, which is what makes comparing them scientifically interesting.
The proven pattern from Inspect, SWE-bench harness adapters, and the Claw-SWE-Bench paper is that you do not normalize the agent loop, you normalize the environment and the model endpoint around it.
The single most important recommendation is to make the model proxy and the container the observation points, not the harness event stream.
Published evidence says harness choice moves benchmark scores about as much as model choice, which is the strongest available justification for AgentLab existing at all.

## 2. Harness-by-harness findings

### 2.1 Claude Agent SDK and headless Claude Code (Anthropic)

The Agent SDK is Claude Code as a library, in Python and TypeScript, running the same agent loop, tools, and context management as the CLI.
Source: https://code.claude.com/docs/en/agent-sdk/overview

Headless operation is first-class.
`claude -p "<prompt>" --output-format json` runs non-interactively, reads stdin, exits 0 on success and non-zero on failure.
There is a `--bare` mode that skips auto-discovery of hooks, skills, plugins, MCP servers, and CLAUDE.md, which the docs explicitly recommend for CI and scripted calls so you get the same result on every machine.
Source: https://code.claude.com/docs/en/headless

For AgentLab this `--bare` flag is important: without it, a `-p` session silently loads hooks and MCP servers from the working directory, which would contaminate an experiment.

Tool definition happens through an in-process MCP server.
You define a tool with the `@tool` decorator (Python) or `tool()` (TypeScript), wrap it with `create_sdk_mcp_server`, and pass it via `mcpServers`.
Source: https://code.claude.com/docs/en/agent-sdk/custom-tools

MCP support covers stdio, HTTP/SSE, and in-process SDK servers, configured either in code via `mcpServers` or from a `.mcp.json` file.
Connection timing differs by type: stdio and uncached HTTP servers delay the first turn up to `MCP_TIMEOUT` (30s default), while in-process SDK servers never delay it.
Source: https://code.claude.com/docs/en/agent-sdk/mcp

Hooks run custom code at lifecycle points, and `can_use_tool` gives a programmatic approval callback.
Source: https://code.claude.com/docs/en/agent-sdk/python

Session persistence is transcript-based.
The SDK writes the conversation to disk automatically and offers three re-entry modes: `continue` (most recent session in the directory), `resume` (a specific session ID), and `fork` (copy history into a new session, leaving the original intact).
The docs are explicit that sessions persist the conversation, not the filesystem, and point to a separate `enable_file_checkpointing` option for file snapshots.
Source: https://code.claude.com/docs/en/agent-sdk/sessions

This is the key semantic distinction for AgentLab: resume here restores context, not world state.
For a containerized experiment where the container is ephemeral, resume alone does not reproduce the run.

Bedrock is supported and verified.
Setting `CLAUDE_CODE_USE_BEDROCK=1` plus AWS credentials routes the session to Bedrock; `ANTHROPIC_BEDROCK_BASE_URL` overrides the endpoint, which is the hook AgentLab would use for a recording proxy.
Sources: https://code.claude.com/docs/en/amazon-bedrock and https://code.claude.com/docs/en/env-vars

Container suitability is demonstrated by AWS's own reference images, which set `CLAUDE_CODE_USE_BEDROCK=1` and `CLAUDE_CONFIG_DIR` in the Dockerfile and run as a non-root user.
Sources: https://github.com/awslabs/fullstack-solution-template-for-agentcore/blob/5c9446bf/patterns/claude-agent-sdk-single-agent/Dockerfile and https://github.com/aws-samples/anthropic-on-aws/tree/main/claude-code-on-agentcore

One operational gotcha worth pre-empting: Bedrock runs need more than `bedrock:InvokeModel`.
A resolved SDK issue shows the CLI also needs `bedrock:InvokeModelWithResponseStream` and `bedrock:ListInferenceProfiles` on `inference-profile/*` ARNs, and failures present as a hang or a generic credentials error rather than an IAM error.
Source: https://github.com/anthropics/claude-agent-sdk-python/issues/224

Maturity: 7,894 stars, MIT, created 2025-06-11, bundles the Claude Code CLI in the package.
Source: https://github.com/anthropics/claude-agent-sdk-python

Note one architectural fact that matters for AgentLab: the Python and TypeScript SDKs drive a CLI subprocess, so the harness boundary is a process boundary, not a function call.
That is good for isolation and bad for in-process instrumentation.

### 2.2 OpenAI Agents SDK

Loop model is a runner-driven tool loop, not a graph.
`Runner.run()` calls the model, executes tool calls, follows handoffs, and returns when the model produces a final output or `max_turns` is exceeded.
Sources: https://openai.github.io/openai-agents-python/running_agents/ and https://developers.openai.com/api/docs/guides/agents/running-agents

Tools come in several kinds: `FunctionTool` wrapping Python functions, hosted OpenAI tools, `HostedMCPTool` for remote MCP servers, local MCP connections via `mcp_servers` on the agent, and agents-as-tools.
Sources: https://openai.github.io/openai-agents-python/tools/ and https://openai.github.io/openai-agents-python/agents/

Streaming is via `Runner.run_streamed()` yielding events while the run is in progress.
Lifecycle observability is good: `RunHooks` observe the whole run and `AgentHooks` attach per agent, with `on_llm_start`/`on_llm_end`, `on_tool_start`/`on_tool_end`, and `on_handoff`.
Source: https://openai.github.io/openai-agents-python/agents/

Checkpoint and resume is the strongest in the field on one narrow axis.
`RunState` is a serializable snapshot with `to_json()`/`from_json()` and `to_string()`/`from_string()`, storing model responses, generated items, approval state, usage, and conversation identifiers.
It is explicitly designed to be persisted to a database or queue and resumed in a different process.
Sources: https://openai.github.io/openai-agents-python/ref/run_state/ and https://openai.github.io/openai-agents-python/human_in_the_loop/

The caveat is that `RunState` is a pause/resume boundary for human-in-the-loop approvals, not an arbitrary time-travel checkpoint.
You obtain it via `result.to_state()` after the run pauses on `interruptions`; there is no documented "snapshot at step N of a healthy run" operation.
Context serialization is also conservative: mapping contexts round-trip, custom contexts need a serializer, and non-JSON values may degrade to their string representation.
Source: https://openai.github.io/openai-agents-python/results/

Human-in-the-loop is a tool-approval model.
Tools declare `needs_approval`, the run pauses, `interruptions` surfaces `ToolApprovalItem` entries, and you `state.approve(...)`/`state.reject(...)` then resume.
Source: https://openai.github.io/openai-agents-python/human_in_the_loop/

Model-agnostic: the README states the SDK is provider-agnostic across the OpenAI Responses and Chat Completions APIs "as well as 100+ other LLMs".
Source: https://github.com/openai/openai-agents-python

Bedrock specifically is reached through the LiteLLM third-party adapter rather than a first-party Bedrock provider.
The dedicated LiteLLM page now redirects to a "Third-party adapters" section under Models.
Source: https://openai.github.io/openai-agents-python/models/litellm/
Treat "OpenAI Agents SDK on Bedrock Converse with tool calling and prompt caching" as unverified until tested; the adapter layer is a real confound for a comparison experiment.

Maturity: 28,645 stars, MIT, created 2025-03-11, only 19 open issues, very active.
Source: https://github.com/openai/openai-agents-python

### 2.3 AWS Strands Agents

Loop model is an explicit event loop.
`event_loop_cycle()` processes one conversation turn covering model inference, tool execution, error recovery, and recursion, yielding typed events throughout.
The terminal `EventLoopStopEvent` payload is a 7-tuple carrying stop reason, message, metrics, request state, interrupts, structured output, and checkpoint.
Source: https://strandsagents.com/docs/api/python/strands.event_loop.event_loop/

Hooks are a strongly typed, composable subscription system.
Events include `AgentInitializedEvent`, `BeforeInvocationEvent`, `AfterInvocationEvent`, `MessageAddedEvent`, `BeforeModelCallEvent`, `AfterModelCallEvent`, `BeforeToolsEvent`, `BeforeToolCallEvent`, `AfterToolCallEvent`, `AfterToolsEvent`, plus multi-agent node events.
All events extend `HookableEvent`, so they are both streamable via `agent.stream()` and subscribable via callbacks.
Sources: https://strandsagents.com/docs/user-guide/concepts/agents/hooks/ and https://strandsagents.com/docs/api/python/strands.hooks.events/

This dual streamable/subscribable design is the cleanest event surface of anything surveyed.

Session persistence is a first-class `SessionManager` abstraction with File, S3, and Repository implementations, persisting on lifecycle events rather than on demand.
The TypeScript SDK additionally supports immutable append-only snapshots keyed by UUID v7, with `listSnapshotIds` and `restoreSnapshot` enabling time-travel restore.
Sources: https://strandsagents.com/docs/user-guide/concepts/agents/session-management/ and https://strandsagents.com/docs/api/python/strands.session.session_manager/

Checkpointing exists but is experimental and deliberately partial.
With `checkpointing=True` the loop pauses at `after_model` and `after_tools` boundaries and returns `stop_reason="checkpoint"` with a populated `checkpoint`; you resume by passing `{"checkpointResume": {"checkpoint": ...}}`.
The docs state plainly that the SDK does not capture conversation state in the checkpoint and that you must pair it with a `SessionManager` for cross-process continuity.
Checkpoints are only emitted on tool_use cycles, so a turn with no tool calls emits none, and `schema_version` rejects incompatible checkpoints on resume.
Sources: https://strandsagents.com/docs/api/python/strands.experimental.checkpoint.checkpoint/ and https://strandsagents.com/docs/api/python/strands.agent.agent/

Human-in-the-loop is supported through `Interrupt` objects surfaced in the stop tuple and in `AgentResult.interrupts`, plus an `interventions` constructor parameter.
Source: https://strandsagents.com/docs/api/python/strands.agent.agent/

Bedrock is the default model provider, not an adapter.
`Agent()` with no model argument uses `BedrockModel`; credentials resolve through boto3; required IAM is `bedrock:InvokeModel` and `bedrock:InvokeModelWithResponseStream`.
Source: https://strandsagents.com/docs/user-guide/concepts/model-providers/amazon-bedrock/

Useful extras for experiment control: `Limits` gives per-invocation caps on turns, output tokens, and total tokens, terminating gracefully with stop reasons like `limit_turns` rather than raising.
Token caps are documented as soft, since checks run at turn boundaries and one oversized response can overshoot by a turn.
Source: https://strandsagents.com/docs/api/python/strands.agent.agent/

Maturity: 6,735 stars, Apache 2.0, created 2025-05-14, monorepo shipping both Python and TypeScript SDKs.
The GitHub repository is now named `strands-agents/harness-sdk` and describes itself as "Build an agent harness and control it end-to-end", with `harness` among its topics.
Source: https://github.com/strands-agents/sdk-python

That rename is worth noting for AgentLab: AWS is positioning Strands explicitly as harness infrastructure, which reduces the risk of the integration point shifting under us.

### 2.4 LangGraph

Loop model is a directed graph of nodes executed in super-steps, not a fixed ReAct loop.
Source: https://docs.langchain.com/oss/python/langgraph/checkpointers

Checkpointing is the most complete of any framework surveyed.
A checkpointer saves a `StateSnapshot` at every super-step boundary, organized into threads keyed by `thread_id`, enabling human-in-the-loop, time-travel debugging, fault tolerance, and conversational memory.
Two storage abstractions exist: a checkpoints table (one row per super-step) and a writes table (one row per node output), where the latter enables "pending writes" recovery so successful nodes in a partially failed super-step are not re-run.
Durability is tunable across `exit`, `async`, and `sync` modes, trading performance against crash recovery.
Sources: https://docs.langchain.com/oss/python/langgraph/checkpointers and https://reference.langchain.com/python/langgraph.checkpoint/base/BaseCheckpointSaver

Human-in-the-loop uses `interrupt()` inside a node, which saves state and waits indefinitely; you resume with `Command(resume=...)` on the same `thread_id`.
There is a sharp semantic hazard here that AgentLab must not paper over: on resume the node restarts from the beginning, not from the `interrupt()` line, so any code before the interrupt runs again.
The docs warn that `while True` + `interrupt()` loops cause exponential re-execution.
Sources: https://docs.langchain.com/oss/python/langgraph/interrupts and https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/

Event streaming is via `graph.stream_events(..., version="v3")` with typed projections `stream.messages`, `stream.values`, `stream.interrupted`, and `stream.interrupts`.
Source: https://docs.langchain.com/oss/python/langgraph/interrupts

Persistence separates checkpointers (thread-scoped short-term) from stores (cross-thread long-term).
Source: https://docs.langchain.com/oss/python/langgraph/persistence

Maturity is strong and it is model-agnostic through LangChain chat models.
The reason it is not a first integration is structural, covered in section 5: LangGraph is a framework for authoring a harness, not a harness.

### 2.5 Google ADK

Loop model is a `Runner` that orchestrates an agent within a session and yields `Event` objects representing each step.
The Runner takes `session_service`, `artifact_service`, `memory_service`, `credential_service`, a `plugin_manager`, and a `resumability_config`.
Source: https://github.com/google/adk-python/blob/main/src/google/adk/runners.py

Resumability exists as a `ResumabilityConfig` on the Runner and `App`, and the runner code shows paused task delegation with `isolation_scope` stamping.
Source: https://github.com/google/adk-python/blob/main/src/google/adk/runners.py
The user-facing semantics of ADK resumability are not documented in the pages retrieved, so treat "ADK supports checkpoint/resume equivalently" as unverified.

State is event-sourced.
State changes must flow through `EventActions.state_delta` applied by `session_service.append_event()`, or through `CallbackContext.state`/`ToolContext.state`, and the docs explicitly warn that mutating a `SessionService`-returned session directly bypasses event tracking and loses data.
Source: https://adk.dev/sessions/state/

This is a genuinely different state model from everything else surveyed and is the reason a generic "get state / set state" interface method cannot be honest.

Callbacks cover agent, model, and tool lifecycle with `before_agent_callback`, `after_agent_callback`, `before_model_callback`, `after_model_callback`, `before_tool_callback`, `after_tool_callback`.
Returning a value from a before-callback short-circuits the underlying call, which makes them usable as interception points.
Source: https://github.com/google/adk-docs/blob/main/docs/callbacks/types-of-callbacks.md

Model support beyond Gemini is via a `LiteLlm` wrapper, requiring `pip install google-adk[extensions]`.
Sources: https://github.com/google/adk-python/blob/main/src/google/adk/models/lite_llm.py and https://adk.dev/tutorials/agent-team/
Bedrock is therefore reachable only through LiteLLM, same adapter-layer concern as the OpenAI SDK.

### 2.6 smolagents (Hugging Face)

Loop model is ReAct via `MultiStepAgent`, with `CodeAgent` writing actions as Python code and `ToolCallingAgent` writing JSON tool calls.
Sources: https://huggingface.co/docs/smolagents/reference/agents and https://github.com/huggingface/smolagents

State is a flat `AgentMemory` holding `SystemPromptStep`, `TaskStep`, `ActionStep`, and `PlanningStep` entries.
There is no checkpoint API, but there is something arguably more useful for research: you can drive the agent one step at a time by constructing an `ActionStep`, calling `agent.step(memory_step)`, appending it to `agent.memory.steps`, and mutating memory between steps.
You can even transplant another agent's memory with `agent.memory.steps = previous_agent.memory.steps`.
Source: https://huggingface.co/docs/smolagents/tutorials/memory

`step_callbacks` provide per-step interception, and `agent.replay()` reprints a past run.
Source: https://huggingface.co/docs/smolagents/reference/agents

Bedrock is supported first-party via `AmazonBedrockModel`, alongside `LiteLLMModel`, `InferenceClientModel`, `AzureOpenAIModel`, and local options.
Source: https://huggingface.co/docs/smolagents/en/guided_tour

Code execution sandboxing is supported through Blaxel, E2B, Modal, or Docker via `executor_type`.
Source: https://github.com/huggingface/smolagents

smolagents is the best candidate for a third integration if AgentLab wants to test the "code actions vs JSON tool calls" axis, which no other pair of harnesses isolates cleanly.

### 2.7 CrewAI

Loop model is role-based crews with sequential or hierarchical process, executed via `.kickoff()`.

MCP is supported two ways: a declarative `mcps` field on the agent (the documented recommended path) and `MCPServerAdapter` from `crewai-tools` for manual lifecycle control, over stdio, SSE, and streamable HTTP.
The adapter documents a real limitation: it primarily adapts MCP `tools`, not `prompts` or `resources`, and it typically processes only the primary text output (`.content[0].text`).
Sources: https://docs.crewai.com/en/mcp/dsl-integration, https://docs.crewai.com/v1.15.10/en/mcp/overview, https://github.com/crewAIInc/crewAI-tools/blob/main/crewai_tools/adapters/mcp_adapter.py

There is a documented interoperability defect directly relevant to AgentLab's stack.
CrewAI MCP tools plus a Bedrock Claude LLM produced `ValidationException: tools.0.input_schema: JSON schema is invalid`, because the tool schema conversion was not provider-agnostic.
The issue was opened 2026-02-12 with fix PRs referenced in February and March 2026.
Source: https://github.com/crewAIInc/crewAI/issues/4472

That is a concrete instance of the general risk in section 6.2: MCP tool schemas are not uniformly translated to every provider's tool format.

Checkpointing and deterministic replay are weak.
Secondary comparisons describe CrewAI replay as partial (task outputs only) and human-in-the-loop as custom-callback only.
Sources: https://markaicode.com/vs/crewai-vs-autogen/ and https://similarlabs.com/blog/crewai-vs-autogen-vs-langgraph
These are secondary sources with inconsistent numbers elsewhere, so treat the specific claims as unverified.

CrewAI is actively maintained through 2026 with the largest install base of the standalone frameworks.
Source: https://www.agenticwire.news/article/ai-agent-framework-status-2026

### 2.8 AutoGen and AG2

This is the clearest "do not integrate" result in the survey.

Multiple secondary sources report that Microsoft placed AutoGen and Semantic Kernel into maintenance mode in October 2025, with Microsoft Agent Framework 1.0 (April 3, 2026) as the successor, and that `microsoft/autogen` receives bug fixes and security patches but no new features.
Sources: https://www.agenticwire.news/article/ai-agent-framework-status-2026, https://www.agenticwire.news/article/agent-frameworks-2026-autogen-ag2-guide, https://similarlabs.com/blog/crewai-vs-autogen-vs-langgraph
The primary `microsoft/autogen` README was not fetched, so the maintenance-mode status is well-corroborated across sources but unverified against the primary.

AG2 is the community fork by the original authors.
GitHub metadata for `ag2ai/ag2` shows 4,860 stars, Apache 2.0, created 2024-11-11, and a v1.0 that is explicitly not a drop-in upgrade from Classic: the agent model, orchestration, and imports all changed, and `ConversableAgent`/`GroupChat` moved to a separate `ag2ai/ag2-classic` repository.
Source: https://github.com/ag2ai/ag2

Be careful with star counts in the secondary literature.
Blog comparisons quote AG2 at 48,400 stars while the GitHub API reports 4,860 for `ag2ai/ag2`; the blogs appear to be conflating `microsoft/autogen`'s historical count with the fork.
Prefer the repository metadata.

The combination of a frozen upstream, a fork that just broke its own API at 1.0, and PyPI package names that changed hands twice makes this the least stable integration target available.

### 2.9 Pydantic AI

Loop model is a typed agent run, and its distinguishing feature is durable execution rather than checkpointing.
Four durable backends are officially supported: Temporal, DBOS, Prefect, and Restate.
Source: https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/

The mechanism is worth understanding because it is a genuinely different answer to the resume problem.
Attaching `TemporalDurability` routes model requests, tool calls, and MCP server communication into Temporal activities while the agent run itself becomes deterministic workflow code, so progress survives crashes by replay rather than by snapshot.
The docs are explicit that attaching the capability alone does not make runs durable: durability comes from executing inside a workflow.
Source: https://pydantic.dev/docs/ai/capabilities/durable_execution/temporal/

Constraints are real: agent `name` and toolset `id`s must be set and must not change after deployment or active workflows break, and toolsets that need durable wrapping must be set at construction time, not per run.
Source: https://pydantic.dev/docs/ai/api/pydantic-ai/durable_exec/

Bedrock support is first-party and unusually thorough.
`bedrock:` uses the Converse API across Anthropic, Amazon, Cohere, Meta, Mistral, DeepSeek, and Qwen; `bedrock-mantle:` covers the OpenAI models Bedrock serves only through Mantle's OpenAI-compatible API.
It exposes Bedrock prompt caching (with automatic management of the 4-cache-point limit), service tiers, and application inference profiles.
Source: https://pydantic.dev/docs/ai/models/bedrock

MCP support runs the server locally by default with an opt-in `native=True` for provider-native MCP.
Source: https://pydantic.dev/docs/ai/mcp/overview/

Pydantic AI is the strongest candidate for a later "durable execution" experiment arm, and its Bedrock provider is the best-documented of any framework surveyed.

## 3. Capability comparison

Legend: Yes = documented first-party. Adapter = only through LiteLLM or similar. Partial = exists but semantically limited. No = not found in docs surveyed.

| Harness | Loop model | Headless container | Bedrock | Event stream | Checkpoint / resume | HITL | MCP client | Maturity signal |
|---|---|---|---|---|---|---|---|---|
| Claude Agent SDK | Opinionated agent loop, CLI subprocess | Yes, `-p` and `--bare` | Yes, native env var | Yes, message iterator + hooks | Partial: session transcript resume/fork; separate file checkpointing | Yes, `can_use_tool` + permissions | Yes: stdio, HTTP/SSE, in-process | 7.9k stars, MIT |
| Strands Agents | Explicit event loop, in-process | Yes | Yes, default provider | Yes, typed hookable events, streamable and subscribable | Partial: experimental cycle markers, no conversation state; SessionManager separately | Yes, interrupts + interventions | Yes | 6.7k stars, Apache 2.0, AWS |
| OpenAI Agents SDK | Runner tool loop + handoffs | Yes | Adapter (LiteLLM) | Yes, streamed events + RunHooks/AgentHooks | Yes, best-in-class `RunState` serialization, but only at approval boundaries | Yes, tool approval interruptions | Yes, hosted and local | 28.6k stars, MIT |
| LangGraph | Graph super-steps | Yes | Adapter (LangChain chat models) | Yes, `stream_events` v3 typed projections | Yes, most complete: per-super-step snapshots, time travel, pending writes | Yes, `interrupt()` / `Command(resume=)` | Yes (via LangChain MCP adapters, unverified here) | 1.0 shipped Oct 2025, very active |
| Google ADK | Runner + event-sourced session | Yes | Adapter (LiteLLM) | Yes, `Event` stream from Runner | Partial: `ResumabilityConfig` exists, semantics undocumented in sources found | Partial (unverified) | Yes (unverified in sources found) | ADK 2.6.0, multi-language |
| smolagents | ReAct, code or JSON actions | Yes | Yes, `AmazonBedrockModel` | Partial: `step_callbacks`, `agent.replay()` | No formal API, but manual step-by-step + memory transplant | No built-in | Yes (MCP tools with output_schema referenced) | Active, HF-maintained |
| CrewAI | Role-based crews | Yes | Adapter (LiteLLM `bedrock/`) | Partial | Partial, task outputs only (unverified) | Custom callback only (unverified) | Yes, `mcps` field + `MCPServerAdapter` | Very high install base, active |
| AutoGen | Event-driven conversation | Yes | Adapter | Yes, message log | Replay via message log | Yes, `UserProxyAgent` | Yes | Maintenance mode (corroborated, unverified vs primary) |
| AG2 | Network hub + channels (v1.0) | Yes | Adapter | Yes, write-ahead log | Unverified | Yes | Yes | 4.9k stars, just broke API at 1.0 |
| Pydantic AI | Typed agent run | Yes | Yes, Converse + Mantle, best documented | Yes, event stream handler | Yes, via durable execution (Temporal/DBOS/Prefect/Restate), replay not snapshot | Yes, via workflow signals | Yes, local default + native opt-in | Active, co-maintained integrations |

## 4. Genuinely common versus not common

### 4.1 Genuinely common (safe to put in the interface)

**A task entry point that takes a prompt or task and returns or streams results.**
Every harness has exactly one of these: `query()`, `Runner.run()`, `agent()`, `graph.invoke()`, `runner.run_async()`, `agent.run()`, `crew.kickoff()`.

**Tool definition as name + description + JSON Schema + handler.**
This is the most universal abstraction in the entire survey.
Claude Agent SDK's `@tool` takes exactly name, description, schema, handler; smolagents bakes name/description/type-hints into the system prompt; OpenAI wraps Python functions into `FunctionTool`; Strands takes a `tool_spec`.
The tool-call and tool-result message pair is likewise universal.

**An ordered stream of events or items produced during the run.**
Every harness exposes one, and every one is pull-based: you iterate an async generator or stream.
None of them offer a push-style `emit_event` callback you can register as the primary output channel, so the AgentLab interface should model this as "the harness yields events" and not as "the harness calls your emit_event".

**Terminal stop with a stop reason, plus a turn/step limit.**
All have both.
But note the vocabularies do not align: Strands uses `end_turn`, `max_tokens`, `tool_use`, `checkpoint`, `limit_turns`, `interrupt`, `cancelled`; OpenAI raises `MaxTurnsExceeded`; smolagents returns state `success` or `max_steps_error`.
AgentLab must define its own stop-reason enum and map into it, accepting that the mapping is lossy.

**Token usage and cost accounting.**
Present in most, but reported at different granularities and not always populated.
Better sourced from the model proxy than from the harness (see section 5.3).

### 4.2 Not common (must be optional capabilities)

**`checkpoint` and `resume`. This is the headline finding.**
The four semantics in the field are mutually incompatible:

- LangGraph: full graph state snapshot at every super-step, with replay and time travel from any prior checkpoint.
- OpenAI Agents SDK: `RunState` serialized only when the run pauses on a tool approval; not an arbitrary mid-run snapshot.
- Strands: an opaque cycle-boundary marker that explicitly excludes conversation state, only emitted on tool_use cycles, gated behind `checkpointing=True` and marked experimental.
- Claude Agent SDK: session ID resume that restores the conversation but explicitly not the filesystem.
- smolagents, CrewAI: no checkpoint API at all.
- Pydantic AI: durability by workflow replay, which is a different mechanism entirely and requires an external durable runtime.

A single `checkpoint()` method in the AgentLab interface would mean four different things depending on the backend, which is worse than not having it.

**`request_human`.**
The common denominator is narrow: pausing to approve or reject a specific tool call.
OpenAI, LangGraph, Strands, and the Claude Agent SDK all support that shape.
The general form, "the agent asks a human an arbitrary question mid-run and waits", is not uniformly supported, and smolagents has no built-in HITL at all.
Model this as `approve_tool_call` capability, not `request_human`.

**State model.**
Four incompatible shapes: a message list (Claude SDK, OpenAI, Strands), a typed graph state with channels (LangGraph), an event-sourced session with `state_delta` (ADK), and a step list (smolagents).
There is no honest generic get/set state operation.

**Planning.**
smolagents has explicit `PlanningStep` and `planning_interval`; the Claude Agent SDK has plan mode and TodoWrite; others have nothing comparable.
Not common, do not put it in the interface.

**Multi-agent topology.**
Handoffs, agents-as-tools, graphs, swarms, crews, networks, subagents.
Completely divergent. Leave it entirely inside the harness.

**Streaming granularity.**
Token-level deltas, item-level events, and turn-level results are all "streaming" in different docs.
Normalize to the coarsest level that all support, which is item/turn.

## 5. Recommendation

### 5.1 First two harnesses: Claude Agent SDK and AWS Strands Agents

Scored against the stated criteria:

| Criterion | Claude Agent SDK | Strands Agents |
|---|---|---|
| Headless container operation | `-p` plus `--bare`, AWS reference Dockerfiles exist | Plain Python/TS library, no TTY assumptions |
| Bedrock support | Native, one env var, no adapter | Native, and the default provider |
| Event / trace observability | Message iterator, hooks, `--output-format json`, `include_partial_messages` | Typed hookable events, both streamable and subscribable, OTel spans in the loop |
| Checkpoint / resume | Session resume and fork by ID; file checkpointing separate | SessionManager (File/S3) plus experimental cycle checkpoints; TS immutable snapshots |
| Active maintenance | Anthropic first-party, bundles the CLI, 7.9k stars | AWS first-party, Apache 2.0, 6.7k stars, repo renamed to `harness-sdk` |
| Different enough to compare | Opaque, opinionated, out-of-process | Transparent, minimal, in-process |

The scientific argument is the deciding one.
These two are not two flavours of the same ReAct loop.
The Claude Agent SDK is a fully opinionated harness: it brings its own tool set, its own context compaction, its own subagents, its own permission model, and it runs as a subprocess you cannot instrument from inside.
Strands is a thin transparent event loop where you supply the model, the tools, and the state manager, and every lifecycle boundary is a typed hook you can subscribe to.
Comparing them answers the question AgentLab exists to answer: how much of agent performance comes from the harness's accumulated scaffolding versus from the model plus a minimal loop.

Both also remove the biggest confound available.
Neither needs LiteLLM to reach Bedrock, so a Claude-SDK-versus-Strands comparison on the same Bedrock model is a comparison of harnesses, not of adapter layers.
Every other strong candidate (OpenAI Agents SDK, LangGraph, ADK, CrewAI) introduces an adapter between the harness and Bedrock, and the CrewAI issue in section 2.7 shows that adapter is where tool-schema translation actually breaks.

**Why not LangGraph first**, despite having the best checkpointing.
LangGraph is a framework for authoring a harness, not a harness.
If AgentLab integrates LangGraph, the thing under test is the graph an AgentLab engineer wrote, so "LangGraph versus Claude Agent SDK" is really "our graph versus Anthropic's harness".
That is a fine experiment later, but it is not a harness comparison, and it makes AgentLab responsible for the quality of one side of every result.
LangGraph should be integration three, specifically to enable the checkpoint-and-branch experiments the other two cannot support.

**Why not OpenAI Agents SDK first**, despite the best serializable run state.
Bedrock only via LiteLLM, and `RunState` snapshots only at approval boundaries rather than arbitrary points.
It should be integration four, and it is the right harness to validate the optional `serialize_state` capability against.

### 5.2 Proposed minimal interface

Only the required half is backed by the survey. Everything else is negotiated, not assumed.

```python
# Required. Every surveyed harness supports all of this.

@dataclass(frozen=True)
class HarnessCapabilities:
    resume_conversation: bool      # re-enter with prior context
    snapshot_state: bool           # serializable state, harness-defined opacity
    snapshot_granularity: Literal["none", "pause_only", "step_boundary", "super_step"]
    approve_tool_calls: bool       # pause on a specific tool call
    mcp_client: bool               # can consume MCP servers
    mcp_transports: frozenset[str] # {"stdio", "http", "sse", "in_process"}
    native_bedrock: bool           # no adapter layer between harness and Bedrock
    cost_reporting: bool

class AgentHarness(Protocol):
    name: str
    version: str

    def capabilities(self) -> HarnessCapabilities:
        """Static declaration. AgentLab skips experiment arms a harness cannot serve."""

    async def start(
        self,
        task: Task,                 # prompt + task metadata
        environment: Environment,   # container handle, workdir, MCP endpoints, model endpoint
        config: RunConfig,          # model id, max_turns, max_tokens, budget, timeout, seed
    ) -> RunHandle:
        """Begin a run. Returns immediately with a handle; does not block to completion."""

    def events(self, run: RunHandle) -> AsyncIterator[Event]:
        """Pull-based, ordered, at-least-once. The ONLY guaranteed observability channel.
        Event is a normalized envelope: {seq, ts, kind, payload, raw}.
        `kind` is one of a closed AgentLab set; `raw` preserves the harness-native event
        so nothing is lost to normalization."""

    async def stop(self, run: RunHandle, *, grace_period_s: float = 5.0) -> StopReason:
        """Cooperative cancel, then hard kill. Must be safe to call on a finished run."""

    async def result(self, run: RunHandle) -> RunResult:
        """Terminal outcome: stop_reason, final message, usage, error.
        NOT the graded output. See section 5.3."""

# Optional. Guarded by capabilities(). Callers MUST check before calling.

class ResumableHarness(Protocol):
    async def snapshot(self, run: RunHandle) -> bytes:
        """Only valid where snapshot_granularity != 'none'. Opaque to AgentLab.
        The harness declares what it does and does not contain."""

    async def resume(
        self, snapshot: bytes, environment: Environment, config: RunConfig
    ) -> RunHandle:
        """Environment is passed again because NO surveyed harness snapshots world state."""

class ApprovingHarness(Protocol):
    def pending_approvals(self, run: RunHandle) -> Sequence[ToolCall]:
        ...

    async def decide(self, run: RunHandle, call_id: str, decision: Decision) -> None:
        ...
```

Three deliberate choices in that sketch:

`emit_event` from the original proposal is inverted into `events()`.
No surveyed harness pushes events to a registered callback as its primary channel; every one yields them. Modelling it as push would mean writing a pump for every integration.

`checkpoint`/`resume` moved out of the base protocol into `ResumableHarness`, and `resume` takes an `Environment` again.
This is forced by the evidence: the Claude SDK resumes conversation but not filesystem, and Strands checkpoints exclude conversation state entirely.
Pretending a snapshot is self-contained would produce silently wrong experiment results.

`request_human` narrowed to `ApprovingHarness.decide`, because tool-call approval is the only human-in-the-loop shape with broad support.

### 5.3 The most important recommendation: instrument the environment, not the harness

Every proven harness-agnostic evaluation system in the survey converges on the same pattern, and it is not "define a rich agent interface".

Inspect's `agent_bridge()` intercepts at the model API.
In-process, agents call their normal SDK with `model="inspect"` and the bridge routes to Inspect's provider.
For containerized agents, `sandbox_agent_bridge()` runs a proxy inside the container on localhost:13131 that serves the OpenAI, Anthropic, and Google APIs, so the agent is redirected purely by setting `OPENAI_BASE_URL` and friends, and it works for agents written in any language.
Source: https://inspect.aisi.org.uk/agent-bridge.html

Claw-SWE-Bench treats the harness as a controlled experimental variable behind a five-method adapter (`create_agent`, `send_task`, `backup_session`, `delete_agent`, `get_docker_args`), with the benchmark layer owning container startup, repository reset, prompt instantiation, patch collection, prediction writing, and evaluation.
Two findings from it are directly load-bearing for AgentLab.
First, candidate outputs are collected from repository state rather than parsed from the agent's final message, which makes the output contract independent of whether the harness emits JSON, prose, or nothing.
Second, harness choice changed Pass@1 by 27.4 points while model choice changed it by 29.4 points, and an adapter that asked the model to emit a diff directly scored 19.1 percent versus 73.4 percent for one that let it edit files and exported the patch from git.
Source: https://arxiv.org/html/2606.12344v1

tau-bench's agent interface is a single method, `agent.solve(env, task_index) -> SolveResult`, with the loop entirely inside the agent and the environment owning tool execution and reward.
Sources: https://github.com/sierra-research/tau-bench/blob/main/tau_bench/run.py and https://github.com/sierra-research/tau-bench/blob/main/tau_bench/agents/tool_calling_agent.py

SWE-bench itself moved to a fully containerized Docker evaluation harness for reproducibility and scores a patch, not a transcript.
Source: https://github.com/swe-bench/SWE-bench

The Exgentic / Unified Protocol work makes the failure mode explicit: harnesses that force a single integration interface end up "effectively evaluating a diminished version of the agent".
Source: https://arxiv.org/html/2602.22953v2

Concretely, for AgentLab on ECS/Fargate with Bedrock:

1. **Put a recording proxy in front of Bedrock and point every harness at it.**
   The Claude Agent SDK honours `ANTHROPIC_BEDROCK_BASE_URL`, Strands accepts a custom boto3 session or endpoint, and Pydantic AI accepts a `base_url` on its Bedrock provider.
   This gives uniform token counts, cost, latency, full request/response capture, and deterministic replay for free, across harnesses that agree on nothing else.
   It is the only observability channel whose fidelity does not depend on how good a given harness's event stream is.

2. **Grade from environment state, not from the agent's final message.**
   Diff the container filesystem, query the task database, run the test suite.
   This is what makes an opaque harness like the Claude Agent SDK comparable to a transparent one like Strands at all.

3. **Serve tools from an AgentLab-controlled MCP server inside the container.**
   Then tool use is observable and identical across harnesses without depending on each one's tool-call events.

Under this design the harness interface stays small on purpose.
`start`, `events`, `stop`, `result`, plus declared capabilities is enough, because the heavy observability is happening at the proxy and the container, where it is harness-independent by construction.

### 5.4 MCP as the tool layer: safe, with one timing caveat

Standardizing on MCP is the right call.
Every harness in the survey can consume MCP servers: Claude Agent SDK (stdio, HTTP/SSE, in-process), OpenAI Agents SDK (`mcp_servers` and `HostedMCPTool`), Strands, CrewAI (`mcps` and `MCPServerAdapter`), Pydantic AI (local by default, native opt-in), smolagents (MCP tools with `output_schema`), and ADK.

The caveat is timing.
The current revision is `2026-07-28`, released as stable on that date, and it is described by the maintainers as the largest revision since launch.
Sources: https://modelcontextprotocol.io/docs/2026-07-28/learn/versioning and https://blog.modelcontextprotocol.io/posts/2026-07-28/

Breaking and near-breaking changes that affect AgentLab:

- The `initialize`/`initialized` handshake and the `Mcp-Session-Id` header are retired; each request now carries protocol version, client identity, and capabilities in `_meta`, with an optional `server/discover` RPC. This is what makes plain round-robin load balancing work without shared storage, which is good for Fargate.
- Streamable HTTP requests must now include `Mcp-Method` and `Mcp-Name` headers.
- Multi Round-Trip Requests (MRTR) replace server-initiated `elicitation/create`, `sampling/createMessage`, and `roots/list`.
- Roots, Sampling, and Logging are deprecated with a twelve-month runway; new implementations should not adopt them.
- The legacy HTTP+SSE transport is deprecated.
- Tasks moved out of experimental core into an `io.modelcontextprotocol/tasks` extension.

Sources: https://blog.modelcontextprotocol.io/posts/2026-07-28/ and https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2026-07-28/schema.ts

Practical guidance: build AgentLab's tool servers against `2026-07-28`, but expect harness MCP clients to lag it by months, and keep the servers backward compatible with `2025-11-25` handshake-based clients, which the spec provides an explicit compatibility path for.
Do not build anything on sampling, roots, or elicitation.
And do not assume MCP tool schemas survive translation to every provider's tool format: the CrewAI + Bedrock Claude `input_schema` validation failure in section 2.7 is exactly that bug.

## 6. Open questions

1. Does the Claude Agent SDK's session resume work across hosts and containers, given the transcript is written to a local directory and `session_store` is a configurable option?
   AgentLab needs this for Fargate task replacement. The sessions doc has a "resuming sessions across hosts" section that was not fully retrieved.

2. What are Google ADK's actual `ResumabilityConfig` semantics?
   The config exists in `runners.py` but no user-facing documentation was found in this survey. Unverified.

3. Can the OpenAI Agents SDK reach Bedrock Converse through LiteLLM with tool calling and prompt caching intact, and at what fidelity cost?
   Needs an empirical test before scoring it as a Bedrock-capable harness.

4. Does the Claude Agent SDK's subprocess architecture allow reliable mid-run snapshotting at all, or is the only pause point the end of a `query()` call?
   This determines whether it can ever satisfy `ResumableHarness`.

5. How stable is Strands' experimental checkpoint API?
   It is in `strands.experimental`, it rejects mismatched `schema_version` on resume, and it only fires on tool_use cycles. Building an AgentLab capability on it risks breakage.

6. Which MCP spec revision does each harness's MCP client actually implement today?
   None of the harness docs surveyed state a protocol version. This is a direct compatibility risk given the `2026-07-28` breaking changes and should be tested per harness, not assumed.

7. Does the recording-proxy approach interfere with Bedrock prompt caching?
   Pydantic AI documents Bedrock's 4-cache-point limit and Claude Code has provider-specific caching behaviour. A proxy that rewrites requests could silently destroy cache hits and inflate cost, which would corrupt cost comparisons between harnesses.

8. Is `--bare` sufficient to make Claude Agent SDK runs hermetic?
   It skips hooks, skills, plugins, MCP servers, auto memory, and CLAUDE.md, but it also disables OAuth and keychain reads. Bedrock credentials are documented to still resolve normally, which is what AgentLab needs, but this should be verified in the actual container image.
