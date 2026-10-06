# Built Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two lesson videos a day that teach the concepts behind Daniel's own projects, one study-map topic per video.

**Architecture:** A laptop script exports one pack per topic to the private results bucket and keeps an ordered backlog there. A new `built` track in the daily run takes the next unseen topic, deep-reads its pack with a lesson prompt, and renders it with the 3Blue1Brown-style generator.

**Tech Stack:** Python 3.12, boto3, pytest with moto, Terraform.

**Spec:** `docs/superpowers/specs/2026-10-05-built-lane-design.md`

## Global Constraints

- Topic material never enters this public repository: packs and the backlog live only in `s3://<results bucket>/topics/`.
- The paper deep-read prompt (`DEEP_READ_SYSTEM`) stays byte for byte the same.
- No em dash anywhere. Plain English, short sentences.
- Stacked on branch `3b1b-style-videos`; this branch is `built-lane`.

## Review Focus

1. Code in a pack is full of `<` and `>`. The deep read parses HTML, so the pack is escaped first. Pinned by the worker test that reads a pack with `x < y`.
2. Before any export there is no backlog object: the track must answer `empty` without a ping. Pinned by the quiet-empty worker test.
3. Re-exporting a project must not duplicate or reorder other projects. Pinned by the merge test.
4. Topic identities are exact (`topic:<project>/<slug>`); two similar titles must never count as the same topic. Pinned by the identity used in the worker test.
5. A changed deep-read prompt must not reuse a deep read saved under the old prompt. The deep-read fingerprint includes the system prompt.

---

### Task 1: Lesson deep read

**Files:** `src/agentlab/scene_plan.py`, `tests/test_scene_plan.py`

- `_PLAN_RULES` is the tail of `_DEEP_READ_MAIN` from "Write in plain language" on, so the paper prompt is unchanged.
- `LESSON_READ_SYSTEM = _LESSON_READ_INTRO + _PLAN_RULES + _EXAMPLE_BLOCK`.
- `deep_read(url, fetch_text, complete, model=..., system=DEEP_READ_SYSTEM)`.
- Tests: the lesson prompt starts with its own opening, contains `_PLAN_RULES` and "anchor idea", and lacks "what the paper shows"; `deep_read` sends the system prompt it is given.

### Task 2: Storyboard note for lessons

**Files:** `src/agentlab/storyboard.py`, `src/agentlab/story_video.py`, `tests/test_storyboard.py`, `tests/test_story_video.py`

- `LESSON_NOTE`, appended to the user prompt when `subject == "lesson"`.
- `build_storyboard_prompt(..., subject="paper")`, `design_storyboard(..., subject="paper")`, `compose_story_video(..., subject="paper")` passes it through.
- Tests: the note appears only for lessons; `compose_story_video(subject="lesson")` reaches `design_storyboard`.

### Task 3: Export script

**Files:** `scripts/export_study_topics.py`, `tests/test_export_study_topics.py`

- `parse_topics(guide_text)` reads rows like `| **1. Name** |` and `| **1\. Name** |`.
- `build_pack(...)`: topic row, then the files the row names in backticks, then the whole guide, then the guide's entry-point files; 12,000 characters per file, 120,000 per pack; HTML through `extract_visible_text`.
- `merge_backlog(backlog, slug, entries)`: replace a project's entries in place, else append.
- `export(...)` uploads packs and the merged backlog, or writes them to `--dry-run DIR`.
- Tests: both guide formats parse; a pack holds the row, a backtick file from the extra dir, and the guide; caps hold; merge appends and replaces in place; dry run writes files.

### Task 4: The `built` track

**Files:** `src/agentlab/worker.py`, `tests/test_worker.py`

- `EXPLAIN_TRACKS` gains `built`; `BUILT_VIDEOS_PER_DAY = 2`; the explain loop runs `built` twice and keeps one status per run.
- `_load_topic_backlog`, `_next_unseen_topic`, `_topic_pack_page` (escaped HTML), `_warn_topics_exhausted` (only when a backlog exists).
- `_run_explain_track`: the `built` branch picks a topic; identity from the candidate; lesson system prompt and pack fetch for the deep read; `subject="lesson"` for the story; the deep-read fingerprint includes the system prompt.
- Tests: two topics sent in order with the lesson prompt and subject; a pack with `x < y` reaches the deep read intact; no backlog means no ping; an exhausted backlog pings once; the summary line lists both built runs.

### Task 5: IAM and runbook

**Files:** `infra/iam.tf`, `docs/runbooks/built-lane.md`

- `VideoArtifacts` gains `"${aws_s3_bucket.results.arn}/topics/*"`.
- The runbook gives the export commands for both projects, the dry run, and the cloud check with `TRACK=built`.

### Task 6: Export and real check (needs Daniel's OK for terraform apply)

- [ ] Dry-run both exports and read two packs.
- [ ] Upload both projects in backlog order.
- [ ] After the 3Blue1Brown generator is merged and deployed: `terraform apply`, then one cloud run with `TRACK=built`.
