# Experiment 002: reflection vs equal-token resampling

Status: RATIFIED in principle by Daniel 2026-08-17 ("whatever experiment 2 is, let's do it"); design below is pre-registered; he may veto before the sized run.
Pre-registered before any trial runs.

## Hypothesis

Under a MATCHED token budget, showing a model its own failed attempt with a self-critique step ("reflection") does not improve final-answer accuracy over simply sampling a fresh attempt.
Null result expected by the 2026 replication literature; the original Reflexion claims (+22pp) predict the opposite.
Either outcome is a finding.

## Task (new, offline-verifiable)

Seeded synthetic multi-step arithmetic word problems with exact integer answers (generator in the corpus module family: deterministic by seed, difficulty knobs = number of steps and value ranges).
Scoring is exact-match on the final integer: no judge, no ambiguity.
Difficulty target: single-attempt accuracy between 0.2 and 0.7 at screening (both arms off floor and ceiling), tuned by the step-count knob.

## Arms (differ ONLY in what fills the retry budget)

- Baseline (`resample`): attempt 1; if wrong by self-consistency signal or unconditionally on second turn, attempt 2 is a FRESH solve of the same problem; final answer = attempt 2's.
- Candidate (`reflect`): attempt 1; the model is shown its attempt and asked to critique it briefly, then produce attempt 2; critique tokens COUNT toward the same budget.
Token matching is the crux: both arms get the same total generation budget per problem; the reflection arm pays for its critique out of that budget.
Both arms run the identical two-attempt structure; only the content between attempts differs.

## Design

- Paired on identical problem seeds; per-problem deltas; standard gate (PROMOTE / REJECT / INCONCLUSIVE) with MDE 0.10.
- Screening: 10 problems x 3 repeats, Nova Lite, validity gates (both arms in (0.1, 0.9), token budgets verified matched within 5% in the logs).
- Sized run: n from measured variance, Nova Lite; Haiku 4.5 confirmation if the CI excludes zero either direction.
- Budget cap: $5.00 total.

## Threats to validity (registered)

- Arithmetic may not transfer to agentic tasks; this tests the mechanism in its cleanest measurable form first.
- The "unconditional second attempt" design measures reflection's value on both right and wrong attempt-1s; a wrong-only variant is a registered follow-up, not this experiment.
- Nova-tier reasoning may floor the task; the difficulty knob and screening gate handle it.

## Implementation deltas

Problem generator + solver styles (`resample`, `reflect`) + token-budget accounting per arm + exact-match scorer, as a second Inspect task family alongside compaction; CLI gains a `--task-family` option.
