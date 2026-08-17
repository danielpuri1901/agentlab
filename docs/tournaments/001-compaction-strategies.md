# Tournament 001: compaction strategies

Status: DRAFT, awaiting Daniel's ratification.
A tournament is a recurring, registered head-to-head measurement, not a one-shot experiment.
It re-runs when a new entrant appears (a new harness ships, a new strategy is published) and maintains a living leaderboard.
Motto this serves: outsource the thinking, never the understanding.

## Question

Which compaction strategy retains the most information under a fixed summary budget, measured on the same corpus, same seeds, same budget, same model?

## Entrants (round 1)

1. `truncate` - delete pre-boundary turns (floor reference, from experiment 000).
2. `naive` - "summarize concisely" (documented floor: ~0 identifier retention, finding 001-A).
3. `structured` - our general identifier-preserving prompt (001 baseline).
4. `codes_first` - our engineered prompt (001 winner).
5. `claude_code_style` - a faithful adaptation of Claude Code's documented compaction priorities: clear old tool outputs first, then summarize preserving objectives, key decisions, constraints, and current state.
Source: the public Claude Code documentation on auto-compact; the adaptation is a prompt in our shared style dict.
6. `deepseek_style` - a faithful adaptation of DeepSeek Harness's compaction backend (dsh-compaction-basic: token-pressure retention + summarization; the model-free tool-result pruning maps to our clear-first step).
Source: the public deepseek-harness repo; an agent reads packages/compaction/ and derives the prompt/policy; the adaptation and its source citations are recorded in the entrant's registration before it runs.

Entrant rule: every entrant is a style in the SINGLE shared code path (`_SUMMARY_INSTRUCTIONS_BY_STYLE` plus, where a strategy requires it, a documented pre-step like clear-tool-outputs); entrants differ ONLY in their registered strategy.
Adaptations are best-effort reconstructions from public sources, and the leaderboard labels them "-style" honestly; we measure the strategy as described, not the vendor's private implementation.

## Design

- Corpus: the 001 corpus (12 facts, 120 filler turns, summary budget 150), which has measured variance and no ceiling for these arms.
- Round-robin against a fixed reference arm (`codes_first`, the current champion): each entrant runs paired vs the champion on identical seeds; a new champion is crowned only by beating the incumbent with the standard gate (CI excludes zero).
- Tiers: full round on Nova Lite; champion-vs-runner-up confirmation on Haiku 4.5.
- Sizing: from 001's measured variance, 51 tasks x 5 repeats per pairing on Nova (~$0.54/pairing), 15 x 3 on Haiku for the confirmation (~$1.35).
- Round-1 budget cap: $8.00 total. Abort and surface if exceeded.

## Outputs

1. `results/tournaments/001/leaderboard.md` - the living table: entrant, recall, delta vs champion, CI, cost, date, source citation.
2. One ASD-STE-100 explainer paragraph per entrant: what the strategy does and why it wins or loses, written for the street-test standard.
3. Every pairing's full report and logs, replayable, as with all experiments.

## Re-run triggers

- A new harness or compaction paper ships a distinguishable strategy (agent proposes a new entrant registration; Daniel ratifies entrants, runs are then autonomous).
- A new model tier becomes the lab default (leaderboard gets a column, not a redesign).

## Threats to validity

- Style adaptations may misrepresent a vendor's true behavior; mitigated by citation-anchored registrations and honest "-style" labeling, not claimed as the vendor's numbers.
- The corpus is synthetic (planted codes in faker filler); rankings transfer better than absolute numbers, per 001's recorded caveats.
- Single task family so far; a second retention task (e.g. decisions/constraints recall rather than codes) is the natural round-2 hardening.
