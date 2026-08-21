# Experiment 005: does checkpointing cut the cost of crash recovery in graph workflows

Status: PRE-REGISTERED 2026-08-21, REFRAMED from the approved proposal "Meta-Agent Orchestration for Complex Discovery" (Daniel tap-approved 2026-08-20).
Full derivation: docs/deep-dives/2026-08-21-meta-agent-orchestration.md (arXiv:2608.19047).

## The reframe, stated honestly

The approved proposal claimed the paper tests LangGraph checkpoint recovery.
It does not: the word "checkpoint" never appears in the paper, and it does not use LangGraph (verified against the full fetched text, 2026-08-21).
The proposer hallucinated the connection around a real citation; this experiment keeps the engineering question because it is worth answering on its own, and drops the false replication claim.
Registered lesson applied: the proposer prompt now requires claimed connections to appear in the cited abstract.

## Hypothesis

For a LangGraph StateGraph decomposing a task into a fixed DAG of 8-12 exactly-checkable sub-steps, adding a persistent checkpointer reduces total completion cost (tokens including crash and resume) after a simulated mid-run crash, with identical final answers.

## Arms (paired on identical task seeds and identical crash point)

- Baseline `no_checkpoint`: crash restarts the graph from START; finished sub-steps re-execute.
- Candidate `checkpointed`: identical graph with a persistent checkpointer and fixed thread_id; resume executes only remaining sub-steps.

## Metric and gate

Primary: paired per-task delta in total completion tokens (candidate minus baseline; expected negative).
Protected hard assertion (not statistical): final-answer correctness identical between arms; any divergence is a bug and fails the run.
Standard gate on the cost delta.

## Implementation deltas and dependency note

Reuses the experiment 002 seeded arithmetic generator reshaped into a dependency DAG.
Requires adding LangGraph to the worker image: a NEW dependency class, flagged for a tap before build (governance: new spend/dependency classes are Daniel-gated).
Queue position: after 002/003 (shares the generator built there).
