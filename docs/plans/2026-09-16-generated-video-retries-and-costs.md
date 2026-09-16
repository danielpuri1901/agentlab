# Generated Video Retries and Costs Plan

**Goal:** Ship dynamic generated Manim videos with bounded repair attempts, prompt caching, and visible cost data.

**Spec:** `docs/specs/2026-09-16-generated-video-retries-and-costs.md`

## Tasks

- [x] Add measured cache-aware model usage and cost records with tests.
- [x] Add one Cost Explorer month-to-date query with tests.
- [x] Add four attempts, a total deadline, three phone-width samples per beat, and best-safe-candidate selection with tests.
- [x] Persist attempt evidence, model usage, and costs in S3 and DynamoDB with tests.
- [x] Add cost lines to Telegram captions with tests.
- [x] Add prompt cache controls to stable system messages with tests.
- [x] Run focused tests, the full test suite, lint, and formatting checks.
- [ ] Build and deploy the video image and Terraform changes.
- [ ] Run one production-like video and inspect its logs, ledger row, cost, and sampled frames.
