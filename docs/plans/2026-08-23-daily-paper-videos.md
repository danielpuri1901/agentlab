# Daily Paper Videos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task.

**Goal:** Three voiced, subtitled ~90s paper-explainer videos land on Daniel's Telegram daily (core + classic + novel tracks), with a full digest linked, rating buttons wired to the ledger, and deterministic dedup across all sources.

**Architecture:** Deterministic pipeline with exactly two model calls (pick, deep-read). New modules `papers_db`, `scene_plan`, `video_render`; new worker command `explain`; explore/exploit fetch pools in `sources.py`; `vid:` callback namespace in the approvals Lambda; a dedicated Manim/ffmpeg worker image and one new schedule.

**Tech Stack:** Python 3.12, uv, boto3 (DynamoDB/S3/Polly), httpx, Manim CE + ffmpeg (dedicated image; locally via uvx for tests), moto, Terraform aws ~>6.0.

**Spec:** docs/specs/2026-08-23-daily-paper-videos.md (binding).

## Global Constraints

- No em dash anywhere; STE style in all user-facing message text.
- Tests fully offline: moto for AWS, monkeypatched httpx/model calls; Manim render tests run locally via `uvx --python 3.12 manim` against a fixture plan, and are marked `@pytest.mark.render` (excluded from the default suite, run explicitly in their task).
- AWS clients passed as function parameters (worker.py convention).
- The model never writes animation code; the render path consumes validated plan dicts only.
- Deep-read model: bedrock/global.anthropic.claude-sonnet-4-6 (quality matters); pick model: Haiku 4.5. Both overridable via env.
- Every failure pings Daniel (reuse notify); silence must mean broken.
- Run `set -o pipefail && uv run pytest -q && uv run ruff check` before every commit. No co-author lines.
- Digests: S3 `digests/<paper_key>.md` + presigned URL (7 days) in the message (spec deviation, ruled 2026-08-23: workers cannot push to git).

## Task 1: `papers_db.py` - identity and the seen-store

**Files:** Create src/agentlab/papers_db.py, tests/test_papers_db.py.
**Interfaces (produces):**
- `paper_identity(url: str, title: str) -> str`: arXiv id (regex `\d{4}\.\d{4,5}` from any arxiv.org URL form, abs/pdf/html, with or without version suffix) prefixed `arxiv:`; else `title:` + normalized title (lowercase, alphanumerics and single spaces only).
- `is_seen(table, identity: str, fuzzy_titles: list[str] | None = None) -> bool`: exact item lookup on PK `seen_paper#<identity>` SK `paper`; if unseen and identity is title-based, also fuzzy-compare (difflib.SequenceMatcher ratio >= 0.92) against `fuzzy_titles` (caller passes recent seen titles).
- `mark_seen(table, identity, url, title, source, track) -> None` (put with first_seen ts, picked=True).
- `recent_seen_titles(table, limit=200) -> list[str]` (scan on sk="paper", newest first).
**Steps:** failing tests (identity extraction incl. abs/pdf/vN forms; normalize; fuzzy hit at 0.93 and miss at 0.85; moto roundtrip; unseen-then-seen) -> implement -> green -> commit "feat: paper identity and seen-store".

## Task 2: explore/exploit pools in `sources.py`

**Files:** Modify src/agentlab/sources.py, tests/test_sources.py.
**Interfaces (produces):**
- `gather_exploit(client=None) -> list[dict]`: exactly today's gather() behavior (rename; keep `gather` as alias for the proposer).
- `gather_explore(client=None) -> list[dict]`: (a) HN front page, NO keyword filter, `points >= 80`, each dict gains `"pool": "explore"`; (b) `fetch_hf_daily(client)`: GET `https://huggingface.co/api/daily_papers` (verify endpoint with one live curl in this task; if the shape differs from `[{paper: {id, title}, ...}]`, adapt and record in the report), normalized to source="hf", url `https://arxiv.org/abs/<id>`, title, upvotes. Fail-soft like every fetcher.
- Exploit dicts gain `"pool": "exploit"`.
**Steps:** live endpoint check -> failing tests (canned HF payload; unfiltered-HN threshold; pool tags; alias intact) -> implement -> green -> commit "feat: explore/exploit fetch pools with hf daily papers".

## Task 3: `scene_plan.py` - deep read to digest + validated plan

**Files:** Create src/agentlab/scene_plan.py, tests/test_scene_plan.py.
**Interfaces (produces):**
- `ScenePlan` pydantic model: title (<=70), one_line_claim (<=200), mechanism_steps: 3-6 of {label<=40, detail<=200, narration<=280}, key_numbers: 0-3 of {value<=20, meaning<=90}, limits_or_caveats (<=200), street_test_question (<=200), citation_url.
- `deep_read(url, fetch_text: Callable, complete: Callable) -> tuple[str, ScenePlan]`: fetch full text (callable injected; prod uses httpx+exa-style fetch of arxiv html), one completion producing BOTH the full digest markdown and, fenced at the end, the scene-plan JSON; split, validate (`parse_scene_plan(raw) -> ScenePlan | None` tolerant of fences), retry once on invalid JSON with the validation error appended.
- Grounding rule in the prompt verbatim: every number must appear in the source text; never attribute what the source does not state; the digest must include a "Limits" section.
- `pick_paper(candidates: list[dict], interests_text: str, complete: Callable) -> dict | None`: one Haiku call ranking candidates (title+source+pool lines), returns the chosen candidate dict by index; explore-day rule is applied by the CALLER (worker) by passing only the explore pool.
**Steps:** failing tests (schema bounds; parse from fenced/dirty output; retry path; pick returns valid index, garbage -> None) -> implement -> green -> commit "feat: scene plan schema, deep read, picker".

## Task 4: `video_render.py` - template, Polly, subtitles

**Files:** Create src/agentlab/video_render.py, src/agentlab/video_scenes.py (Manim template), tests/test_video_render.py, tests/fixtures/sample_plan.json.
**Interfaces (produces):**
- `narrate(polly_client, plan: ScenePlan, voice_id: str, out_dir) -> list[NarrationClip]` where NarrationClip = {path, seconds, text}; one clip per scene (title+claim, each mechanism step, numbers, caveat, question). Duration read from the mp3 (mutagen or ffprobe).
- `render_video(plan: ScenePlan, clips, out_path) -> Path`: runs Manim on video_scenes.py with the plan + per-scene durations injected via a JSON temp file; scenes sized to narration; house style: dark background, one accent color, step-by-step mechanism build with arrows; then ffmpeg concat/mux audio + burn subtitles from a generated .srt (timings from clip durations).
- `verify_voice(polly_client) -> str`: DescribeVoices, prefer a neural en-US/en-GB voice available in the region, else standard; return voice_id (this is the build-time Polly verification the spec requires).
- Offline tests: polly + ffprobe monkeypatched; srt generation exact; scene-duration math. RENDER test (marked `render`): render fixture plan WITHOUT audio at low quality via uvx manim, assert file exists, duration > 30s (ffprobe), no scene raises.
**Steps:** fixture plan -> offline failing tests -> implement narrate/srt/mux logic -> template scenes (implementer has visual judgment here; keep every text block inside safe margins, shrink-to-fit long labels) -> run the marked render test locally and attach the output path in the report for Daniel's eyeball -> green -> commit "feat: manim video template with polly voiceover and subtitles".

## Task 5: `worker explain` - the orchestrator

**Files:** Modify src/agentlab/worker.py, src/agentlab/notify.py, tests/test_worker.py, tests/test_notify.py. Create docs/interests.md (initial profile: agents, evals, RSI, compaction, memory, harnesses, verification; explicitly editable).
**Interfaces:**
- notify.py: `send_video(config, caption, video_path, buttons=None)` beside send_photo (multipart, sendVideo endpoint); `notify(...)` gains optional `video_path` param routed like photo_png (queued videos: store S3 key not bytes; flush re-downloads - keep simple: during quiet hours video pings store {text, video_s3_key, buttons} and flush sends via a fresh download to tmp).
- worker.py `explain_command()`: env STATE_TABLE, RESULTS_BUCKET, TRACK (core|classic|novel|all, default all). Flow per track: build candidate pool (core: exploit pool; novel: explore pool, picked on the novelty prompt 'newest coolest thing Daniel does not know yet'; classic: next unseen entry from docs/classics.json packaged into the image) -> dedup via papers_db -> pick -> mark_seen -> deep_read -> upload digest S3 + presign -> narrate+render -> upload video S3 -> send via notify with buttons [("IMPLEMENT","vid:<key>:implement"),("LEARNED","vid:<key>:learned"),("SKIP","vid:<key>:skip")] and caption: claim + street-test question + digest link -> write video# item. EVERY stage in try/except: on failure, ping the text fallback (digest link if it exists, else claim text, else error) and continue to next track; exit 0 unless both tracks crashed before any ping.
- docs/classics.json: THIS task creates it with 10 entries only (verified canonical: Attention Is All You Need 1706.03762 etc. - verify each id live with curl before writing); full curation to 60+ is a registered follow-up.
**Steps:** failing tests (three tracks with correct pools; happy path with all externals monkeypatched -> one telegram sendVideo + ledger items; render-failure -> text fallback ping; classic track advances) -> implement -> green -> commit "feat: worker explain - daily paper videos end to end".

## Task 6: Lambda `vid:` namespace + infra + deploy

**Files:** Modify infra/lambda/approvals_webhook.py, tests/test_approvals_webhook.py, infra/scheduler.tf, infra/ecs.tf, infra/iam.tf, infra/variables.tf. Create Dockerfile.video, scripts/build_and_push_video_image.sh (clone of existing script with -f Dockerfile.video, tag suffix "-video", repo same ECR).
**Interfaces:**
- Lambda: callback `vid:<paper_key>:<implement|learned|skip>` -> update video#<paper_key> item SET rating, rating_ts (conditional attribute_exists to ignore unknown keys); answer toast "Rated: <rating>"; edit message to strip buttons. prop:* untouched (tests prove both coexist).
- Dockerfile.video: FROM the existing image's base pattern + apt ffmpeg + manim deps (pangocairo etc.) + `uv sync` including a new `video` dependency group (manim, mutagen) kept OUT of the main image.
- ecs.tf: `explain` task definition (2 vCPU/4GB - render is CPU-bound) with image `<repo>:<tag>-video`, command ["worker","explain"]; iam: video task role = proposer grants + polly:SynthesizeSpeech + DescribeVoices on * (no resource-level support) + s3 Put/Get on digests/* and videos/* prefixes.
- scheduler.tf: `agentlab-explain` at cron(30 10 * * ? *) Europe/Amsterdam, proposer-style RunTask.
**Steps:** lambda tests -> lambda impl -> terraform fmt/validate -> build video image -> apply -> commit "feat: video pipeline infra - explain task, schedule, vid callbacks" -> E2E: run one explain task manually, Daniel receives the first video; record outcome in ledger.

## Task 7 (this week, after E2E): golden set + judge eval + Actions gate

Converter docs/golden-papers-labeling.md -> docs/golden-papers.jsonl; `judge_eval.py` scoring the pick prompt against it; `.github/workflows/judge-gate.yml` running it on PRs touching the pick prompt; Friday tuner agent registered as follow-up once labels accumulate. Detailed steps written after Tasks 1-6 ship.

## Self-Review Notes
Spec coverage: two pools T2, dedup T1, pick+deep-read T3, template+voice+subs T4, orchestration+fallbacks+interests T5, ratings+schedule+image T6, flywheel T7. Deviation ruled: digests to S3 not git. Classic list starts at 10 verified entries (speed over completeness, follow-up registered).
