# Experiment 001: compaction summary quality (respec)

Status: DRAFT, awaiting Daniel's ratification.
Pre-registered: this spec is written and committed before the experiment runs.
Supersedes the truncate-vs-structured comparison, which the 2026-08-16 pilot showed is degenerate (baseline recall ~0 by construction, candidate ~0.97; see `docs/phase0-synthesis.md` section 3).

## Hypothesis

Under a fixed summary length budget, a structured, identifier-preserving compaction prompt retains more planted facts across the compaction boundary than a naive "summarize the conversation" prompt, on the same model.

## Why this version is fundable and falsifiable

Both arms now genuinely try to preserve information; they differ only in prompt quality.
The literature claims large effects for compaction quality (6.5 to 18 points; see `docs/research/agent-improvement-literature.md`).
A null result here would itself be informative: it would say prompt engineering of the summarizer does not matter once a length budget binds, contradicting the claimed effects.

## Arms

- Baseline (`naive`): "Summarize the conversation so far concisely." with a summary budget of MAX_SUMMARY_TOKENS.
- Candidate (`structured`): the existing structured prompt (preserve exact identifiers, codes, and decisions) with the SAME summary budget.
Both arms compact at the same boundary (0.6) on identical seeds.

## Difficulty changes vs the pilot corpus (anti-ceiling)

The pilot showed Nova Lite at 97% recall on the current corpus, leaving no headroom.
Three knobs, applied together:

1. `filler_turns`: 40 -> 120 (more haystack).
2. `n_facts`: 12 -> 24 (more needles, same pre-boundary planting).
3. `MAX_SUMMARY_TOKENS = 150` for BOTH arms (the binding constraint: 24 facts cannot all fit verbatim in 150 tokens without triage, so summary quality must matter or the hypothesis is false).

A screening pilot validates that these land both arms off ceiling (< 0.9) and off floor (> 0.1); if not, adjust knobs and re-register before the sized run.

### Amendment 1 (2026-08-16, after screening pilot 1)

Screening pilot 1 (10 tasks x 3 repeats, Nova Lite, 24 facts / 120 turns / budget 150, $0.12) FAILED the floor gate: naive recall 0.003, structured recall 0.136.
Root cause is capacity, not summarizer quality: 24 facts x ~9 tokens of exact codes is ~220 tokens, which cannot fit in a 150-token budget even with perfect triage, so both arms are crushed and the delta degenerates toward "any preservation vs none".
Amended knobs: `n_facts` 24 -> 12 (about 110 tokens of codes against the 150-token budget, a ~1.4x pressure ratio that forces triage but is physically satisfiable), `filler_turns` stays 120, budget stays 150.
The sized run remains blocked until a screening pilot passes both gates.

## Design

- Paired: both arms on identical task seeds, per-task deltas, clustered by task.
- Two-stage:
  1. Screening pilot: 10 tasks x 3 repeats, Nova Lite. Expected cost < $0.10. Checks ceiling/floor and measures sd_task_delta for this corpus.
  2. Sized run: task count = `required_tasks(measured sd, MDE = 0.10)` with a floor of 10 tasks, 5 repeats, Nova Lite.
- Confirmation pass (only if the sized run returns PROMOTE): same design at the sized n on Haiku 4.5.
Sonnet 4.6 confirmation is deferred until a finding survives Haiku (Claude 5 family is sales-gated on this account).

## Metrics and gate

- Primary: recall delta (candidate minus baseline), MDE 0.10, alpha 0.05, power 0.8.
- Protected: cost per run; allowance = +50% over baseline arm cost (the structured prompt is longer; it must not double cost).
- Secondary (recorded, not gated): actual summary token counts per arm; per-task recall distributions.
- Verdict: existing gate (PROMOTE / HOLD / REJECT / INCONCLUSIVE); INCONCLUSIVE triggers one re-sizing from the measured sd, then stands.

## Budget cap

$5.00 total across screening, sized run, and Haiku confirmation.
Abort and surface if the running total would exceed it.

## Implementation deltas required (small)

1. `compact_transcript`/solver: new style `naive`; both summary styles gain a `max_tokens` summary budget (GenerateConfig on the summarization call).
2. `generate_session` already takes `n_facts`/`filler_turns`; thread them through `compaction_dataset` and the CLI (`--filler-turns`, `--n-facts`, `--summary-budget`).
3. CLI `run`: `--baseline-style naive --candidate-style structured` (default stays current behavior for reproducibility of experiment 000).
4. Tests: naive-style unit tests mirroring the structured ones; a test that the summary budget is passed to the generate call.

## Threats to validity (named at registration)

- Fact-plant phrasing is formulaic ("note for the record, X resolved with CODE"), which may favor any summarizer that learns the pattern; acceptable for 001, flagged for a future perturbation family.
- Recall is exact-substring; paraphrased retention scores zero in both arms (symmetric, but understates absolute retention).
- Single model family at screening (Nova); the Haiku confirmation checks generalization one tier up only.

### Amendment 2 (2026-08-16, model-tier diagnostic)

Daniel hypothesized the naive floor was model-bound; tested with a Haiku 4.5 screening (10 tasks x 3 repeats, 12 facts / 120 turns / budget 150, $0.97).
Result: naive recall 0.019 (floor still violated), structured recall 0.644 (vs 0.18 on Nova Lite), sd_task_delta 0.020.
Conclusion: the naive floor is robust across tiers (finding 001-A: generic "summarize concisely" prompts discard 98-100% of exact identifiers regardless of model tier); model strength greatly improves the structured arm instead.
Cumulative 001 spend: $1.27 of the $5.00 cap.

### Amendment 3 (2026-08-16, the pivot - re-registered arms)

Two screenings plus the tier diagnostic show the naive arm cannot pass the floor gate at any affordable tier, so the naive-vs-structured hypothesis is retired in favor of the gradation question.
Re-registered hypothesis: a compaction prompt engineered to spend its budget on identifiers FIRST (codes_first) retains more planted facts than the current general structured prompt, under the same budget.
- Baseline (`structured`): the existing identifier-preserving prompt (Nova 0.18, Haiku 0.64 at 12 facts / 120 turns / budget 150).
- Candidate (`codes_first`): list every exact identifier and code verbatim first, one per line; summarize the rest only if budget remains; never paraphrase codes.
Same corpus knobs, same gates (both arms in (0.1, 0.9) at screening), same MDE 0.10, same $5.00 cumulative cap ($1.27 spent).
Screening on Nova Lite; sized run on Nova Lite; confirmation on Haiku 4.5 if PROMOTE.
Finding 001-A (naive discards ~all identifiers cross-tier) stands as a recorded result of the retired arms.

### Amendment 4 (2026-08-16, sized-run result and confirmation sizing)

Sized run (51 tasks x 5 repeats, Nova Lite, $0.54): PROMOTE.
codes_first 0.423 vs structured 0.155; delta +0.268, 95% CI [0.207, 0.329]; sd_task_delta 0.218.
Confirmation-sizing ruling: copying Nova's n to Haiku would cost ~$10 and breach the cap; Haiku's measured task noise is far smaller (screening sd 0.020), so the confirmation runs 15 tasks x 3 repeats (~$1.5), decisive if the effect transfers.
Known report blemish fixed after this run: the report's hypothesis line was a stale hard-coded constant; now derived from the actual arms.
