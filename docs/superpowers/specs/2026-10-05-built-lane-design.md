# Built Lane Design

Date: 2026-10-05.
Status: Daniel approved the design in chat on 2026-10-05.

## Goal

Daniel builds a lot with AI, and his technical understanding lags behind what he builds.
The built lane sends short lesson videos that teach the concepts behind his own projects.
Each video teaches one topic from a project's study map.
The videos use the 3Blue1Brown-style generator (`docs/superpowers/specs/2026-10-05-3b1b-style-videos-design.md`).

## Decisions (Daniel, 2026-10-05)

- Scope: one video per study-map topic.
- Depth: one anchor idea per video, the idea from the topic that the project depends on most, in about 2 to 3 minutes. The linked digest covers the rest of the topic and its exercise.
- Order inside a project: the study map's prerequisite order, as written.
- Backlog for the first weeks: two of Daniel's projects, first 15 topics, then 20. The order lives in the private backlog file.
- Cadence: 2 built videos per day, from the `agentlab-built-lessons` schedule at 10:30 Amsterdam time. Paper videos come only from approved proposals, so the old all-tracks daily schedule is gone.

## Why the source material is exported from the laptop

The video job runs in AWS Fargate and cannot see the laptop.
One project is not in git, and the other is a private repository.
agentlab is public, so topic material must not go into it.
So a laptop command builds one pack per topic and uploads it to the private results bucket.

## Design

### 1. Study maps

Each project has a study guide in its own repository at `docs/study-guide.md`.
One guide already existed.
The other was written on 2026-10-05 and saved in its own project.
A guide has a table with one row per topic: `| **N. Topic** | What to study | Where it appears |`.

### 2. Export (`scripts/export_study_topics.py`, laptop)

`uv run python scripts/export_study_topics.py PROJECT_DIR --slug SLUG --title TITLE [--extra-dir DIR] [--dry-run OUT_DIR]`

- It reads `PROJECT_DIR/docs/study-guide.md` and parses the topic rows.
- For each topic it writes one pack in plain text:
  - the topic, what to study, and where it appears;
  - the files that the topic row names in backticks, when they exist in `PROJECT_DIR` or `--extra-dir`;
  - the whole study guide;
  - the project files that the guide lists as entry points.
- Each file is cut at 12,000 characters and each pack at 200,000 characters. The deep read caps its input again.
- HTML files go through the same text extraction as web pages.
- Packs go to `s3://<results bucket>/topics/<slug>/<NN>-<topic slug>.md`.
- `topics/backlog.json` lists every topic in play order. A new project goes to the end. Exporting a project again replaces its entries in place.
- `--dry-run` writes the packs and the backlog to a local folder and touches no AWS.

### 3. The `built` track (`src/agentlab/worker.py`)

- `EXPLAIN_TRACKS` gains `built`. The daily run plays it twice (`BUILT_VIDEOS_PER_DAY = 2`).
- The picker takes the first backlog entry that is not in the seen store, the way `classic` takes the next classic.
- A topic's identity is `topic:<project slug>/<topic slug>`, exact, with no fuzzy title match.
- With no backlog object at all, the track answers `empty` and stays quiet.
- When every exported topic has been sent, the track pings once: "The built track is out of topics."
- The deep read fetches the pack from S3. The pack is wrapped as escaped HTML, so code with `<` and `>` survives text extraction.
- The Telegram caption starts with `[BUILT]` and carries the topic title and the project title.

### 4. Lesson deep read (`src/agentlab/scene_plan.py`)

`LESSON_READ_SYSTEM` replaces the paper opening of the deep-read prompt and keeps its scene-plan rules.
The digest sections, in order:
1. headline: the anchor idea in one sentence;
2. the idea in plain words;
3. why the project needed it;
4. how it works, opening with a `Components:` line;
5. where it lives in the project: files and functions from the pack;
6. a worked example with the project's real numbers;
7. the rest of the topic, in brief;
8. common confusion;
9. limits;
10. the exercise from the study map, when it has one.

The grounding rule stays: every number comes from the pack.
General knowledge about a concept is allowed; facts about Daniel's project come only from the pack.
`deep_read` gains a `system` parameter. The paper prompt does not change.

### 5. Storyboard note for lessons (`src/agentlab/storyboard.py`, `src/agentlab/story_video.py`)

`compose_story_video` and `design_storyboard` gain `subject="paper"` or `"lesson"`.
For a lesson, the storyboard prompt adds one paragraph:
- the video is a lesson about one idea from a project Daniel built, not a paper;
- the title beat names the idea;
- the problem beat shows what goes wrong in his project without it;
- the application beat shows where it lives in his code;
- the question beat is the exercise.

The system prompt, and so the checkpoint fingerprint, does not change.

### 6. Infrastructure

The explain task role may read `topics/*` in the results bucket (`infra/iam.tf`, `VideoArtifacts`).
This needs `terraform apply` from the laptop.

## Testing

- Export: parsing both study guides; pack contents and caps; backlog order and replacement; dry run.
- Worker: the built track sends the first unseen topic and advances; two videos per day; the lesson prompt and the lesson subject reach the deep read and the storyboard; no backlog means quiet `empty`; an exhausted backlog pings once.
- Deep read: the lesson system prompt is used when asked; the paper prompt is unchanged.
- Storyboard: the lesson paragraph appears only for lessons.
- Real path: export both projects, read two packs, then run one built video in the cloud with `TRACK=built` before the first daily run.

## Rollout

1. Merge after the 3Blue1Brown-style generator, because the lessons use it.
2. `terraform apply` for the IAM change.
3. Export both projects in backlog order.
4. One cloud run with `TRACK=built`.
5. The `agentlab-built-lessons` schedule then sends two built videos each day at 10:30.

## Out of scope

- Finding new projects by itself. Daniel or a session runs the export.
- Re-queueing a topic after an UNCLEAR rating.
- Reading files from git branches during export. The topic rows name some branch files; the docs on disk cover them for now.
