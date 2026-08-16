# Evaluation Methodology and Statistics for Comparing Agents

Research brief for AgentLab v0.1.
Date: 2026-08-16.
Scope: how to compare a baseline agent config against a candidate config, over repeated stochastic trials, on a $1,000 total budget, without fooling ourselves.

## 1. Summary

Agent evals are experiments with two nested noise sources: which tasks you picked, and how the agent happened to roll that day.
Treat them that way or you will ship noise: on SWE-bench Verified, single-run pass@1 varies by 2.2 to 6.0 percentage points across reruns, and that persists at temperature 0 ([arXiv 2602.07150](https://arxiv.org/html/2602.07150)).
Pairing (same tasks for both configs) is the single highest-leverage move, and it is free ([Miller 2024](https://arxiv.org/abs/2411.00640)).
Repeats per task are cheap variance reduction but saturate fast: past about 5 repeats, the binding constraint becomes the number of distinct tasks, not the number of rolls.
At a 10-point minimum detectable effect this implies roughly 60 tasks by 5 repeats per config, about 600 trials total, which is $300 to $600 of the budget for one defensible claim.
Report pass@1 for capability and pass^k for reliability; they answer different questions and diverge by up to 24.9 points ([arXiv 2602.07150](https://arxiv.org/html/2602.07150), [tau-bench](https://arxiv.org/abs/2406.12045)).
Judges must come from a different model family than the agent, because Claude and GPT judges measurably favor their own family ([arXiv 2508.06709](https://arxiv.org/html/2508.06709v1)).
Calibrate every judge against about 100 human-labeled traces and gate on TPR and TNR above 0.90 ([Husain](https://hamel.dev/blog/posts/llm-judge/)).
Promotion rule: superiority on one primary metric plus non-inferiority on every protected metric, correcting beta rather than alpha for the guardrails ([Spotify](https://engineering.atspotify.com/2024/03/risk-aware-product-decisions-in-a-b-tests-with-multiple-metrics)).
Static task suites rot; OpenAI retired SWE-bench Verified in Feb 2026 over contamination and broken tests ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)).

## 2. Findings

### 2.1 Statistical methodology for LLM and agent evals

**Miller's framework is the reference implementation and it is small enough to copy.**
Evan Miller's "Adding Error Bars to Evals" (Anthropic, Nov 2024) frames eval questions as drawn from an unseen super-population, which unlocks standard experiment-design statistics ([arXiv 2411.00640](https://arxiv.org/abs/2411.00640), [Anthropic post](https://www.anthropic.com/research/statistical-approach-to-model-evals)).
Its five recommendations are: use the CLT standard error, cluster standard errors when questions come in groups, reduce variance by resampling answers, analyze question-level paired differences when comparing two models, and use power analysis to decide sample size.

**The CLT standard error, not the Bernoulli one.**
SE = sqrt( (1/(n-1)) * sum_i (s_i - s_bar)^2 / n ), which is correct for fractional and partial-credit scores; the Bernoulli form sqrt(p(1-p)/n) is only right for strictly binary scores and is conservative otherwise ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
Miller regards bootstrapping as unnecessary for simple means with many questions, but explicitly carves out "complicated sampling schemes or estimators," which is exactly the agent case.

**Clustered standard errors matter more than people expect.**
On real Anthropic data, clustered standard errors were up to 3.05x larger than naive ones on DROP, 1.88x on MGSM ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
Failing to cluster makes intervals look far tighter than they are.
For AgentLab the natural cluster is the task family or environment, not the individual task.

**Paired designs are a free variance cut, and the size of the cut depends on correlation.**
Var(paired) = Var(unpaired) - 2 Cov(x_A, x_B)/n ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
Miller measures question-score correlations between frontier models on popular evals at 0.3 to 0.7, and shows a worked case where a correlation of 0.5 cuts estimator variance by one third in relative terms.
Two configs of the *same* agent differing by one change should correlate higher than two different frontier models, so the cut should be larger for AgentLab than Miller's 1/3 example.

**The power formula, verbatim.**
n = (z_{alpha/2} + z_beta)^2 * (omega^2 + sigma_A^2/K_A + sigma_B^2/K_B) / delta^2 ([arXiv 2411.00640](https://arxiv.org/html/2411.00640), Eq. 9).
Here omega^2 = Var(x_A) + Var(x_B) - 2Cov(x_A, x_B) is the paired between-task variance of the true per-task means, sigma^2 are the within-task conditional variances, K is repeats per task, and delta is the minimum detectable effect.
Inverted for a fixed task count: delta = (z_{alpha/2} + z_beta) * sqrt( (omega^2 + sigma_A^2/K_A + sigma_B^2/K_B) / n ) (Eq. 10).
Miller's own worked example: at n=198, increasing K from 1 to 10 moves the MDE from 13.2% to 7.5%.
Note the structural point buried in that formula: omega^2 does not shrink with K at all, so repeats hit a floor and only more tasks get you past it.

**Resampling saturates.**
Once E[sigma_i^2]/K is much less than Var(x), further increases in K barely move the standard error ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
In Miller's uniform-difficulty example, K=2 cuts variance by 1/3, K=4 by 1/2, K=6 by 5/9, with an asymptotic ceiling of 2/3.

**Do not lower temperature to reduce variance.**
Miller shows temperature reduction can shift conditional variance into the (irreducible) variance of conditional means, tripling the floor variance in one worked case, and can bias the estimator in another ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
This is directly relevant because "just run at temp 0" is the obvious wrong fix.

**Temperature 0 does not make agent evals deterministic anyway.**
60,000 SWE-bench Verified trajectories across three models and two scaffolds show single-run pass@1 varying by 2.2 to 6.0 percentage points, with standard deviations above 1.5 points *at temperature 0* ([arXiv 2602.07150](https://arxiv.org/html/2602.07150)).
Trajectories diverge within the first ~1% of tokens and cascade into different solution strategies.
The paper's concrete power table: detecting a 2-point improvement at p<0.05 with 80% power needs about 9 runs per config; a 1-point improvement needs about 36 runs at median observed variance (sigma=1.5%), or 8 runs at their lowest observed variance (sigma=0.7%).

**Variance decomposition, expressed as ICC, tells you whether a win is real.**
Intraclass correlation splits total variance into between-task (difficulty) and within-task (agent inconsistency) ([arXiv 2512.06710](https://arxiv.org/html/2512.06710v1), code at [github](https://github.com/youdotcom-oss/stochastic-agent-evals)).
Measured ICC: 0.4955 to 0.7118 on FRAMES (retrieval/reasoning), 0.304 to 0.774 on GAIA (agentic).
Their operational rule for sub-agent replacement decisions: an accuracy improvement is only trustworthy if ICC also improves.
ICC estimates converge by n=8 to 16 trials for structured tasks and n>=32 for complex reasoning, which sets a floor if you want to *report* ICC rather than just use it for planning.

**Even two or three repeats buys most of the ranking stability.**
Re-evaluating eight models on AI4Math with three runs each: 10 of 12 slices (83%) invert at least one pairwise rank relative to the three-run aggregate when using a single run, and two runs remove about 83% of single-run inversions ([arXiv 2509.24086](https://arxiv.org/html/2509.24086)).
Mean ICC across slices was 0.68.
Notably, pairwise *significance* verdicts were stable (0% sign-flip rate) because effect sizes were large; ranks are fragile long before significance is.

**For nested data, hierarchical bootstrap beats flat bootstrap by a wide margin.**
A computer-use agent study found that bootstrapping only rollouts gave 17% suite-level CI coverage; adding configuration resampling raised it to 56%; adding scenario resampling reached the nominal 95% ([arXiv 2605.08261](https://arxiv.org/html/2605.08261)).
At R=3 rollouts, Wald intervals covered the true value only 25% of the time because they go degenerate at p=0 or p=1, while Wilson intervals held near-nominal coverage across all R including R=1.
Their bottom line: use Wilson at the rollout level and hierarchical bootstrap at the suite level.
METR uses the same shape of method, a three-level hierarchical bootstrap over task families, then tasks, then runs ([METR](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/)).

**Empirical CI-method comparison for eval-shaped data exists.**
A simulation study across N=10 to 100 with 2,000 Monte Carlo trials per condition, validated against real eval data, finds that taking the per-input mean across runs and then applying standard interval methods (Wilson, Tango, Normal-Inverse-Gamma) achieves near-target coverage, because input-level clustering is absorbed when the mean is one observation ([statsforevals.com](https://statsforevals.com/which-method.html)).
Explicitly run-aware nested methods give narrower intervals at the same coverage.
Practical read: "means then Wilson" is a safe, cheap baseline; nested bootstrap is the upgrade.

**Bayesian hierarchical modeling is the more powerful alternative and can prove equivalence.**
HiBayES fits multilevel GLMs over the natural nesting (repeats within items within subdomains within domains), stays stable at under 20 data points per cluster, and can provide evidence *for* the null via HPDI containment, which NHST cannot ([arXiv 2505.05602](https://arxiv.org/html/2505.05602)).
They also show a Beta-Binomial GLM outperforms a plain Binomial GLM on GAIA data, whose success-rate distribution is asymmetric and bimodal.
This is the right v0.3 direction; it is more machinery than v0.1 needs.

### 2.2 pass@k, pass^k, and which metric supports which claim

**The two estimators, unbiased forms.**
With n trials per task and c successes:
pass@k = 1 - C(n-c, k)/C(n, k), the chance at least one of k attempts succeeds (Chen et al. 2021 / HumanEval form).
pass^k = C(c, k)/C(n, k), the chance *all* k attempts succeed, averaged across tasks ([tau-bench, arXiv 2406.12045](https://arxiv.org/abs/2406.12045)).
At k=1 both reduce to the mean per-task success rate.

**pass^k was invented precisely for the reliability claim.**
tau-bench introduced pass^k because "for real-world agent tasks requiring reliability and consistency like customer service," average success is not the quantity that matters ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045)).
Their headline result: gpt-4o with function calling scored above 60% pass^1 on tau-retail but under 25% at pass^8, roughly a 60% relative drop ([Sierra](https://sierra.ai/blog/benchmarking-ai-agents)).
Published tau-bench numbers show the decay shape clearly, for example claude-3-5-sonnet-20241022 on airline: 0.460 / 0.326 / 0.263 / 0.225 for pass^1 through pass^4 ([tau-bench repo](https://github.com/sierra-research/tau-bench)).

**Report the envelope, not one number.**
pass@k is the optimistic bound (what you get with retries), pass^k is the pessimistic bound (what you get if every attempt must land), pass@1 is the expectation ([arXiv 2602.07150](https://arxiv.org/html/2602.07150)).
That study found gaps up to 24.9 percentage points between optimistic and pessimistic bounds on the same agent.
A large (pass@k - pass@1) gap means the agent can solve the task but needs luck to find the path; a large (pass@1 - pass^k) gap means success is hostage to random choices.

**Mapping metrics to claims.**
Capability claim ("the candidate can do more"): pass@1 and pass@k.
Reliability claim ("the candidate can be trusted to do it every time"): pass^k, plus ICC.
Cost claim: cost per successful task, not cost per trial.
These are not interchangeable and a promotion gate that only checks pass@1 will happily promote a less reliable agent.

**Partial-credit multi-run scores are not binomial.**
Aggregate CIs for pass@k and pass^k should come from a hierarchical (task-then-run) bootstrap rather than Wald or Wilson; Wilson remains correct for the per-task binary pass rate ([agentrel](https://pypi.org/project/agentrel/)).
`agentrel` is a small open-source library that already implements exactly this stack (variance components, ICC(1), unbiased pass@k and pass^k with bootstrap CIs, paired comparison with Holm/BH, flakiness flagging), verified against brute-force subset enumeration.
Worth reading as a reference implementation even if AgentLab does not depend on it.

**pass@k can be gamed in stateful environments.**
A 1MB replay script that blindly executes a recorded action sequence without ever looking at the screen outperformed frontier models on prominent static computer-use benchmarks; the authors prove its expected success rate equals the source agent's pass@k in deterministic environments ([arXiv 2605.08261](https://arxiv.org/html/2605.08261)).
Reporting pass@k on a fixed, deterministic environment is therefore a weak claim.

### 2.3 LLM-as-judge calibration and correlated evaluator failure

**Self-preference and family-preference are measured, not hypothetical.**
A statistical study over 5,000+ prompt-completion pairs with expert human annotations and nine LLM judges finds GPT-4o and Claude 3.5 Sonnet systematically assign higher scores to their own outputs, and separately exhibit *family bias*, rating other models from the same family higher than warranted ([arXiv 2508.06709](https://arxiv.org/html/2508.06709v1)).
Claude 3.5 Sonnet boosted scores of both Claude v2 and Claude 3 Sonnet.
Llama and Mistral models did not show this effect in their data.
This is AgentLab's stated "correlated evaluator failure" danger, quantified, and it is the direct argument for family separation.

**The precise failure mode for a paired A/B is worse than the level bias.**
In a paired design, a judge with *constant* bias partly cancels in the difference between arms.
It does not cancel when the judge's error rate differs between arms, which is exactly what family bias produces if baseline and candidate use different model families.
So the operational rule is stronger than "do not let the agent judge itself": the judge must be from a family shared by *neither* arm whenever the arms differ in model family.

**The other classic biases.**
Position bias: a systematic study of 15 LLM judges across MTBench and DevBench, 22 tasks, ~40 solution-generating models, over 150,000 evaluation instances, confirms position bias is not chance, varies significantly across judges and tasks, and is driven strongly by the quality gap between solutions rather than by prompt length ([ACL 2025.ijcnlp-long.18](https://aclanthology.org/2025.ijcnlp-long.18/)).
Verbosity bias: all tested LLM judges preferred longer responses more strongly than human judges did, and the effect was larger on multi-turn conversation than on summarization ([arXiv 2408.13006](https://arxiv.org/html/2408.13006v2)).
The same study found a significant negative correlation between judge accuracy and position-bias magnitude, and that position preference depends on both the model and the prompt template.
Mitigations in the literature: swap-and-average, swap-and-mark-conflicts-as-tie (PandaLM), and shuffling ([survey, arXiv 2411.15594](https://arxiv.org/html/2411.15594v6)).

**Juries beat single judges, but only if the panel is genuinely disjoint.**
A Panel of LLM evaluators (PoLL) drawn from three disparate model families correlated better with human judgments than a single GPT-4 judge, showed less intra-model bias, and cost seven to eight times less ([arXiv 2404.18796](https://arxiv.org/abs/2404.18796)).
Their measured spread against human annotators: PoLL standard deviation 2.2 versus 6.1 for GPT-3.5 alone.
They also observed that each individual model's largest positive score delta occurs when it judges itself.

**Do not build the jury as a debate.**
A systematic study of four bias types across Multi-Agent-Debate and LLM-as-Meta-Judge frameworks found the debate framework *amplifies* biases sharply after the initial round and sustains the amplification, while meta-judge approaches resist it ([ACL 2025.findings-emnlp.941](https://aclanthology.org/2025.findings-emnlp.941/)).
Independent scoring plus a voting function is the safe aggregation.
A separate benchmark reports that naively polling a panel can amplify a bias the judges *share*, and that order-swapping alone fixes at most one bias while sometimes worsening others ([IJISAE 2025](https://ijisae.org/index.php/IJISAE/article/view/8407)).

**The calibration protocol that practitioners actually use.**
Split human-labeled data into train (10-20%, used only as few-shot examples), dev (40-45%, iterate here), test (40-45%, read once) ([validate-evaluator skill](https://github.com/hamelsmu/evals-skills/blob/main/skills/validate-evaluator/SKILL.md)).
Target ~100 labeled traces, balanced at roughly 50 pass / 50 fail even if real prevalence is skewed, because you need enough failures to measure TNR.
Measure TPR and TNR, not raw accuracy, because on an imbalanced set an always-pass judge hits 90% agreement while catching zero failures ([aievals.co](https://www.aievals.co/learn/llm-as-judge/calibration-to-humans)).
Target TPR > 0.90 and TNR > 0.90; minimum acceptable 0.80 and 0.80.
Pin dated model snapshots, not floating aliases, because providers update models without notice.

**Correct the aggregate rate for known judge error.**
Rogan-Gladen: theta_hat = (p_obs + TNR - 1) / (TPR + TNR - 1), clipped to [0,1], invalid when the denominator approaches zero ([validate-evaluator skill](https://github.com/hamelsmu/evals-skills/blob/main/skills/validate-evaluator/SKILL.md)).
Worked example: TPR 0.92, TNR 0.88, observed pass rate 0.80 gives a corrected 0.85.
A recent paper gives the full statistical treatment, constructing confidence intervals that account for uncertainty from *both* the test set and the calibration set, plus an adaptive algorithm for allocating calibration samples between the two label types ([arXiv 2511.21140](https://arxiv.org/html/2511.21140)).
Their allocation result: when the judge is worse at identifying incorrect outputs, spend more calibration budget on true-negative examples.

**Which agreement statistic to report.**
On binary verdicts, Pearson r, Spearman rho, Kendall tau-b, phi, and MCC all collapse to a single number, so reporting several manufactures fake triangulation ([arXiv 2606.00093](https://arxiv.org/html/2606.00093)).
Cohen's kappa is the one that adds information: the kappa-phi gap measures how far the judge's positive-label rate has drifted from the human's.
Every symmetric statistic is invariant under swapping FP and FN, so none of them distinguishes a strict judge from a lenient one; report the 2x2 confusion matrix.
One practitioner nuance worth flagging as contested: Husain's guidance reserves Cohen's kappa for human-vs-human inter-annotator agreement and uses TPR/TNR for judge-vs-ground-truth ([hamel.dev](https://hamel.dev/blog/posts/evals-faq/)), while the survey literature uses kappa for judge-vs-human ([arXiv 2411.15594](https://arxiv.org/html/2411.15594v6)).
Reporting the confusion matrix satisfies both camps.

**Contested: does the judge really need a different model family?**
Husain argues that using the same model is usually fine, because the judge does a different (scoped binary classification) task than the pipeline, and what ultimately matters is measured alignment with human labels ([hamel.dev](https://hamel.dev/blog/posts/evals-faq/)).
This is a defensible position for a *single-system* quality metric.
It is the wrong position for AgentLab specifically, because AgentLab's output is a *comparison* between two configs, and family bias distorts comparisons even when it would wash out of a level.
Recommendation below sides with family separation, and flags this as a deliberate departure from one respected practitioner view.

**Rubric-based beats pairwise for a promotion gate.**
Pairwise comparison aligns better with human preference in general ([survey, arXiv 2411.15594](https://arxiv.org/html/2411.15594v6)), but it imports position bias and yields a relative verdict with no absolute anchor.
A promotion gate needs an absolute per-trial score so the paired delta and its CI are well defined.
Binary pass/fail per rubric criterion, with a written critique, is the pattern that survives contact with domain experts, because uncalibrated 1-5 scales are not actionable and different raters interpret them differently ([hamel.dev](https://hamel.dev/blog/posts/llm-judge/)).

**Criteria drift is real and it means you cannot fully specify the rubric up front.**
"To grade outputs, people need to externalize and define their evaluation criteria; however, the process of grading outputs helps them to define that very criteria" (Shankar et al., quoted in [hamel.dev](https://hamel.dev/blog/posts/llm-judge/)).
Implication for AgentLab: budget for rubric revision, and version the rubric as part of the experiment record so old results stay interpretable.

### 2.4 Trajectory evaluation

**Outcome-only scoring is a known blind spot.**
Agent-as-a-Judge argues that scoring only final outcomes "does not effectively pinpoint what is happening within agentic systems that affects the resolve rate," and that agents should be evaluated on the full thought-and-action trajectory ([arXiv 2410.10934](https://arxiv.org/abs/2410.10934v2), [ICML 2025](https://proceedings.mlr.press/v267/zhuge25a.html)).
On DevAI (55 tasks, 365 hierarchical requirements), Agent-as-a-Judge agreed with human experts about 90% of the time versus 70% for LLM-as-a-Judge, and cut evaluation cost from 86 hours / $1,297 to about 2 hours / $31, roughly a 97% reduction.
The framework decomposes into modules (graph, locate, read, search, retrieve, ask, memory, planning) rather than being one big prompt.

**Deterministic trajectory matching is cheap and should come first.**
LangChain's `agentevals` provides four trajectory-match modes with no LLM calls: `strict` (exact tools, exact order), `unordered` (same tools, any order), `subset` (agent called only tools from the reference, catching unnecessary actions), `superset` (agent called at least the reference tools, catching missing actions) ([LangSmith docs](https://docs.langchain.com/langsmith/trajectory-evals), [repo](https://github.com/langchain-ai/agentevals)).
Tool-argument matching is separately configurable as exact / ignore / subset / superset, or by a custom comparator per tool.
The `subset` mode is the direct implementation of "did the agent do unnecessary work."

**Partial credit on trajectories is better than binary.**
LangSmith recommends a subsequence score, `i / len(reference_trajectory)`, counting how many expected steps appeared in order, because binary exact-match cannot distinguish off-by-one-step from completely wrong, and multiple correct paths often exist ([LangSmith](https://docs.langchain.com/langsmith/evaluate-complex-agent)).
They split evaluation into three levels: final response, trajectory, and single step ([approaches doc](https://docs.langchain.com/langsmith/evaluation-approaches)).

**Managed offerings have converged on the same taxonomy.**
Bedrock AgentCore Evaluations ships session-level trajectory evaluators with programmatic (zero-token) scoring: `Builtin.TrajectoryExactOrderMatch`, `Builtin.TrajectoryInOrderMatch`, `Builtin.TrajectoryAnyOrderMatch` ([AWS docs](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html)).
It also ships tool-level LLM judges: `Tool selection accuracy` (is calling this tool justified at this point) and `Tool parameter accuracy` (are parameters correctly derived from context), plus a session-level `GoalSuccessRate` driven by natural-language assertions ([prompt templates](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/prompt-templates-builtin.html)).
Their published prompt templates are worth reading as concrete rubric examples.
Note their operational constraint: evaluators using ground-truth placeholders cannot be used for online evaluation of live traffic, since ground truth does not exist there.

**Inspect handles the statistics side natively.**
Inspect (UK AI Security Institute) exposes `epochs` for repeated sampling with configurable score reducers, computes `stderr()` correctly via the CLT, and documents clustered standard errors as a first-class scoring metric ([Inspect scorers](https://inspect.aisi.org.uk/scorers.html), [main docs](https://inspect.aisi.org.uk/)).
Miller specifically credits Inspect for computing SE correctly ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
Its `Task` API also carries `token_limit`, `time_limit`, `working_limit`, and `cost_limit` per sample, which is the mechanism for cost-controlled comparison ([task.py](https://github.com/UKGovernmentBEIS/inspect_ai)).

**The ABC checklist explicitly recommends process metrics.**
Its first best practice for benchmark developers is "use process-based evaluation metrics alongside outcome-based metrics" ([UIUC Kang Lab](https://uiuc-kang-lab.github.io/agentic-benchmarks/)).
Second is "benchmark your LLM-as-a-judge in a reproducible manner."

**Automatic failure attribution is a solved-enough problem to copy.**
tau-bench ships an auto error identification tool doing fault assignment (user / agent / environment) and fault type classification (`goal_partially_completed`, `used_wrong_tool`, `used_wrong_tool_argument`, `took_unintended_action`) ([repo](https://github.com/sierra-research/tau-bench)).
That four-way taxonomy is a reasonable starting schema for AgentLab's trajectory diagnostics.

### 2.5 Benchmark design pitfalls

**Static benchmarks rot, and the biggest one just got retired.**
In Feb 2026 OpenAI stopped reporting SWE-bench Verified, concluding it "no longer measures frontier" capability ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/)).
Two findings drove it: an audit of 138 problems (27.6% of the dataset, the ones models often failed), reviewed by at least six engineers each, found 59.4% had material test-design or problem-description flaws, broken down as 35.5% narrow tests enforcing implementation details, 18.8% wide tests checking unspecified functionality, 5.1% miscellaneous.
And every frontier model tested could reproduce the human-written gold patch or verbatim problem statement specifics for some tasks, indicating training exposure; models that had seen problems in training were more likely to succeed because they had the extra information needed to pass underspecified tests.
Their two general lessons: benchmarks sourced from public material carry contamination risk and need explicit contamination testing; and automated scoring must be simultaneously agnostic to implementation details and robust to shortcuts.

**The recommended replacement was retracted five months later.**
OpenAI subsequently audited SWE-Bench Pro and estimated ~30% of tasks are broken (automated pipeline flagged 27.4%, human annotation campaign 34.1%), citing overly strict tests, underspecified prompts, low-coverage tests, and misleading prompts, and retracted their earlier recommendation to adopt it ([OpenAI](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)).
The relevant lesson for AgentLab is not "which benchmark" but "assume your task suite has a double-digit broken-task rate until you have audited it."

**Contamination is measurable without training-data access.**
SWE-Bench+ found 32.67% of successful patches involved solution leakage (the fix was in the issue report or comments) and 31.08% passed due to weak tests; filtering both dropped SWE-Agent+GPT-4 from 12.47% to 3.97%, and on a post-cutoff dataset to 0.55% ([arXiv 2410.06992](https://arxiv.org/html/2410.06992)).
Over 94% of SWE-bench issues predate the models' knowledge cutoffs.
The SWE-Bench Illusion diagnostic is the cleanest contamination probe: ask models to identify the buggy file path from the issue description *alone*, with no repository structure.
Models hit up to 76% on SWE-bench Verified versus at most 53% on equally popular repositories not in SWE-bench, a gap of up to 47 points ([Microsoft Research](https://www.microsoft.com/en-us/research/publication/the-swe-bench-illusion-when-state-of-the-art-llms-remember-instead-of-reason/)).
An independent replication on Claude models found 3x better localization and 6x better all-files-found rates on SWE-bench Verified than on BeetleBox or SWE-rebench ([arXiv 2512.10218](https://doi.org/10.48550/arxiv.2512.10218)).
This "make the task logically impossible and see if the model still wins" probe is cheap and AgentLab should run it on any borrowed task suite.

**Agent benchmarks enable a worse kind of overfitting than model contamination.**
"This is a much more serious problem than LLM training data contamination, as knowledge of test samples can be directly programmed into the agent... In principle, a lookup table can achieve 100% accuracy on many agent benchmarks" ([AI Agents That Matter, arXiv 2407.01502](https://arxiv.org/abs/2407.01502v1)).
Kapoor et al. found many agent benchmarks have no held-out set at all, and prescribe four levels of generality each needing a different kind of holdout ([project page](https://agents.cs.princeton.edu/)).

**Cost must be a first-class axis, not a footnote.**
Three trivially simple baseline agents outperformed several SOTA complex architectures on HumanEval while costing much less; the authors argue all agent evaluation must be cost-controlled or the field will optimize for expensive agents that top leaderboards ([arXiv 2407.01502](https://arxiv.org/abs/2407.01502)).
They also flag the budget problem AgentLab faces directly: "The high cost makes it infeasible to run evaluations multiple times, and perhaps as a result, agent evaluations are rarely accompanied by error bars."
Their reproduction found reported accuracy scores above the maximum of five runs they performed.

**Reward design bugs can dwarf model differences.**
The Agentic Benchmark Checklist (ABC) audited ten popular benchmarks and found flaws causing under- or over-estimation by up to 100% in relative terms ([arXiv 2507.02825](https://arxiv.org/abs/2507.02825), [NeurIPS 2025](https://proceedings.neurips.cc/paper_files/paper/2025/file/f316275b44ee2de533102913828a8107-Paper-Datasets_and_Benchmarks_Track.pdf)).
Specifics: tau-bench includes intentionally unsolvable tasks (38% of airline, 6% of retail) where success is defined as leaving the environment unchanged, so a do-nothing agent passes them and scores 38%, beating a GPT-4o agent.
tau-bench also grades some tasks by substring match against verbatim database text, so an agent that dumps the whole database passes, overestimating by 40%.
SWE-Lancer fails to isolate agents from ground truth, allowing 100% scores without solving anything.
KernelBench overestimates kernel correctness by ~31% because its fuzzer varies tensor values but not shapes or memory layouts.
WebArena's substring matching ignores extraneous content and its LLM judge accepts empty replies as correct for "N/A" tasks, over-estimating by 1.4-5.2%.
Seven of ten benchmarks had outcome-validity flaws, seven had task-validity flaws, and *all ten* had result-reporting limitations; 80% failed to acknowledge weaknesses in their own design.

**The checklist itself is directly reusable.**
ABC's three sections are task validity, outcome validity, and benchmark reporting ([ABC.md](https://github.com/uiuc-kang-lab/agentic-benchmarks/blob/main/ABC.md), [PDF](https://uiuc-kang-lab.github.io/agentic-benchmarks/assets/checklist.pdf)).
The items AgentLab should treat as hard requirements: residual state fully cleared between runs; agent completely isolated from ground truth; each task verified solvable; an oracle solver that passes every task; a trivial-agent baseline (one that does nothing) reported alongside real results; statistical significance reported; and evidence of the judge's accuracy, self-consistency, and human agreement.
Their pilot-experiment heuristic is a cheap sanity check: if agents consistently fail easy tasks the tasks may be impossible; if agents only succeed on difficult tasks there is a shortcut.

**Continuously regenerated task suites are the documented answer to saturation.**
SWE-rebench runs a fully automated pipeline extracting executable SWE tasks from GitHub at scale, yielding 21,336 verified tasks from 3,468 repositories, with a rolling ~111-294 task benchmark window ([arXiv 2505.20411](https://arxiv.org/abs/2505.20411v2), [leaderboard](https://swe-rebench.com/)).
The decontamination mechanism is simple and copyable: track issue and PR creation dates against model release dates, and explicitly mark evaluations that include pre-release tasks.
Their leaderboard reports resolved rate with a standard error and cost per problem alongside pass@5, which is close to the reporting format AgentLab should adopt.
The acknowledged trade-off: automation reduces per-task curation quality, and their LLM-based install-instruction generation was validated on only 18 repositories.

**Environment memorization has its own fix: environment variability.**
DigiWorld instantiates five design principles (privileged verification, realistic environments, integrity-checked configurations, sandboxed execution, multifactorial variability) across 15 mobile apps with over 3.2 million verified unique configurations, precisely so that a recorded action replay cannot win ([arXiv 2605.08261](https://arxiv.org/html/2605.08261)).
Their per-app results show wide CIs even with thousands of rollouts, because app-level variance dominates the uncertainty budget.
The transferable idea for AgentLab: parameterize tasks so each trial draws a fresh configuration from a large space, rather than replaying one frozen instance.

**Even careful methodology has a precision floor.**
METR's own lead author notes their time-horizon error bars have historically been a factor of ~2 in each direction, and states plainly "I really have no idea whether Claude's 'true' time horizon is 3.5h or 6.5h" ([METR](https://metr.org/notes/2026-01-22-time-horizon-limitations/)).
He also notes errors are correlated across models so *relative* comparisons have smaller error bars than absolute estimates, which is the same argument as pairing.
Measuring 99%-reliability horizons would need ~300 highly diverse tasks per time bucket and under 1% label noise.
Useful calibration on how much precision is buyable at all.

### 2.6 Promotion-gate design

**A defensible ship rule is a conjunction, not a single p-value.**
Spotify's production decision rule: ship if and only if the treatment is significantly superior on at least one success metric, AND significantly non-inferior on all guardrail metrics, AND no success, guardrail, or deterioration metric shows evidence of deterioration ([Spotify Engineering](https://engineering.atspotify.com/2024/03/risk-aware-product-decisions-in-a-b-tests-with-multiple-metrics)).
Four metric types with four test types: success metrics get superiority tests, guardrail metrics get non-inferiority tests, deterioration metrics get inferiority tests, quality metrics get various tests.

**The multiplicity result is counterintuitive and important.**
You should *not* correct the false-positive rate for guardrail metrics, because requiring all of them to pass is not "multiple chances" ([Spotify](https://engineering.atspotify.com/2024/03/risk-aware-product-decisions-in-a-b-tests-with-multiple-metrics)).
You must correct alpha only for the number of *success* metrics.
But you must correct beta for the guardrails: if each guardrail is powered at 1-beta independently, the simultaneous probability that all pass collapses fast.
Their figure: with 5 independent guardrail metrics simultaneous power is well below 40%, and with 10 it is around 11%.
The fix is to power each metric at 1 - beta/(G+1) where G is the guardrail count, which guarantees the decision has at least the intended power under any covariance structure.

**Non-inferiority margins are both a design and an analysis parameter.**
Unlike an MDE (which only feeds power analysis), the non-inferiority margin enters the hypothesis test itself ([Spotify Confidence](https://confidence.spotify.com/docs/experiments/design/effect-sizes)).
Smaller margins require larger samples because it is harder to gather evidence that a metric stayed within a tighter tolerance.

**Peeking, if you must peek, needs a sequential correction.**
Group sequential tests with alpha spending (Lan-DeMets) let you spend alpha arbitrarily across peeks, save unspent alpha, and reduce exactly to a standard z-test if you never peek; they need a maximum sample size estimate up front ([Spotify](https://engineering.atspotify.com/2023/03/choosing-sequential-testing-framework-comparisons-and-discussions)).
Always-valid inference needs no sample-size estimate but has lower power because it implicitly corrects for infinitely many looks.
Spotify's simulation finds GST superior in power in most cases when the sample size can be estimated.
Sequential tests typically have lower power than fixed-sample tests and stopping early biases the effect estimate upward.
Classical boundary families are Pocock, O'Brien-Fleming, and Wang-Tsiatis with error-spending functions ([JDS review](https://jds-online.org/journal/JDS/article/1333/file/pdf)).
Interim stopping can be for benefit, for futility, or both.

**Non-inferiority plus superiority is exactly the shape AgentLab described.**
The user's framing ("non-inferiority on protected tasks + superiority on primary metric with alpha spending") matches Spotify's rule almost exactly.
The one refinement the literature adds is the beta correction, which is the part teams universally forget.

## 3. Recommended v0.1 methodology

### 3.1 Design: paired, two-stage, task-clustered

Run baseline and candidate on the *identical* task list, with the same seeds where the environment supports seeding, in the same time window.
Analyze at the per-task level: compute each config's mean score per task across repeats, then take the per-task difference, then do inference on those differences.
This is Miller Eq. 7 and it is the single highest-value decision in the whole design ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).

Three task splits, physically separated:
- **Dev split**: what you iterate against. Contaminated by definition. Never appears in a promotion decision.
- **Gate split**: touched only at promotion time. If anyone tuned against it, it is burned and must be regenerated.
- **Protected split**: safety and regression invariants. Deterministic assertions only. Never changes except by explicit review.

Cluster on task family, not task, when families share an environment, a fixture, or a source repository ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).
Under-clustering was worth up to 3x on real data and it always makes you overconfident.

Two-stage execution with a **futility-only interim**:
- Stage 1: 40% of the planned trials. Stop and reject the candidate if the paired delta point estimate is below zero and the upper CI bound is below the MDE.
- Stage 2: remaining 60%, run only for survivors. Full alpha spent here.

Stopping only for futility cannot inflate type-I error, so v0.1 needs no alpha-spending function at all.
This is a deliberate simplicity choice over group-sequential machinery ([Spotify](https://engineering.atspotify.com/2023/03/choosing-sequential-testing-framework-comparisons-and-discussions)); revisit if AgentLab later wants to stop early *for* benefit.

### 3.2 Metric definitions

Let T = number of tasks, R = repeats per task per config, c_i = successes on task i out of R.

| Metric | Definition | Claim it supports |
|---|---|---|
| `pass@1` | mean over tasks of c_i / R | capability, primary gate metric |
| `pass@k` | mean over tasks of 1 - C(R-c_i, k)/C(R, k) | optimistic bound, retry value |
| `pass^k` | mean over tasks of C(c_i, k)/C(R, k) | reliability, promotion co-gate |
| `ICC(1)` | between-task variance / total variance | is the win capability or luck |
| `cost_per_success` | total USD / number of successful trials | cost-controlled comparison |
| `unnecessary_action_rate` | fraction of trials failing `subset` trajectory match | process quality |
| `tool_error_rate` | tool calls returning errors / total tool calls | process quality |
| `recovery_rate` | trials that hit a tool error and still succeeded / trials that hit a tool error | robustness |

Set k = min(R, 5) for pass^k.
Report the full envelope (pass^k, pass@1, pass@k) on every experiment, always ([arXiv 2602.07150](https://arxiv.org/html/2602.07150)).
Report cost alongside accuracy as a Pareto pair, never accuracy alone ([arXiv 2407.01502](https://arxiv.org/abs/2407.01502)).

### 3.3 Minimum trial counts in the cost-feasible regime

Work the power formula with plausible agent-eval parameters.
Assume binary scoring at a base pass rate near 0.6, so total per-trial variance is about p(1-p) = 0.24.
Assume ICC of 0.4, in the middle of the measured 0.30-0.77 range ([arXiv 2512.06710](https://arxiv.org/html/2512.06710v1)), giving between-task variance 0.096 and within-task variance 0.144.
Assume the paired task-difficulty correlation rho between baseline and candidate is 0.8, higher than Miller's 0.3-0.7 cross-model range because the two arms are the *same agent* with one change.
Then omega^2 = 2 * 0.096 * (1 - rho) = 0.0384.
At alpha=0.05 two-sided and 80% power, (z + z)^2 = 7.85.

Resulting MDE from Miller Eq. 10:

| Tasks T | Repeats R | Trials (both arms) | MDE (pp) |
|---|---|---|---|
| 20 | 3 | 120 | 20.6 |
| 25 | 5 | 250 | 17.4 |
| 40 | 5 | 400 | 13.7 |
| 60 | 5 | 600 | 11.2 |
| 60 | 10 | 1200 | 10.2 |
| 100 | 5 | 1000 | 8.7 |

Read the two structural facts off that table.
Going from R=5 to R=10 at T=60 doubles the cost and buys 1 percentage point, because omega^2 does not shrink with R.
Going from T=25 to T=60 at fixed R=5 buys 6 percentage points.
**Task count is the binding constraint; repeats past 5 are close to wasted money.**

Concrete v0.1 defaults:
- **Screening run**: T=20, R=3, 120 trials. Purpose is killing obviously-broken candidates and estimating variance parameters, not deciding anything. Roughly $60-$150.
- **Gate run**: T=60, R=5, 600 trials. Detects ~11pp at 80% power. Roughly $300-$600 at $0.50-$1.00 per trial.
- **Minimum viable gate**: T=40, R=5, 400 trials, and label the claim honestly as "detects ~14pp."

Hard budget consequence, stated plainly: at $1,000 total, AgentLab can afford roughly **one or two full gate runs plus several screening runs**.
Do not promise 10-point resolution and a dozen experiments from the same budget; the arithmetic does not allow both.
Recalibrate the table from real pilot data as soon as the first screening run lands, because omega^2 and sigma^2 are the only inputs that matter and mine are illustrative.

Two cost multipliers to plan for.
tau-bench measured $0.38 per task for the agent plus $0.23 for the user simulator with gpt-4o ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045)); SWE-rebench currently shows $0.81 to $4.40 per problem for frontier configs ([leaderboard](https://swe-rebench.com/)).
Judge calls are additional. Route to a judge only what deterministic checks cannot decide, and use a smaller judge model where calibration allows, since a three-model panel of smaller models cost 7-8x less than a single GPT-4 judge ([arXiv 2404.18796](https://arxiv.org/abs/2404.18796)).

Enforce a per-trial `cost_limit` and `token_limit` so a runaway agent cannot eat the budget, as Inspect does per sample ([Inspect](https://github.com/UKGovernmentBEIS/inspect_ai)).

### 3.4 Evaluator stack, cheapest-first

Run evaluators in this order and short-circuit as soon as a verdict is determined.

1. **Deterministic assertions**: exit codes, tests, state diffs, schema validation. Zero cost, zero variance.
2. **Programmatic trajectory checks**: `strict` / `unordered` / `subset` / `superset` tool-sequence matching plus the subsequence partial-credit score ([agentevals](https://github.com/langchain-ai/agentevals), [AWS](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/ground-truth-evaluations.html)). Zero LLM cost. `subset` is the unnecessary-action detector.
3. **LLM judge**, rubric-based, binary per criterion, only for what steps 1 and 2 cannot decide.
4. **Human review**, on a sampled audit slice only.

Every task carries an oracle solution and a trivial-agent (do-nothing) baseline; a task where the do-nothing agent passes is a broken task, not an easy one ([ABC](https://arxiv.org/abs/2507.02825)).
Fully clear environment state between trials and verify the agent has no path to ground truth ([ABC items T.4, T.5](https://github.com/uiuc-kang-lab/agentic-benchmarks/blob/main/ABC.md)).

### 3.5 Judge setup rules

**Family separation (hard rule).**
The judge model family must differ from every model family used by either arm.
If baseline runs Claude and candidate runs GPT, the judge must be neither, because family bias distorts the *delta*, not just the level ([arXiv 2508.06709](https://arxiv.org/html/2508.06709v1)).
This is a deliberate departure from the practitioner view that same-family judging is fine when alignment is measured ([hamel.dev](https://hamel.dev/blog/posts/evals-faq/)); that view is correct for single-system quality metrics and wrong for A/B promotion decisions.

**Pinning.**
Pin dated model snapshots for judges, never floating aliases.
Record the judge model id, judge prompt hash, and rubric version in every experiment record.

**Calibration set.**
About 100 human-labeled traces per judge, balanced ~50 pass / ~50 fail, split train 10-20% / dev 40-45% / test 40-45% ([validate-evaluator](https://github.com/hamelsmu/evals-skills/blob/main/skills/validate-evaluator/SKILL.md)).
Labels from one trusted domain expert beat several outsourced annotators; if multiple annotators are needed, measure inter-annotator agreement with Cohen's kappa and resolve disagreements before proceeding ([hamel.dev](https://hamel.dev/blog/posts/evals-faq/)).
Iterate the judge prompt on dev; read the test set exactly once.
Ship threshold TPR > 0.90 and TNR > 0.90; block below 0.80 on either.
Report the 2x2 confusion matrix, not just a scalar, because every symmetric statistic hides strictness versus leniency ([arXiv 2606.00093](https://arxiv.org/html/2606.00093)).

**Bias correction.**
For absolute rate claims, apply Rogan-Gladen: theta_hat = (p_obs + TNR - 1) / (TPR + TNR - 1), with a bootstrap CI ([validate-evaluator](https://github.com/hamelsmu/evals-skills/blob/main/skills/validate-evaluator/SKILL.md), full CI treatment in [arXiv 2511.21140](https://arxiv.org/html/2511.21140)).
For the paired delta, report the uncorrected delta as primary and the corrected one as a sensitivity check, and state the assumption explicitly: correction cancels in a difference only if judge error rates are equal across arms, which family separation is what buys you.

**Jury, selectively.**
Use a three-judge panel from disjoint model families for protected-set criteria and any criterion where a single judge's dev TPR/TNR fell below 0.90 ([arXiv 2404.18796](https://arxiv.org/abs/2404.18796)).
Aggregate by independent scoring plus max-vote for binary criteria.
Do **not** implement the jury as debate; debate amplifies shared bias while independent scoring and meta-judging resist it ([ACL 2025.findings-emnlp.941](https://aclanthology.org/2025.findings-emnlp.941/)).

**Prompt hygiene.**
Binary pass/fail per rubric criterion with a written critique; no 1-5 scales ([hamel.dev](https://hamel.dev/blog/posts/llm-judge/)).
If any pairwise comparison is used, evaluate both orders and average, or mark order-conflicts as ties ([survey, arXiv 2411.15594](https://arxiv.org/html/2411.15594v6)).
Give the judge the same context a human reviewer would need, including metadata.

**Audit cadence.**
Human spot-check 10% of judged trials per gate run, stratified to oversample (a) trials where jury members disagreed and (b) trials whose score sits near the decision boundary.
Re-run the frozen calibration set as a CI regression test on every change to the judge prompt, judge model version, or rubric; fail the build if TPR or TNR drops below threshold.
Re-validate from scratch when the task distribution shifts materially, since criteria drift means the rubric itself moves ([hamel.dev](https://hamel.dev/blog/posts/llm-judge/)).

### 3.6 The promotion gate

Promote the candidate if and only if all five hold.

1. **Superiority on the primary metric.**
Paired delta on `pass@1` over the gate split, lower bound of the two-sided (1 - alpha_success) CI strictly greater than zero.
Use exactly **one** primary metric in v0.1 so alpha_success = alpha = 0.05 and no multiplicity correction is needed.

2. **Non-inferiority on every protected metric.**
For each protected metric g, lower bound of the paired CI must exceed -NIM_g.
Suggested defaults: NIM = 0 for hard safety invariants tested by deterministic assertions, NIM = 5pp for softer protected behaviors.
Do **not** correct alpha across guardrails ([Spotify](https://engineering.atspotify.com/2024/03/risk-aware-product-decisions-in-a-b-tests-with-multiple-metrics)).

3. **Reliability co-gate.**
Paired delta on `pass^k` (k = min(R,5)) must be non-inferior at NIM = 0.
This is what stops AgentLab promoting a config that raises average success while becoming flakier, which is the exact failure tau-bench was built to expose ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045)).

4. **No deterioration anywhere.**
Every reported metric, primary and secondary, gets an inferiority test at alpha_deterioration = 0.05.
Any significant deterioration blocks promotion regardless of the primary result.

5. **Cost guardrail.**
`cost_per_success` non-inferior at a declared margin (suggest 20%).
A candidate that buys 3 points of accuracy by tripling spend is not an improvement ([arXiv 2407.01502](https://arxiv.org/abs/2407.01502)).

**Power correction (the part everyone forgets).**
Power each individual test at 1 - beta/(G+1), where G is the number of guardrail plus co-gate plus cost tests ([Spotify](https://engineering.atspotify.com/2024/03/risk-aware-product-decisions-in-a-b-tests-with-multiple-metrics)).
With the gate above, G = 3 to 5, so at a target decision power of 80% each test should be powered at roughly 1 - 0.20/5 = 96%.
That raises the required task count materially and must be reflected in the plan, not discovered afterwards.

**Statistical machinery, small enough for one Python module.**
- Per-task mean scores per config, then per-task paired differences.
- Primary CI: paired t interval on the per-task differences, t_{T-1} not z, since T is 40-60 not thousands.
- Cross-check CI: hierarchical bootstrap resampling task families, then tasks, then runs, B=2000 ([METR](https://metr.org/blog/2025-03-19-measuring-ai-ability-to-complete-long-tasks/), [arXiv 2605.08261](https://arxiv.org/html/2605.08261)). If the two disagree materially, trust the bootstrap and investigate clustering.
- Per-task binary pass rates for flakiness flagging: Wilson intervals, never Wald ([arXiv 2605.08261](https://arxiv.org/html/2605.08261)).
- pass@k and pass^k CIs: hierarchical bootstrap only, since partial-credit multi-run scores are not binomial ([agentrel](https://pypi.org/project/agentrel/)).
- Variance components and ICC(1) from one-way random-effects decomposition, reported on every run.
- Non-inferiority test: same paired interval, compared against -NIM instead of 0.

Total surface area: roughly 200-300 lines. `agentrel` implements most of it already and is worth evaluating as a dependency before writing it ([PyPI](https://pypi.org/project/agentrel/)).

**Always report, on every experiment:**
task count, cluster count, repeats per task, total trials, total USD, pass@1 with SE, pass^k, pass@k, ICC(1), the paired delta with CI, the paired task-score correlation, the judge model id and its TPR/TNR, the fraction of trials decided by deterministic checks versus judge, and the trivial-agent baseline score.
Miller's suggested table format (mean with SE in parentheses, question count, cluster count, plus pairwise differences and correlations) is a good template ([arXiv 2411.00640](https://arxiv.org/html/2411.00640)).

### 3.7 Task-suite hygiene

Run the ABC checklist against the suite before the first gate run ([ABC.md](https://github.com/uiuc-kang-lab/agentic-benchmarks/blob/main/ABC.md)).
Budget for a double-digit broken-task rate; OpenAI's audits found 59.4% of a hard SWE-bench Verified subset and ~30% of SWE-Bench Pro materially broken ([OpenAI](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/), [OpenAI](https://openai.com/index/separating-signal-from-noise-coding-evaluations/)).

Contamination probe, cheap and mandatory for any borrowed suite: strip context until the task should be logically unsolvable, and check whether models still perform well above chance ([Microsoft Research](https://www.microsoft.com/en-us/research/publication/the-swe-bench-illusion-when-state-of-the-art-llms-remember-instead-of-reason/)).
Track task creation date against model release date and mark potentially contaminated results explicitly, as SWE-rebench does ([arXiv 2505.20411](https://arxiv.org/abs/2505.20411v2)).

Parameterize tasks so each trial draws a fresh configuration from a large space rather than replaying one frozen instance, which is what defeats replay-style shortcuts ([arXiv 2605.08261](https://arxiv.org/html/2605.08261)).
Rotate a fraction of the gate split each cycle from a generator; keep the protected split frozen so regressions stay comparable over time.
Freeze external dependencies; ABC explicitly warns against live websites ([ABC item T.6](https://arxiv.org/abs/2507.02825)).

## 4. Open questions

1. **The variance parameters are unknown until AgentLab measures them.**
Every number in section 3.3 is illustrative, derived from omega^2 and sigma^2 values I assumed from published ICC ranges.
The first screening run should be treated primarily as a variance-estimation exercise, and the trial-count table regenerated from it.
Until then, treat "60 tasks by 5 repeats detects 11pp" as an order-of-magnitude claim, not a promise.

2. **Is the paired task-difficulty correlation actually high for config changes?**
Miller measured 0.3-0.7 across different frontier models.
I assumed 0.8 for two configs of the same agent, which is plausible but unverified.
If it turns out to be 0.5, required task counts roughly double.
This single number deserves its own measurement in the first pilot.

3. **User-simulator stochasticity may be a third variance level.**
tau-bench-style evaluation puts an LLM on both sides of the conversation, so trial-to-trial variance has an agent component and a simulated-user component ([arXiv 2406.12045](https://arxiv.org/abs/2406.12045)).
Whether to model this as a third bootstrap level, or fix user-simulator seeds and treat the simulator as part of the environment, is unresolved.
Fixing seeds is cheaper but narrows the claim to "under these user behaviors."

4. **Partial-credit versus binary scoring.**
Binary loses information but makes pass^k and Wilson intervals directly applicable.
Partial credit is more sensitive but breaks the binomial machinery and forces bootstrap everywhere.
METR binarizes continuous scores at a human-performance threshold ([arXiv 2503.14499](https://arxiv.org/html/2503.14499v1)); that is probably the right default but the threshold choice is itself a researcher degree of freedom.

5. **Judge cost as a share of budget is unmodeled.**
The trial-cost figures I cite are agent-side. If judging costs 20-30% on top, the feasible task counts drop accordingly.
Needs measurement in the first pilot.

6. **Whether family separation is worth its cost in every case.**
Family separation may force a weaker judge model, and a weaker but unbiased judge can be worse than a stronger biased one when the bias is small relative to the noise.
The honest resolution is empirical: measure TPR/TNR for both the same-family and cross-family judge on the calibration set, and pick on measured alignment, with cross-family winning ties.

7. **Bayesian hierarchical modeling as a v0.2 upgrade.**
HiBayES can provide evidence *for* equivalence, which is exactly what a non-inferiority gate wants, and stays stable under 20 observations per cluster ([arXiv 2505.05602](https://arxiv.org/html/2505.05602)).
The frequentist recipe above is recommended for v0.1 on simplicity grounds only.

8. **How aggressively to rotate the gate split.**
Rotating too fast destroys longitudinal comparability across experiments; rotating too slowly invites overfitting.
No source I found gives a principled rotation rate.

9. **Unverified.**
Cost-per-trial figures for AgentLab's own workloads are unknown; the $0.50-$1.00 range used in section 3.3 is extrapolated from tau-bench and SWE-rebench published costs, which may not transfer.
The claim that repeats past 5 are "close to wasted" holds under the assumed ICC of 0.4 and will shift if AgentLab's tasks are much noisier.
