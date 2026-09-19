# AgentLab experiment report

## Verdict

**PROMOTE**

## Hypothesis

Structured summarization retains more of the planted per-fact information across the compaction boundary than truncation does.

## Configuration

- Model: `bedrock/eu.amazon.nova-lite-v1:0`
- Baseline arm: `truncate`
- Candidate arm: `structured`
- Repeats per task: 5
- Tasks: 20
- Seed list: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19]

## Per-arm recall

- Baseline recall: 0.0000
- Candidate recall: 0.9575

## Paired result

- Mean delta (candidate minus baseline): 0.9575
- 95% confidence interval: [0.9275, 0.9875]
- Tasks in this analysis: 20
- Standard deviation of per-task delta: 0.0641

## Cost and tokens

- Total cost: $0.13
- Total tokens: 1338119

## Replay

Inspect eval logs for this experiment:
- `/path/to/agentlab/results/experiment-20260816T131253Z/logs/2026-08-16T13-12-55-00-00_compaction-task_kxW6jjL2fC2xY5HVVxMJ4S.eval`
- `/path/to/agentlab/results/experiment-20260816T131253Z/logs/2026-08-16T13-15-06-00-00_compaction-task_MgMaYwzKacnfKmwUKMrRjG.eval`
