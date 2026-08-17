# v0.3: the daytime lab (scope draft, awaiting ratification)

Status: DRAFT for Daniel's ratification; the full task-level plan gets written after scope sign-off.
Motto served: outsource the thinking, never the understanding.

## What it is

The lab runs itself through the day and Daniel receives findings, not chores.
An EventBridge Scheduler fires twice a day: 09:30 and 12:00 Amsterdam time (Daniel's correction, 2026-08-17).
Each firing launches a proposer task on the existing Fargate fabric.
The proposer reads fresh sources (releases, papers, harness repos), writes proposals to the proposal ledger, and submits ONLY pre-registered work: tournament re-runs, approved entrants, new-model intake through existing suites.
Anything with a new hypothesis waits in the ledger for Daniel's one-word ratification.
Results flow to a plain digest email via SNS. Nothing touches or integrates with Daniel's existing Hermes agent; email is simply a channel his tools already read.
Video explainers render in the house style (mechanism-first with real artifacts, the v2 cut is the reference) on demand, locally at first.

## Governance (unchanged)

Autonomous: registered templates, re-runs, intake, floor checks.
Daniel-gated: new hypotheses, new entrants, promotions, any new spend class.
Anti-collapse rules apply to the proposer (ledger with rejected archive, distance statements, fresh-source anchoring, blind independence when multiple proposers run).

## New infrastructure (Claude builds; Daniel clicks around)

1. EventBridge Scheduler (three daytime cron schedules -> SQS submit messages or a proposer-task RunTask).
2. A proposer task definition (same image, new worker command `agentlab worker propose`).
3. SNS topic + email subscription for the digest.
4. Proposal ledger storage (reuse DynamoDB + S3; no new database).

## Budget

Proposer runs are Haiku-tier reading/writing: est. under $1/day.
Auto-submitted experiment runs stay inside per-tournament caps.
Monthly budget alarm ($50) already guards everything.

## Explicitly out of scope for v0.3

Expert-call outreach, TwinMind hookup, GPU/world-model work, in-cloud video rendering.
