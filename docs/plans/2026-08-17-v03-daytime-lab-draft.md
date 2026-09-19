# v0.3: the daytime lab (scope draft, awaiting ratification)

Status: RATIFIED by Daniel 2026-08-17. Full task-level plan to be written next session; build follows the standard reviewed loop.
Motto served: outsource the thinking, never the understanding.

## What it is

The lab runs itself through the day and Daniel receives findings, not chores.
An EventBridge Scheduler fires twice a day: 09:30 and 12:00 Amsterdam time (Daniel's correction, 2026-08-17).
Each firing launches a proposer task on the existing Fargate fabric.
The proposer reads fresh sources (releases, papers, harness repos), writes proposals to the proposal ledger, and submits ONLY pre-registered work: tournament re-runs, approved entrants, new-model intake through existing suites.
Anything with a new hypothesis waits in the ledger for Daniel's one-word ratification.
Results flow to a plain digest email via SNS. Nothing touches or integrates with Daniel's existing Hermes agent; email is simply a channel his tools already read.
Video explainers render in the house style (mechanism-first with real artifacts, the v2 cut is the reference) on demand, locally at first.

## The product, concretely (what lands where, phone-first)

Every proposer run ends in ONE digest email to you@example.com.
The email body: 1-3 findings in STE (headline first), each with its numbers; a "pending your ratification" list; links.
Links point into the private GitHub repo, which renders leaderboards and plays mp4 videos in the GitHub mobile app - no laptop needed.
Charts ship as PNGs rendered by the worker (matplotlib is already in the image) and committed with the finding.
Videos: rendered in the house style when the finding deserves one; v0.3 renders them on Daniel's Mac when available, and an in-cloud manim image is the registered follow-up so videos become laptop-independent.
Ratifications in v0.3 happen by telling Claude in any session; email-reply approval is a registered follow-up (needs inbound-mail infra).
A proper lab site is trigger-listed: it gets built when Daniel first wants to SHARE a finding outside.

## Entrant and topic selection is researched, not hand-picked

Daniel's harness suggestions (Pydantic AI, Hermes, LangGraph...) are seeds, not commitments.
The proposer researches popularity and momentum with evidence (GitHub stars/velocity, release activity, discussion volume) and proposes rosters with citations.
Every proposal states its evidence; Daniel ratifies from evidence, not from vibes.

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
