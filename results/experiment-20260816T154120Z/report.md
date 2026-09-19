# AgentLab experiment report

## Verdict

**PROMOTE**

## Hypothesis

The 'codes_first' compaction style retains more of the planted per-fact information across the compaction boundary than the 'structured' style does.

## Configuration

- Model: `bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0`
- Baseline arm: `structured`
- Candidate arm: `codes_first`
- Repeats per task: 3
- Tasks: 15
- Seed list: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14]

## Per-arm recall

- Baseline recall: 0.6315
- Candidate recall: 0.7722

## Paired result

- Mean delta (candidate minus baseline): 0.1407
- 95% confidence interval: [0.0196, 0.2619]
- Tasks in this analysis: 15
- Standard deviation of per-task delta: 0.2188

## Cost and tokens

- Total cost: $1.35
- Total tokens: 1182393

## Replay

Inspect eval logs for this experiment:
- `/path/to/agentlab/results/experiment-20260816T154120Z/logs/2026-08-16T15-41-21-00-00_compaction-task_fYkw2D9J79fmoJLRPXpQd4.eval`
- `/path/to/agentlab/results/experiment-20260816T154120Z/logs/2026-08-16T15-43-03-00-00_compaction-task_4MpvR3nNDBMQUN2fz2LCrm.eval`
