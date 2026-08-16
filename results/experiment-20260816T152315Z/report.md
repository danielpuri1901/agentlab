# AgentLab experiment report

## Verdict

**PROMOTE**

## Hypothesis

Structured summarization retains more of the planted per-fact information across the compaction boundary than truncation does.

## Configuration

- Model: `bedrock/eu.amazon.nova-lite-v1:0`
- Baseline arm: `structured`
- Candidate arm: `codes_first`
- Repeats per task: 5
- Tasks: 51
- Seed list: [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50]

## Per-arm recall

- Baseline recall: 0.1552
- Candidate recall: 0.4232

## Paired result

- Mean delta (candidate minus baseline): 0.2680
- 95% confidence interval: [0.2068, 0.3292]
- Tasks in this analysis: 51
- Standard deviation of per-task delta: 0.2176

## Cost and tokens

- Total cost: $0.54
- Total tokens: 6205915

## Replay

Inspect eval logs for this experiment:
- `/Users/danielpuri/Desktop/Projects/agentlab/results/experiment-20260816T152315Z/logs/2026-08-16T15-23-16-00-00_compaction-task_cfPyTAqXGasF8MkBrfPyts.eval`
- `/Users/danielpuri/Desktop/Projects/agentlab/results/experiment-20260816T152315Z/logs/2026-08-16T15-31-49-00-00_compaction-task_7KAPVNkTfH45dqJXeLZnWw.eval`
