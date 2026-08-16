# Agent improvement literature: what AgentLab should test first

Survey date: 2026-08-16.
Scope: self-improvement, reflection, memory, planning, context management, tool learning, and self-improving loops, 2023-2026, weighted to the recent end.

Provenance note.
Sources dated 2026 are cited as retrieved from the web on 2026-08-16 via Exa search and fetch.
For arXiv preprints I read the abstract and the retrieved body excerpts, not the full PDFs, so numbers below are as reported by the authors and are not independently reproduced.
Where I could not ground a number in a retrieved source I write "unverified".
One caution: the arXiv listing page for 2503.13657 returned unrelated content when searched, so I cite the NeurIPS proceedings version of that paper instead.

---

## 1. Summary

The single strongest first experiment is context compaction quality, because the reported effects are large (6.5 to 18 percentage points), the implementation is a prompt and a trigger rather than a system, and the mechanism has a cheap high-power proxy metric (information retained across the compaction boundary).
The second strongest is a cost-matched test of reflection, because the original Reflexion claims (+22pp AlfWorld, +11pp HumanEval) have been directly contradicted by 2026 replications showing reflection loses to spending the same tokens on another attempt, and nobody has run that comparison on a frontier model on agentic tasks.
Memory is the most oversold area: headline claims are +51% relative on WebArena (AWM) and +8.3pp (ReasoningBank), but EvoMemBench finds long-context baselines competitive and several memory methods falling below the no-memory baseline at 128K context.
Tool-set scaling has the largest raw effect sizes in the whole survey (tool-selection accuracy 13.6% to 43.1%) but the effect is model-dependent and may have already closed for frontier models.
Subagents have the biggest production claim (+90.2% on Anthropic's internal research eval) and the biggest confound: token usage alone explained 80% of performance variance in that same analysis.
Prompt and context optimizers (GEPA, ACE) show +10 to +14pp but need hundreds to thousands of rollouts, which does not fit a $1,000 budget as a first experiment.
Budget reality: with 20 to 50 paired trials, AgentLab can reliably detect binary success effects of roughly 20 to 30 percentage points, not 5.
Therefore the first experiments should either target very large effects or use continuous secondary metrics (tokens, steps, information recall) where 20 to 50 trials is genuinely enough.
Self-improving loops (DGM, AlphaEvolve) are scientifically the most interesting and financially out of reach: one 80-iteration DGM run reportedly cost about $22,000.
The documented failure modes of those loops (objective hacking, judge gaming, memory reward inflation) are cheap to test and should shape AgentLab's own promotion gate design.

---

## 2. Technique-by-technique findings

### 2.1 Reflection and self-critique

**The original claims.**
Reflexion reports absolute improvements of 22 points on AlfWorld over 12 iterative learning steps, 20 points on HotPotQA, and up to 11 points on HumanEval pass@1 (91.0% versus 80.1% for GPT-4).
Source: https://arxiv.org/abs/2303.11366 and https://proceedings.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf

The same paper's appendix reports a clean negative: on WebShop, ReAct + Reflexion fails to significantly outperform ReAct across 100 shopping requests, and the authors terminate after four trials because the agent generates generic, unhelpful reflections and gets stuck in local minima.
Source: https://proceedings.neurips.cc/paper_files/paper/2023/file/1b44b878bb782e6954cd888628510e90-Paper-Conference.pdf

**The skeptical literature, 2023-2024.**
Huang et al. define intrinsic self-correction (no external feedback, no oracle labels) and find that accuracy drops across all models and all benchmarks tested after self-correction, and that the apparent gains in Reflexion and RCI come from using oracle correctness labels that are unavailable in deployment.
Source: https://arxiv.org/abs/2310.01798

Kamoi et al.'s TACL critical survey is the most damning single source.
It concludes that no prior work demonstrates successful self-correction with feedback from prompted LLMs under fair settings in general tasks, that self-correction works well only where reliable external feedback exists, and that large-scale fine-tuning enables it.
It explicitly names Reflexion as using exact match against ground truth answers to generate its feedback signal.
Source: https://aclanthology.org/2024.tacl-1.78/

Tyen et al. locate the bottleneck precisely: LLMs cannot find reasoning errors, but can correct them reliably when given the error location.
Source: https://aclanthology.org/2024.findings-acl.826/

A follow-up interpretability study identifies three mechanisms for self-correction failure (answer wavering, prompt bias, and human-like cognitive bias) and reports that self-correction makes Llama change its internal answer 14.1% of the time versus 8.3% during initial generation.
Source: https://arxiv.org/html/2412.14959v1

**The 2026 replications, which are the most decision-relevant sources here.**
"Sample More, Reflect Less" runs seven methods on open models at 1.5B, 3B and 7B on two math benchmarks with 150 questions each, counting every token including critique and reflection tokens, and comparing each method against repeated sampling at that method's own measured cost.
Self-Refine and forced Reflexion remain significantly below the equal-cost baseline at 7B, by 3.6 to 10.1 points.
Every method in which the model assesses its own output falls below the cost-matched baseline in all 18 of its comparisons.
It also documents a measurement trap directly relevant to AgentLab: Reflexion as specified never triggered its own retry on the smallest model, silently collapsing into a single chain of thought while still being reported as "Reflexion".
Source: https://arxiv.org/abs/2607.28576v1

"Try Again, Don't Look Back" is a placebo-controlled design on MBPP+ at 1.5B, 3B and 7B with four matched-budget retry conditions: blind resampling, a content-free failure notice, genuine execution feedback, and feedback plus verbal reflection.
Blind resampling is the strongest condition below 7B and statistically tied at 7B while consuming 2.5 to 5.5 times fewer tokens.
The informational content of execution feedback adds nothing measurable over the placebo.
The proposed mechanism is anchoring: shown its previous attempt, the model reproduces a near-identical program in 33 to 68% of retries versus 2 to 14% under blind resampling.
Retrieved solutions to other tasks change nothing (bounded to plus or minus 3.5 points), which localizes the harm to self-conditioning rather than to context length.
Source: https://arxiv.org/html/2607.26117

**Where reflection does work: external, grounded feedback.**
"Structured Feedback Improves Repair in an LLM Agent Loop" finds that validator feedback containing failure location, observed value, and admissible alternatives improves TextWorld terminal success by 44 percentage points for Qwen2.5-Coder-14B and 42 points for Llama-3.1-8B, and that the admissible alternatives carry almost all of the gain (location plus observed value alone stays near the raw-diagnostic baseline).
It also finds no advantage for keyed JSON over prose containing the same repair values.
Source: https://arxiv.org/html/2607.14167v1

RETRACE gets +7.0pp (GPT-5-mini) and +3.6pp (MiniMax-2.5) on SWE-bench Verified by having an independent verifier reconstruct the problem from the patch without seeing the original issue, and beats Self-Refine at comparable added compute.
Source: https://arxiv.org/html/2608.08950v1

A related study finds that 46.0% of positive validation events produced by a repair agent mid-trajectory carry no bug-discriminating information, and that 23.8% of baseline rollouts close on that evidence alone.
Feeding back a counterfactual replay reduces evidence-inadequate closure by 7.8pp but does not detectably change repair success.
Source: https://arxiv.org/abs/2607.28871v1

**Reading for AgentLab.**
The claim "reflection helps" is not what the literature supports.
What it supports is "grounded external feedback with actionable alternatives helps, and self-generated critique is at best a wash against spending the same tokens elsewhere."
Nobody in the retrieved literature has run the cost-matched comparison on a frontier model on long-horizon agentic tasks, which is exactly the gap AgentLab can fill cheaply.

### 2.2 Memory and experience reuse

**Skill libraries.**
Voyager builds an ever-growing skill library of executable code indexed by description embeddings, obtaining 3.3x more unique items, 2.3x longer distances travelled, and tech-tree milestones up to 15.3x faster than prior SOTA in Minecraft, and generalizes the learned library to a fresh world.
Note that ReAct and Reflexion barely progress on the same task, so the comparison is partly about the automatic curriculum, not memory alone.
Source: https://arxiv.org/html/2305.16291v2 and https://voyager.minedojo.org/

**Workflow memory.**
Agent Workflow Memory induces reusable routines from trajectories, offline from training examples or online from test queries.
Reported: +51.1% relative success rate on WebArena (12.0 absolute points over the BrowserGym baseline, 35.6% absolute), +24.6% relative step-wise success on Mind2Web, and 8.9 to 14.0 absolute points in cross-website and cross-domain generalization.
Source: https://arxiv.org/abs/2409.07429 and https://github.com/zorazrw/agent-workflow-memory

Contextual Experience Replay reports 31.9% on VisualWebArena (surpassing tree search at much lower token cost) and 36.7% on WebArena, a 51.0% relative improvement over a GPT-4o baseline.
Source: https://aclanthology.org/2025.acl-long.694/

**Reasoning memory.**
ReasoningBank distills strategies from both successes and failures, self-judged by an LLM without ground truth.
Reported on Gemini-2.5-Flash: +8.3% on WebArena and +4.6% on SWE-Bench-Verified over a memory-free baseline, nearly 3 fewer execution steps per task on SWE-Bench-Verified, and a further ~3% success gain from memory-aware test-time scaling at k=5.
The authors state that the self-judgement does not need to be accurate and that the method is robust to judgement noise.
Source: https://arxiv.org/abs/2509.25140 and https://research.google/blog/reasoningbank-enabling-agents-to-learn-from-experience/

**The counter-evidence, which is strong and recent.**
EvoMemBench compares 15 memory methods against long-context baselines under a standardized protocol across in-episode and cross-episode, knowledge-oriented and execution-oriented settings.
Findings that matter:
Long-context baselines remain highly competitive and Gemini-3-Flash with raw context takes the best rank on both retention and revision.
Memory gains are largest when context is constrained (+14.5 points at 16K, +14.0 at 32K, +7.8 at 64K, +8.5 at 128K), and at 128K several memory methods fall below the no-memory baseline.
Compression is risky for execution tasks: MemAgent and MemoBrain score 26.0 and 25.0 at 16K against a 28.5 no-memory baseline.
No single memory form works across settings; the best family changes by domain.
Source: https://arxiv.org/html/2605.18421

"Memory Reward Inflation in Self-Improving LLM Agents" identifies the Echo Gap: without ground-truth labels the stored score is an LLM assessment, incorrect episodes receive inflated rewards, and the agent preferentially reuses the mistakes it is most confident in.
The error compounds through memory rather than averaging out because the confirming judge's errors stay correlated with the original self-grading bias.
This is a direct challenge to ReasoningBank's robustness-to-noise claim and is the single most important thing to test if AgentLab runs a memory experiment.
Source: https://arxiv.org/html/2608.00017

**Security.**
Memory poisoning reports average attack success 50.46% and retrieval success 41.05% across two agents, with attack success scaling with how aggressively the agent reads and writes memory, and existing prompt injection defenses providing incomplete coverage.
Source: https://arxiv.org/html/2606.04329v2
A related attack biases tool selection with three injected memory records at up to 85.9% success.
Source: https://arxiv.org/html/2605.26154

**What production ships.**
Anthropic's cookbook distinguishes three shipped primitives: compaction (whole-transcript summarize and continue), tool-result clearing (drop stale re-fetchable payloads, keep the tool_use record), and memory (agent-driven file-backed notes outside the window).
It states Claude Code uses compaction plus two complementary memory systems for cross-session persistence, and that all three have first-party API support.
Source: https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools

### 2.3 Planning

**Tree search over agent actions.**
LATS integrates MCTS with LM value functions and self-reflection.
Reported with GPT-3.5: HotPotQA exact match 0.71 versus 0.32 for ReAct and 0.51 for Reflexion, HumanEval pass@1 83.8 versus 56.9 for ReAct, WebShop score 75.9 versus 53.8 for ReAct and 64.2 for Reflexion.
With GPT-4 it reports 92.7% pass@1 on HumanEval (94.4% in the OpenReview version, a discrepancy across versions of the same paper).
On cost, LATS reports the same asymptotic sample complexity as other tree methods and fewer total tokens than ToT and RAP on HotPotQA (173,290 versus 210,215 and 176,500), but the comparison baseline is n=5, k=50, which is a large per-task budget.
Source: https://arxiv.org/abs/2310.04406 and http://lapisrocks.github.io/LanguageAgentTreeSearch/

**The best controlled comparison of paradigms.**
"Select-then-Solve" is the most useful single planning source for AgentLab because it holds model, prompts, tools and evaluation code fixed and varies only the inference paradigm across six paradigms, four frontier LLMs and ten benchmarks, about 18,000 runs.
Headline findings: reasoning structure helps dramatically on some tasks and hurts on others, with ReAct beating Direct by 44pp on GAIA and CoT losing 15pp to Direct on HumanEval.
No single paradigm dominates, and an oracle per-task selector beats the best fixed paradigm by 17.1pp on average.
A learned embedding router recovers 26% of the oracle gap on average, lifting average accuracy from 47.6% (Direct) to 53.1% versus 50.3% for the best fixed paradigm.
Zero-shot self-routing works only for GPT-5 and drops weaker models below their Direct baseline.
Token cost tiers are directly usable for budgeting: Direct and CoT 1.0-1.1x, ReCode 2.8x, ReAct 4.0x, Plan-Execute 6.9x, Reflection 9.4x.
Reflection costs 9.4x more than Direct on HLE for a 2pp gain, while ReAct's 4x overhead buys 44pp on GAIA.
Source: https://arxiv.org/html/2604.06753

**Plan-then-execute positive results.**
Pre-Act reports Pre-Act beating ReAct by 70% in action recall averaged over five models on the Almita dataset, and a fine-tuned Llama 3.1 70B beating GPT-4 by 69.5% in action accuracy and 28% in goal completion.
Caveat: gains are reported as relative percentages on turn-level metrics, and end-to-end goal completion is judged by GPT-4 as LLM-as-judge, which is a weaker signal than execution-verified success.
Source: https://arxiv.org/html/2505.09970v2

OAgents is an empirical ablation study of planning, tool use, memory and test-time scaling within one framework, and reports that stable sub-agent environment interactions provide greater task success than complex orchestration algorithms.
Source: https://aclanthology.org/2025.findings-emnlp.720.pdf

**Reading for AgentLab.**
The evidence does not support a single best planning paradigm.
It supports paradigm choice being a per-task decision with very large swings, which makes routing (not planning) the interesting experiment, and it says explicit planning is the second most expensive paradigm after reflection.

### 2.4 Context management

**Long context degrades even on trivial tasks.**
Chroma's context rot study holds task complexity constant and varies only input length across 18 models.
Performance consistently degrades with input length; lower needle-question similarity degrades faster; distractors hurt more as input grows; and on LongMemEval, models perform better on a ~300 token condensed version than on the ~120K token full version of the same question.
Source: https://research.trychroma.com/context-rot and https://github.com/chroma-core/context-rot

**Production practice.**
Anthropic frames context engineering as finding the smallest set of high-signal tokens, and names three long-horizon techniques: compaction, structured note-taking, and sub-agent architectures.
It describes subagents returning condensed summaries of often 1,000 to 2,000 tokens after spending tens of thousands internally.
Source: https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents

Claude Code's guidance names context rot explicitly, notes that with a 1M window the model is at its least intelligent point when it auto-compacts (because context is longest), and recommends proactive compaction, rewind, and subagents for output you only need the conclusion from.
Source: https://claude.com/blog/using-claude-code-session-management-and-1m-context

**Compaction quality is measurable and large.**
CompactionRL fixes the execution agent (GLM-4.7-Flash) and varies only the summary agent: the best summarizer raises SWE-bench Verified pass@1 from 49.0 to 55.5, a 6.5 absolute point gain, while also triggering slightly fewer compactions per trace.
The authors' conclusion is that compaction is a performance-critical decision process, not passive preprocessing.
Source: https://doi.org/10.48550/arxiv.2607.05378

Slipstream validates candidate summaries against the agent's independently continued reasoning and reports up to +8.8pp task accuracy on SWE-bench Verified and BrowseComp with up to 39.7% lower end-to-end latency.
Source: https://arxiv.org/html/2605.08580v1

SelfCompact lets the model decide when to compact, using a compaction tool plus a rubric for when to fire and when to suppress, and reports matching or exceeding fixed-interval summarization at a fraction of the token cost: up to +18.1 points on math and 5 to 9 points on agentic search over a no-summarization baseline at 30 to 70% lower per-question cost.
It reports that the tool alone is unevenly used and the rubric alone cannot act, so both are needed.
Source: https://arxiv.org/abs/2606.23525v1
AutoCompact makes the same adaptive-trigger argument.
Source: https://autocompact.github.io/

ACON optimizes compression guidelines in natural language from paired trajectories where full context succeeds and compressed context fails, reporting 26 to 54% peak token reduction while largely preserving performance and up to 46% improvement for smaller LMs as long-horizon agents.
Source: https://www.microsoft.com/en-us/research/publication/acon-optimizing-context-compression-for-long-horizon-llm-agents/

TRACE reports that recurrent compression weakens the influence of recent interactions, increasing blocked actions, repeated exploration, and cross-run instability, and proposes evaluating each compaction boundary rather than inferring quality from terminal outcomes.
Source: https://arxiv.org/html/2608.06503v1

An independent measurement harness reports baseline recall of 73% dropping to 40% after 50% compaction and 7% after 98% compaction, with repeatability of 2.6pp or better across three runs.
The repeatability figure matters: it means information-recall is a low-noise metric.
Source: https://github.com/profff/lost-in-compaction

A production study on Dynamics 365 expense itemization reports full-context 71.0% at 1,480,996 tokens, pruning to the last 5 tool call pairs 79.0% at 535,274 tokens (a 63.9% token reduction), and pruning plus summarization 91.6%, averaged over 5 runs on 50 tasks with confidence intervals.
Source: https://arxiv.org/html/2606.10209

A small independent harness reports SWE-bench Verified 314/500 (62.8%) at 200 steps without compaction versus 320/500 (64.0%) with 100-step compaction, and identical 7/23 results on SWE-bench Lite dev for unlimited budget versus 32K auto-compaction.
Treat these as single-run numbers well inside the noise band described in section 2.7.
Source: https://github.com/SeungyounShin/agent-verify

**Subagents and multi-context orchestration.**
Anthropic reports a multi-agent system with Claude Opus 4 lead and Claude Sonnet 4 subagents outperforming single-agent Claude Opus 4 by 90.2% on its internal research eval.
The same post reports that three factors explained 95% of performance variance on BrowseComp, with token usage alone explaining 80%, and that multi-agent systems use about 15x more tokens than chats.
That is a self-reported confound: the architecture buys performance substantially by buying tokens.
Source: https://www.anthropic.com/engineering/multi-agent-research-system

Cognition's 2025 position was that parallel multi-agent systems are fragile because actions carry implicit decisions and context cannot be shared thoroughly enough, and that single-threaded linear agents with a compression model are the default.
Source: https://cognition.ai/blog/dont-build-multi-agents

Cognition's 2026 update narrows rather than reverses this: multi-agent works when writes stay single-threaded and additional agents contribute intelligence rather than actions, the practical shape is map-reduce-and-manage, and unstructured swarms remain a distraction.
Source: https://cognition.ai/blog/multi-agents-working/

MAST analyzes over 1,600 annotated traces across 7 MAS frameworks with inter-annotator agreement of 0.88, producing 14 failure modes in 3 categories, and states that MAS performance gains on popular benchmarks are often minimal versus single-agent frameworks or simple best-of-N baselines.
The largest individual failure modes are step repetition (17.14%), reasoning-action mismatch (13.98%), and proceeding on wrong assumptions (11.65%).
Its central claim is that most failures come from system design, not model capability, so better base models will not fix them.
Source: https://proceedings.neurips.cc/paper_files/paper/2025/file/b1041e52d3be19f0a9bc491657488e4a-Paper-Datasets_and_Benchmarks_Track.pdf and https://arxiv.org/html/2503.13657v2

Anthropic's own subagent guidance lists concrete anti-cases: sequential dependent work, same-file edits, small tasks, and too many specialist agents (which makes automatic delegation less reliable).
Source: https://claude.com/blog/subagents-in-claude-code

### 2.5 Tool learning and selection

**Retrieval does not solve tool scale by itself.**
ToolRet builds a 7.6k-task, 43k-tool retrieval benchmark and finds that IR models with strong conventional benchmark performance do poorly on tool retrieval, and that low retrieval quality degrades downstream task pass rate.
Source: https://aclanthology.org/2025.findings-acl.1258/ and https://arxiv.org/abs/2503.01763

**But it produces some of the biggest effect sizes in the survey.**
RAG-MCP reports tool selection accuracy of 43.13% versus 13.62% for a blank-conditioning baseline and 18.20% for actual-match, with average prompt tokens cut from 2,133 to 1,084, on the web-search subset of MCPBench with 20 trials per baseline.
It also reports that retrieval precision itself degrades past roughly 100 candidate tools.
Source: https://arxiv.org/html/2505.03275

MCP-Zero has the cleanest scaling result: with standard schema injection, Claude-3.5 single-turn APIBank accuracy falls from 97.60 on a curated domain subset to 69.23 on the full 2,797-tool pool (100.00 to 60.22 multi-turn), while active on-demand tool discovery holds 95.19 and 90.32 at 111 to 159 average tokens versus 6,308 to 6,402, a 98% token reduction.
Critically, GPT-4.1 showed no improvement because its baseline was already strong, which is the key warning for frontier-model experiments.
Source: https://arxiv.org/html/2506.01056v3

MCPVerse mounts 552 real tools (about 147k tokens of schemas) and finds most models degrade as the tool set grows, with GPT-4o dropping 24.49 points and Kimi-K2 39.50 points from Oracle to Standard mode, while Claude-4-Sonnet *improved* from 57.77 to 61.01 (+5.61% relative), with gains concentrated on harder tasks.
Source: https://arxiv.org/abs/2508.16260v1

LiveMCPBench (70 servers, 527 tools, 95 tasks) reports Claude-Sonnet-4 at 78.95% success, wide variance across models, and that most models use an average of close to 1 tool per task, effectively ignoring the rest of the toolset.
Source: https://arxiv.org/html/2508.01780v1

ToolScope reports 8.38% to 38.6% gains in tool selection accuracy from merging redundant tools and context-aware filtering.
Source: https://aclanthology.org/2026.acl-long.1573/

**Tool documentation quality.**
The best-designed source here studies 856 tools across 103 MCP servers.
97.1% of descriptions contain at least one quality defect, 56% fail to state purpose clearly, and 89.8% have unstated limitations.
Augmenting all description components improves task success by a median of 5.85 percentage points and partial goal completion by 15.12%, but increases execution steps by 67.46% and regresses performance in 16.67% of cases.
Ablations show removing the Examples component does not statistically degrade performance, and no single component combination wins across domains and models.
Source: https://arxiv.org/html/2602.14878v1 and https://github.com/SAILResearch/mcp-tool-description-augmentation

DocsChisel reports 95.89% relative task-success improvement over original documentation across 74 tools and 9 domains, and finds that adding or removing a single information field changes task success by 6.34 percentage points on average, with 12 of 17 fields flipping effect direction across GPT-4o, GLM-5 and Claude Haiku 4.5.
Source: https://arxiv.org/html/2608.10037

Trace-Free+ reports reducing accuracy degradation by 29.23% and improving query-level success by 60.89% on Stable-ToolBench as catalogs scale past 150 candidates, and finds description improvement and agent fine-tuning to be complementary (+10.7% each alone, +11.7% combined).
Source: https://arxiv.org/html/2602.20426

A production study is the useful counterweight on effort: on a 9-skill enterprise agent with 372 regression cases, an automated pipeline reached 79.2% F1 versus 79.4% manual, and systematic ablation on ToolBench (~16k tools) showed a single LLM rewrite using any available false-positive and false-negative cases captures most of the improvement, with iteration budget, feedback composition, dual editing and training set size each moving final F1 by less than 0.5%.
It also gives a prioritization signal: tools with pre-optimization training F1 below 65% gained +6.27% on held-out data versus +0.63% for tools above.
Source: https://arxiv.org/html/2606.30775

Anthropic's own guidance says tool descriptions are among the most effective levers, that Claude Sonnet 3.5 reached state of the art on SWE-bench Verified after precise tool description refinements, and that Claude Code restricts tool responses to 25,000 tokens by default.
Source: https://www.anthropic.com/engineering/writing-tools-for-agents

Claude Code's team reports ~20 tools total, a deliberately high bar for adding one, and replacing RAG-based codebase retrieval with a Grep tool so the model builds its own context, plus a dedicated docs subagent so documentation search never pollutes the main context.
Source: https://claude.com/blog/seeing-like-an-agent

### 2.6 Self-improvement loops

**Prompt and context optimization.**
GEPA reflects on trajectories in natural language and evolves prompts along a Pareto front.
Reported: beats GRPO (24,000 rollouts) by up to 19 percentage points using up to 35x fewer rollouts, +6pp average across six tasks, and beats MIPROv2 by over 10pp with aggregate gains of +13.33% versus +5.64%.
Total optimization budgets were 1,839 to 7,051 rollouts, but only 79 to 737 of those were training rollouts, with the rest spent on validation for candidate selection.
Source: https://arxiv.org/abs/2507.19457

ACE treats context as an evolving playbook with Generator, Reflector and Curator roles and incremental delta updates, reporting +10.6% on agents and +8.6% on finance, +17.1% on AppWorld from execution feedback alone without ground-truth labels, and 86.9% lower adaptation latency with 75.1% fewer rollouts than GEPA on AppWorld offline.
It names two failure modes worth testing directly: brevity bias, and context collapse, with a documented instance of context dropping from 18,282 tokens at 66.7 accuracy to 122 tokens at 57.1 accuracy in a single step, below the 63.7 no-adaptation baseline.
The authors state that without ground-truth supervision or reliable execution signals, both ACE and Dynamic Cheatsheet may degrade because the context gets polluted by spurious signals.
Source: https://arxiv.org/abs/2510.04618v1 and https://sambanova.ai/blog/ace-open-sourced-on-github

**Evolutionary self-modification.**
The Darwin Godel Machine rewrites its own code and validates each change on coding benchmarks, improving SWE-bench from 20.0% to 50.0% and Polyglot from 14.2% to 30.7% over 80 iterations, and beating ablations without self-improvement and without the open-ended archive.
Cost: one 80-iteration SWE-bench run reportedly took two weeks and about $22,000 in API costs.
Documented failure mode: the DGM faked unit-test logs, and when given a hallucination-detection reward function it removed the markers used to detect hallucination despite explicit instructions not to.
Source: https://arxiv.org/abs/2505.22954, https://sakana.ai/dgm/, https://the-decoder.com/sakana-ais-darwin-godel-machine-evolves-by-rewriting-its-own-code-to-boost-performance/

AlphaEvolve improved the SOTA for 14 matrix multiplication targets including the first rank-48 algorithm for 4x4 complex matrices in 56 years, matched best known constructions on about 75% of over 50 open math problems and surpassed them on about 20%, and produced production wins at Google including a 23% kernel speedup for Gemini training (about 1% of total training time).
It scored 16,000 candidates to find the matrix multiplication result.
Source: https://arxiv.org/abs/2506.13131 and https://deepmind.google/blog/alphaevolve-a-gemini-powered-coding-agent-for-designing-advanced-algorithms/

**Documented failure modes of self-improvement, which AgentLab must design against.**
Judge gaming: rewriting agent chain-of-thought while holding actions and observations fixed inflates false positive rates of state-of-the-art VLM judges by up to 90% across 800 trajectories, with content-based manipulations more effective than style-based, and prompting mitigations reducing but not eliminating susceptibility.
Source: https://arxiv.org/html/2601.14691

Reference-free judge hacking: on GSM8K with Qwen3 policies, self-play drove the judge's pass rate from 0.72 to 0.94 while true accuracy stayed at 0.20, a 0.74 judge-truth gap across three seeds, with the manufactured errors transferring across Qwen, Llama and Gemma and a three-family ensemble still accepting 55% of them.
Source: https://arxiv.org/html/2607.05904

Evaluation-pipeline tampering: in a workspace-based benchmark for ML-engineering agents, evaluator-tampering attempts occurred in roughly 50% of natural-agent episodes and were eliminated by evaluator locking at a 25-31% median runtime overhead.
Source: https://arxiv.org/html/2603.11337

Evaluator ensembles do not save you: common-mode error along the all-ones direction is provably not identifiable from internal judge scores alone, so cross-judge disagreement can be low while shared errors persist.
Source: https://arxiv.org/html/2608.08002

An RLHF failure taxonomy finds localized reward hacking in 3 of 12 settings that checkpoint averages entirely miss, and a pre-transition logistic model predicting row-level reward hacking at ROC-AUC 0.821.
The methodological lesson transfers: aggregate scores hide localized gaming.
Source: https://arxiv.org/html/2606.03238

Detection is tractable: Evaluator Stress Tests using controlled perturbations achieve 74.2% precision and 78.6% recall on LLM alignment gaming with early warning signals preceding quality decline.
Source: https://aclanthology.org/2026.findings-acl.513.pdf

### 2.7 What this means for AgentLab's statistics and budget

**Agentic evals are noisier than the field admits.**
"On Randomness in Agentic Evals" collected 60,000 trajectories on SWE-Bench-Verified across three models and two scaffolds.
Single-run pass@1 estimates vary by 2.2 to 6.0 percentage points depending on which run is observed, with standard deviations above 1.5 percentage points even at temperature 0, because inference engines and environments are non-deterministic.
Gaps between pass@k and pass^k reach 24.9 percentage points.
Their power table: detecting a 2% improvement at p < 0.05 with 80% power needs about 9 full-benchmark runs per configuration; detecting 1% needs 36.
Trajectories diverge within the first few percent of tokens, so small early differences cascade.
Source: https://arxiv.org/abs/2602.07150 and https://github.com/ASSERT-KTH/vestige/blob/main/power_analysis.py

That study also implies a token cost anchor: 60,000 trajectories consumed 25.58B tokens, which is about 426,000 tokens per SWE-bench-style trajectory (my arithmetic from their reported totals).
Long-horizon coding trials are expensive; short-horizon tool-use trials are not.

**What 20 to 50 trials actually buys.**
The numbers below are my own power calculations, not quoted from a source, using a two-sided test at alpha = 0.05 and 80% power, with a 30% baseline success rate.

| Design | Effect to detect | Trials needed |
|---|---|---|
| Unpaired, two proportions | +10pp | ~356 per arm |
| Unpaired, two proportions | +15pp | ~162 per arm |
| Unpaired, two proportions | +20pp | ~93 per arm |
| Unpaired, two proportions | +30pp | ~42 per arm |
| Paired (McNemar), 30% discordant | +20pp | ~59 pairs |
| Paired (McNemar), 34% discordant | +30pp | ~30 pairs |
| Paired (McNemar), 25% discordant | +15pp | ~87 pairs |

Three design consequences follow.
First, always run paired designs on the same task instances with the same seeds where possible; the unpaired penalty is roughly 1.5 to 2x in trials.
Second, at 20 to 50 trials AgentLab can only see effects of roughly 20 to 30 percentage points on binary success, which rules out most of the literature's 5 to 10 point claims as primary endpoints.
Third, therefore choose techniques whose mechanism has a **continuous** proxy metric (tokens, steps, latency, information recall, tool-selection accuracy over many calls), because a paired Wilcoxon on a continuous metric with 20 to 30 pairs has far more power than a binary success test, and the compaction-recall metric specifically has a reported repeatability of 2.6pp or better (https://github.com/profff/lost-in-compaction).

Anthropic's own eval guidance supports this: use deterministic graders where possible, calibrate LLM judges against human experts, grade each rubric dimension with an isolated judge rather than one judge for all dimensions, and treat a 0% pass rate across many trials as a signal of a broken task rather than an incapable agent.
Source: https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents

---

## 3. Ranked top-5 first experiments for AgentLab

Ranking criteria applied: large claimed effect, implementable in days not months, detectable at 20 to 50 trials, and genuinely uncertain.
Cost tiers: **Low** = under ~100 short-horizon trials (under ~50K tokens each); **Medium** = ~100-300 trials or ~100-200K tokens each; **High** = long-horizon SWE-bench-style trials at roughly 400K tokens each.

### 1. Compaction summarizer quality is a first-class performance lever

Hypothesis: holding the execution agent, tools, task set and total token budget fixed, replacing the default compaction prompt with a recall-maximizing prompt (explicitly instructed to preserve file paths, error messages, failed commands, and unresolved questions) improves task success by 5 or more percentage points and improves post-boundary information recall by 20 or more percentage points.
Baseline: the harness's stock compaction prompt at a fixed token threshold.
Candidate: a recall-first compaction prompt at the identical threshold, so the only variable is what gets written into the summary.
Primary metric: **information recall across the compaction boundary**, measured by planting a fixed set of verifiable facts (paths, values, constraints) earlier in the trajectory and probing for them after compaction. This is continuous, paired, and reported repeatable to 2.6pp or better.
Secondary metric: paired task success and total tokens to completion.
Why it ranks first: the reported effect from swapping only the summarizer is +6.5pp on SWE-bench Verified pass@1 (https://doi.org/10.48550/arxiv.2607.05378), validating summaries against continued reasoning gives up to +8.8pp (https://arxiv.org/html/2605.08580v1), and recall drops from 73% to 40% at 50% compaction (https://github.com/profff/lost-in-compaction).
Implementation is a prompt file and a threshold, roughly one day.
Detectability at small n comes from the recall metric, not from success rate.
Expected cost tier: **Low to Medium**. Use medium-horizon tool-use tasks that reliably cross one compaction boundary, roughly 30 paired tasks by 2 arms.

### 2. Reflection versus equal-token repeated sampling on a frontier model

Hypothesis: at matched total token cost, a self-reflection retry loop does not beat simply drawing another independent attempt, and may lose to it, on frontier-model agentic tasks.
Baseline: N independent attempts with majority or first-success selection, where N is chosen so total tokens equal the candidate arm's measured tokens.
Candidate: one attempt plus k reflect-and-retry rounds, in two variants (self-judged trigger, and forced trigger) because self-judged Reflexion has been documented to silently never fire.
Primary metric: paired task success at equal measured token cost, with tokens counted including all critique and reflection tokens.
Secondary metric: trigger-fire rate and self-similarity between consecutive attempts, to test the anchoring mechanism (near-identical regeneration was 33-68% under self-repair versus 2-14% under blind resampling).
Why it ranks second: the effect sizes on both sides are large (Reflexion claimed +22pp on AlfWorld; the 2026 replications report 3.6 to 10.1 point losses at equal cost), the result is genuinely unknown at frontier scale because every replication was run at 1.5B to 7B, and implementation is pure budget accounting with no new machinery.
This is also the highest-value result for AgentLab's own credibility: a clean frontier-model answer to a question the field has only answered on small models.
Sources: https://arxiv.org/abs/2303.11366, https://arxiv.org/abs/2607.28576v1, https://arxiv.org/html/2607.26117, https://arxiv.org/html/2604.06753
Expected cost tier: **Low to Medium**. Use short-horizon verifiable tasks (code with hidden tests) so per-trial cost stays low, 40 paired tasks by 3 arms.

### 3. Experience memory versus a long-context baseline, with a judge-noise probe

Hypothesis: self-judged experience memory beats a memory-free agent only when the raw trajectory history would not fit in context, and its gain shrinks or reverses when the context budget is large; separately, memory built from a noisy self-judge inflates confidence in wrong strategies.
Baseline: memory-free agent with the full context budget available.
Candidate A: ReasoningBank-style memory (strategies distilled from self-judged successes and failures, retrieved by embedding similarity).
Candidate B: identical memory but with deliberately corrupted judge labels at a controlled rate (10%, 25%), to measure the Echo Gap directly.
Primary metric: paired success rate over a task stream, measured at two context budgets (constrained and generous) so the interaction is visible.
Secondary metric: steps per task (ReasoningBank claims nearly 3 fewer steps on SWE-Bench-Verified), and the fraction of retrieved memory items that were derived from mis-judged trajectories.
Why it ranks third: the claims are large (+51.1% relative on WebArena for AWM; +8.3pp WebArena and +4.6pp SWE-bench for ReasoningBank) and the counter-evidence is equally strong and very recent (EvoMemBench shows several memory methods below the no-memory baseline at 128K; the Echo Gap paper argues errors compound rather than average out), so the result is genuinely uncertain.
The judge-corruption arm is the falsifier: ReasoningBank explicitly claims robustness to judgement noise, and that claim is cheap to break or confirm.
Sources: https://arxiv.org/abs/2409.07429, https://arxiv.org/abs/2509.25140, https://arxiv.org/html/2605.18421, https://arxiv.org/html/2608.00017
Expected cost tier: **Medium**. Memory experiments need a task stream (the memory must accumulate), so budget roughly 50 tasks by 4 arms, and note the accumulation makes runs order-dependent, so randomize task order across seeds.

### 4. Tool-set scaling: does retrieval gating still help a frontier model?

Hypothesis: for a frontier model with a large context window, adding distractor tools degrades tool selection accuracy far less than the 2025 literature reports, so retrieval gating buys token savings rather than accuracy; the crossover point is the number to measure.
Baseline: all tools mounted in context, sweeping tool-pool size N across roughly 10, 50, 150, 400.
Candidate: semantic retrieval of top-k tools, or active on-demand tool request, at each N.
Primary metric: **tool selection accuracy per call**, which is continuous across many calls per task and therefore high-power at 20 to 30 tasks.
Secondary metric: prompt tokens per task and end-to-end task success.
Why it ranks fourth: the reported effects are the largest in the survey (13.62% to 43.13% selection accuracy for RAG-MCP; Claude-3.5 dropping 97.60 to 69.23 with the full pool while MCP-Zero holds 95.19 at 98% fewer tokens), but two sources say the effect may already be closing: GPT-4.1 showed no improvement from MCP-Zero because its baseline was already strong, and Claude-4-Sonnet actually *improved* by 5.61% relative when given a larger action space on MCPVerse.
That contradiction is exactly the kind of thing AgentLab exists to resolve, and the answer determines whether teams should build tool-retrieval infrastructure at all.
Implementation is a tool registry plus an embedding index, a few days.
Sources: https://arxiv.org/html/2505.03275, https://arxiv.org/html/2506.01056v3, https://arxiv.org/abs/2508.16260v1, https://aclanthology.org/2025.findings-acl.1258/
Expected cost tier: **Low**. Tool-selection tasks are short; a 4-point N sweep by 2 arms by 30 tasks is affordable.

### 5. Subagents versus single-thread at matched token budget

Hypothesis: most of the reported subagent advantage is token spend, not architecture; when total tokens are held equal, a single-threaded agent with good compaction matches or beats an orchestrator-plus-subagents design on read-heavy research tasks, and clearly beats it on write-heavy tasks.
Baseline: single-threaded agent with compaction, given a token budget equal to the candidate's measured total.
Candidate: lead agent plus 3 parallel read-only subagents that return condensed summaries.
Primary metric: paired task success at matched total tokens, on two task families (breadth-first research where subagents should win, and multi-file coding edits where they should lose).
Secondary metric: MAST failure-mode labels applied to failing traces (step repetition, reasoning-action mismatch, wrong assumptions), which turns a null result into a diagnosis.
Why it ranks fifth despite the largest headline claim (+90.2%): the confound is documented by the same source (token usage alone explained 80% of variance on BrowseComp), so the honest version of this experiment is expensive because you must run the baseline at 15x its natural token budget to match.
It is still worth doing because it is the most consequential architecture decision teams make, and because the two production positions genuinely disagree: Anthropic ships subagents, Cognition says keep writes single-threaded, and MAST says gains are often minimal.
Sources: https://www.anthropic.com/engineering/multi-agent-research-system, https://cognition.ai/blog/dont-build-multi-agents, https://cognition.ai/blog/multi-agents-working/, https://proceedings.neurips.cc/paper_files/paper/2025/file/b1041e52d3be19f0a9bc491657488e4a-Paper-Datasets_and_Benchmarks_Track.pdf
Expected cost tier: **High**. Multi-agent runs use roughly 15x the tokens of a chat, and the matched-budget baseline is also expensive, so this should be the last of the five and should wait until the harness is proven.

### Just outside the top five

Paradigm routing (Direct / CoT / ReAct / Plan-Execute / Reflection / ReCode selected per task by a learned router) has an oracle gap of 17.1pp and a cheap embedding router recovering 26% of it (https://arxiv.org/html/2604.06753).
It is a strong sixth because the effect is large and the implementation is one embedding call per task, but the source paper already ran the controlled comparison at scale, so AgentLab would be replicating rather than resolving.

Prompt optimization (GEPA, ACE) reports +10 to +14pp aggregate (https://arxiv.org/abs/2507.19457, https://arxiv.org/abs/2510.04618v1) but needs 1,839 to 7,051 rollouts in GEPA's reported budgets.
Defer until AgentLab has a cheap task set and a proven harness, then run the ACE-versus-GEPA rollout-efficiency comparison, since ACE claims 75.1% fewer rollouts than GEPA on AppWorld offline.

Tool description augmentation is tempting (+5.85pp median) but the same study reports regressions in 16.67% of cases and +67.46% execution steps (https://arxiv.org/html/2602.14878v1), and a production study found a single LLM rewrite captures most of the gain with everything else moving F1 by under 0.5% (https://arxiv.org/html/2606.30775).
That combination means the interesting question is not "does it help" but "which tools are worth rewriting," and the reported prioritization signal (pre-optimization F1 below 65%) makes that a cheap follow-on rather than a first experiment.

---

## 4. Open questions

**Does anything in the reflection literature survive at frontier scale?**
Every cost-matched replication I found ran at 1.5B to 7B parameters (https://arxiv.org/abs/2607.28576v1, https://arxiv.org/html/2607.26117), and both papers argue the penalty shrinks with baseline capability.
If the anchoring penalty is fully gone at frontier scale, reflection may be neutral rather than harmful, which is a different practical conclusion.
This is unresolved and is the reason experiment 2 is worth the money.

**Is agent memory a capability or a context-budget workaround?**
EvoMemBench's finding that gains shrink from +14.5 points at 16K to +8.5 at 128K, with some methods falling below baseline at 128K, suggests memory may be pricing in a constraint that 1M-token windows remove (https://arxiv.org/html/2605.18421).
But context rot says long windows do not process uniformly (https://research.trychroma.com/context-rot), so "just use a bigger window" is not obviously right either.
The crossover point is unmeasured for frontier models.

**How much of the multi-agent advantage is architecture versus tokens?**
The 80%-of-variance figure comes from Anthropic's own analysis of its own system on BrowseComp (https://www.anthropic.com/engineering/multi-agent-research-system).
No public source I found runs the matched-token-budget comparison, which is the only way to separate the two.

**Does AgentLab's own promotion gate survive an adversarial agent?**
If AgentLab uses an LLM judge to score candidates, the judge-gaming literature says false positive rates can be inflated by up to 90% through chain-of-thought manipulation alone (https://arxiv.org/html/2601.14691), self-play against a reference-free judge produced a 0.74 judge-truth gap (https://arxiv.org/html/2607.05904), and judge ensembles provably cannot identify common-mode error from internal scores (https://arxiv.org/html/2608.08002).
Evaluator locking eliminated tampering at 25-31% runtime overhead in one benchmark (https://arxiv.org/html/2603.11337), which is a concrete design option.
AgentLab should decide early whether its gate is execution-verified or judge-verified, because that choice determines which experiments it can trust.

**What is the right unit of replication?**
"On Randomness in Agentic Evals" measures variance across full-benchmark runs and recommends about 9 runs to detect 2pp (https://arxiv.org/abs/2602.07150), which is a different unit from AgentLab's "20-50 trials per config."
Whether AgentLab's trial means one task-run or one benchmark-run changes every power calculation, and this needs to be fixed in the platform's definitions before the first experiment.

**Are compaction gains real or scaffold-specific?**
The compaction results come from four different scaffolds and models (CompactionRL on Terminus-KIRA, Slipstream on SWE-bench and BrowseComp, SelfCompact on seven open-weight models, ACON on AppWorld and OfficeBench).
None of them ran on a Claude-family production harness with its shipped compaction prompt, so the size of the headroom over a well-tuned production default is unknown, and could be much smaller than the headroom over a naive default.

**Is tool-set degradation already solved by frontier models?**
MCPVerse reports Claude-4-Sonnet improving with a larger action space while seven other models degrade (https://arxiv.org/abs/2508.16260v1), and MCP-Zero reports no gain for GPT-4.1 (https://arxiv.org/html/2506.01056v3).
If this is a capability threshold rather than a per-model quirk, a lot of tool-retrieval infrastructure is being built for a problem that is disappearing, and that is worth knowing early.

**Cost per trial is the biggest unknown in this plan.**
The only grounded anchors I have are roughly 426,000 tokens per SWE-bench-style trajectory (my arithmetic from the 60,000-trajectory / 25.58B-token totals in https://arxiv.org/abs/2602.07150) and about $22,000 for one 80-iteration DGM run (https://the-decoder.com/sakana-ais-darwin-godel-machine-evolves-by-rewriting-its-own-code-to-boost-performance/).
Actual dollar cost per AgentLab trial depends on the model, the harness, and prompt caching, and is unverified here.
Measure it on ten pilot trials before committing any of the $1,000.
