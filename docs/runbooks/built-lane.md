# Built lane runbook

The built track sends two lesson videos a day.
The `agentlab-built-lessons` schedule runs it at 10:30 Amsterdam time.
Each one teaches one topic from a study map of one of Daniel's projects.
Design: `docs/superpowers/specs/2026-10-05-built-lane-design.md`.

## Add a project

1. Write the project's study guide at `<project>/docs/study-guide.md`.
   Use the table format of the existing guides: `| **N. Topic** | What to study | Where it appears |`, in prerequisite order.
2. Check the packs locally first.
   This touches no AWS.
   ```
   uv run python scripts/export_study_topics.py <project> --slug <slug> --title "<Title>" --dry-run /tmp/topics-check
   ```
   Read two packs under `/tmp/topics-check/topics/<slug>/`.
3. Upload.
   The packs hold private code, so they go only to the private results bucket.
   ```
   AWS_PROFILE=agentlab RESULTS_BUCKET=agentlab-results-891377302765 \
     uv run python scripts/export_study_topics.py <project> --slug <slug> --title "<Title>"
   ```
   A new project goes to the end of the backlog.
   Exporting a project again replaces its topics in place, and already-sent topics stay sent.

## The first backlog (2026-10-05)

Two projects with 35 topics in all, exported in backlog order with the upload command above.
A project with a second repository, for example a separate proof checker, adds it with `--extra-dir <dir>`.

## Check one video in the cloud

After a deploy, run the explain task once with `TRACK=built`.
It sends the next two unsent topics.

## When it stops

When every topic has been sent, the track pings once: "The built track is out of topics."
Export the next project to start it again.
Before the first export there is no backlog, and the track stays quiet.
