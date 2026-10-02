# Taste Flywheel Design

Date: 2026-10-02.
Branch: `taste-flywheel`, from `origin/main` at `f5a095a`.

## Goal

Every tap, rating, and silence from Daniel must make the next day's picks better.
Today the proposer has no memory of Daniel's taste, so proposals drift toward SDK release notes and the videos built from them are not worth watching.
This design adds a taste profile that the proposer and the video picker read every run, a weekly job that rewrites the profile from the ledger, and an eval gate that blocks a worse profile.

## Evidence from the ledger (scanned 2026-10-02)

- 94 proposals since 2026-08-20: 18 approved, 17 rejected, 59 never tapped.
- Proposals that cite GitHub release notes: 2 approved, 10 rejected.
- Proposals that cite arXiv: 12 approved, 3 rejected.
- 77 videos sent: 15 COOL, 3 MEH, 0 SKIP, 59 unrated.
- The core video track also produced three changelog videos (`langgraph==1.2.11`, `python/v1.56.0`, `harness-typescript/v0.1.1`). None were rated.
- The proposer invents experiment-style titles, so the Telegram message hides what the source is.
- An approved proposal builds a video that carries the URL as its title and no proposal id, so a video rating never reaches the proposal.
- `preferences.py` needs 30 ratings with 10 non-COOL before it does anything. Daniel does not tap SKIP. He goes silent.
- `docs/golden-papers.jsonl` does not exist. The worker reads it and silently gets nothing. Daniel's 21 labels sit unused in `docs/golden-papers-labeling.md`.

Daniel's rulings in this design session:

- A good pick is any of three lenses: a foundational paper he should know, a frontier paper on his core interests, or something he would implement in his own harness.
- Silence means "not interesting". An untapped proposal or an unrated video is a weak negative.

## Scope

In scope:

1. Deterministic source fixes: no GitHub release notes in any pool, wider arXiv categories, Hugging Face daily papers in the proposer pool.
2. One foundational slot per day from the classics list, with the classics list extended by Daniel's golden papers.
3. A versioned taste profile in S3 with a DynamoDB pointer, read by the proposer and the video picker.
4. Episode derivation from the ledger, including implicit negatives.
5. A weekly consolidation job that rewrites the profile with a model and gates the swap with a held-out eval.
6. Proposal and video items linked both ways.
7. A REVERT button on the consolidation ping.
8. The golden sheet converter and the committed `docs/golden-papers.jsonl`.

Out of scope:

- Embedding retrieval over labelled items.
- Reason buttons on verdicts.
- Video render quality.
- Re-enabling the daily video tracks.
- Any process that edits files in the repo at runtime.
- Replay eval over stored day records. The day records are written from day one so the eval can be built once 10 days exist.

## Architecture

Four kinds of memory, each with one owner:

| Memory | What it holds | Where it lives | Who writes it |
|---|---|---|---|
| Working | Today's sources, the profile text, the recent archive | The proposer prompt, rebuilt every run | `proposer.py` |
| Episodic | What Daniel did with each item | `proposal#` and `video#` items, golden jsonl | Lambda taps, explain task, golden converter |
| Semantic | What is true about Daniel's taste, with examples | `profile/<version>.md` in S3, pointer `profile#current` in DynamoDB | `worker consolidate` |
| Procedural | Lens definitions, source pools, label rules, gate rules | Prompts and code in the repo | Commits only |

Rule from Daniel's judge lessons: code computes facts, the model writes taste.
Counts, rates, source weights, the held-out split, and the swap decision are code.
The prefer and avoid sentences and the choice of examples are the model's.

## Data model

### Profile pointer

DynamoDB item on the existing `agentlab-state` table.

- `experiment_id`: `profile#current`, `sk`: `profile`.
- `version`: ISO timestamp compacted, for example `20261005T160000Z`.
- `s3_key`: `profile/<version>.md`.
- `previous_version`, `previous_s3_key`: the version before this one, or null.
- `applied_ts`: when the pointer was set.
- `source`: `consolidate`, `revert`, or `seed`.
- `eval`: JSON string with `old_f1`, `new_f1`, `old_golden_recall`, `new_golden_recall`, `held_out_count`.

Profile text is immutable in S3.
Every candidate the consolidator produces is written to `profile/candidates/<version>.md`, whether or not it is applied.
If the pointer is missing, readers fall back to the repo file `docs/interests.md`.

### Proposal item additions

- `title`: the real source title, truncated to 80 characters. No invented titles.
- `why`: one line, written for Daniel, 300 characters max.
- `lens`: `foundational`, `frontier`, or `implement`.
- `source_type`: `arxiv`, `hf`, `hn`, `blog`, or `classic`, derived by code from the URL host and the slot.
- `video_key`: set by the explain task after the video is sent, null until then.
- `profile_version`: the profile version the proposer used.

Existing fields stay: `headline` is written with the same text as `why` so old readers keep working, `distance` stays, `kind` stays `new_hypothesis`.

### Video item addition

- `pid`: the proposal id that started this video, or null for scheduled tracks.

### Day record

S3 object `proposals/days/<YYYY-MM-DD>/sources.json`:

```json
{
  "date": "2026-10-05",
  "profile_version": "20261004T160000Z",
  "candidates": [{"title": "...", "url": "...", "source": "arxiv", "pool": "exploit", "summary": "..."}],
  "chosen": [{"pid": "prop-...", "title": "...", "url": "...", "lens": "frontier"}]
}
```

### Episodes

An episode is one thing Daniel did with one item.
Episodes are derived at read time by `episodes.py`.
No status field is ever rewritten to encode silence.

| Source | Condition | Kind | Weight |
|---|---|---|---|
| proposal | status APPROVED | approved | +1.0 |
| proposal | status REJECTED | rejected | -1.0 |
| proposal | status PROPOSED and `created_ts` older than 48 hours | ignored | -0.5 |
| proposal | status PROPOSED and younger than 48 hours | none | no episode |
| video | rating COOL | cool | +1.0 |
| video | rating MEH | meh | -0.5 |
| video | rating SKIP | skip | -1.0 |
| video | no rating and `sent_ts` older than 72 hours | unrated | -0.5 |
| video | no rating and younger than 72 hours | none | no episode |
| golden | rating COOL | golden_yes | +1.0 |
| golden | rating MEH | golden_meh | -0.5 |
| golden | rating SKIP | golden_no | -1.0 |

Episodes older than 60 days get their weight multiplied by 0.5.
An approved proposal and the video it produced are two episodes.
The video episode carries the `pid` so the consolidator can see "approved on title, then rated MEH on content".
Each episode carries `title`, `url`, `source_type`, `kind`, `weight`, `ts`, `identity` (from `papers_db.paper_identity`), and `why` when the item has one.

`source_type` for a video item is `classic` when `track` is `classic`, else derived from the URL host.

## Daily proposer

### Pools

`sources.py` changes:

- `fetch_github_releases` and `TRACKED_REPOS` are deleted, with their tests.
- `fetch_arxiv` queries `cat:cs.CL OR cat:cs.AI OR cat:cs.LG OR cat:cs.MA`, 40 newest, same keyword filter.
- `gather_exploit` returns arXiv plus HN keyword hits. The core video track keeps using it.
- New `gather_proposer_pool` returns arXiv plus Hugging Face daily papers plus HN keyword hits, deduplicated by `paper_identity`, each tagged with `source`.
- `gather` stays as an alias, now of `gather_proposer_pool`.

### Slots

`DAILY_CAP` stays 3.
Slot 1 is the next foundational paper: the first `docs/classics.json` entry whose identity is not in the seen-papers store and whose title does not fuzzy-match any proposal title in the ledger.
It is filed by code with `lens` `foundational`, `source_type` `classic`, and `why` set to "Foundational paper from <year>. Everyone in the field builds on it."
When the classics list is exhausted, all 3 slots are fresh.
The remaining slots are fresh picks by the model.
A capped day still pings, as today.

### Prompt

The system prompt is procedural memory and lives in `proposer.py`:

- The job: choose what Daniel learns today, not experiments.
- The three lenses, each defined in one sentence.
- What Daniel rejects: changelogs, SDK release notes, product throughput posts, generic news.
- Keep the source's real title.
- One line why, specific to Daniel.
- Tag the lens.
- Cite exactly one URL from the list.
- Never claim what the source does not state (the 2026-08-21 rule stays).
- Output only a JSON array with keys `title`, `why`, `citation`, `distance`, `lens`.

The user message is working memory:

1. "Daniel's taste profile:" followed by the profile text.
2. "Fresh sources today:" one line per candidate with source tag, title, URL, and the first 200 characters of summary when present.
3. "Recent archive (do not repeat):" the last 20 proposals with their derived status, so an ignored proposal shows as `IGNORED`, not `PROPOSED`.
4. "Pick at most N."

`PROPOSER_MODEL` keeps its env override.
The Terraform variable `proposer_model` is set to the `agentlab-sonnet` application inference profile at deploy time.
The code default moves to `bedrock/global.anthropic.claude-sonnet-4-6`.

### Message

```
Today's lessons. Tap to decide.

1. [FOUNDATIONAL] Attention Is All You Need (classic)
Why: Foundational paper from 2017. Everyone in the field builds on it.
https://arxiv.org/abs/1706.03762

2. [FRONTIER] <real title> (arxiv)
Why: <one line for Daniel>
<url>
```

The source tag in parentheses is the item's `source_type`.
Buttons stay `APPROVE n` and `REJECT n` with callback `prop:<pid>:approve|reject`.

### Day record

After filing, the proposer writes the day record to S3 with every candidate it saw and the chosen proposals.

## Capture

### Approval builds a titled, linked video

`_build_video` in the Lambda adds two environment overrides: `PID` and `EXPLAIN_TITLE` set to the proposal's `title`.
`worker explain` already builds the forced candidate title from `EXPLAIN_TITLE`.
It now also reads `PID`, writes it on the video item as `pid`, and after the video is sent calls `proposals.set_video_key(table, pid, video_key)`.
A missing `PID` leaves both fields null, which is the scheduled-track case.

### Revert

New callback namespace `prof:<version>:revert`.
The Lambda reads the pointer.
If the pointer's `version` equals the callback version and `previous_s3_key` is set, it updates the pointer to the previous version with a condition on `version`, sets `source` to `revert`, and answers "Reverted to <previous_version>".
Otherwise it answers "Nothing to revert".
Taps from any other Telegram user are ignored, as for every other namespace.
The Lambda needs no new IAM permission: it already has `dynamodb:GetItem` and `dynamodb:UpdateItem` on the table.

### Silence

No new buttons.
Silence is derived by `episodes.py` from timestamps.

## Weekly consolidation

`worker consolidate` runs every Sunday at 18:00 Europe/Amsterdam on the proposer task definition.
It also accepts `--dry-run`, which prints the stats, the candidate profile, and the eval, and writes nothing and pings nothing.

Steps:

1. Scan the table. Load `docs/golden-papers.jsonl` from the image. Derive episodes.
2. Count episodes with `ts` later than the pointer's `applied_ts`. If a pointer exists and fewer than 5 are new, send the tally ping and stop. No model call.
3. Compute stats in code: per ISO week, approval rate = approved / (approved + rejected + ignored) and COOL rate = cool / (cool + meh + skip + unrated); per `source_type`, approval rate with counts; totals.
4. Split: an episode is held out when `sha1(identity)` modulo 10 is less than 3. The split is a property of the item and never changes.
   Golden episodes are always in the training set and are always probed as the protected set.
5. Load the current profile text, or the seed `docs/interests.md` when no pointer exists.
6. One model call with `CONSOLIDATE_MODEL` (default `bedrock/global.anthropic.claude-sonnet-4-6`).
   The system prompt gives the fixed output format and the rules: write about Daniel, cite evidence counts in each bullet, choose examples only from the episodes given, do not invent titles.
   The user message carries the current profile, the stats, and the training episodes grouped into positives and negatives, most recent first, capped at 150 lines.
7. Validate the output in code. See the profile format below. Replace the "Source weights" section with the code-computed one.
8. Probe the held-out set and the golden set with the old profile and the new profile. Decide the swap.
9. Write the candidate to `profile/candidates/<version>.md` always. On pass, also write `profile/<version>.md` and flip the pointer with the old version as previous.
10. Ping. The ping always goes out, pass or fail, because silence must never mean broken.
    It carries the week's stats, the F1 and golden recall before and after, the prefer and avoid bullets that changed, and on pass one REVERT button with callback `prof:<version>:revert`.

### Profile format

```
# Daniel's taste profile
version: <version>

## Prefer
- <sentence> (evidence: <n> approved, <m> COOL)

## Avoid
- <sentence> (evidence: <n> rejected, <m> ignored)

## Positive examples
- <title> (<kind>, <source_type>)

## Negative examples
- <title> (<kind>, <source_type>)

## Source weights
- arxiv: approval 0.80 (12 of 15)
```

Validation rules, all in code:

- All five sections present, in this order.
- At most 1200 words.
- At least 3 bullets under Prefer and 3 under Avoid.
- At most 10 examples per examples section. Each example title must fuzzy-match a training episode title with `SequenceMatcher` ratio 0.86 or higher, else the line is dropped. If fewer than 3 examples survive in a section, the candidate fails validation.
- The Source weights section is replaced by the code-computed one.
- A failed validation means no swap and a ping that says why.

## Eval gate

`taste_eval.py` probes one item at a time with the proposer model.
System prompt: the profile text plus "Answer YES or NO only."
User message: "Would Daniel approve a lesson on: <title> (<source_type>)?"
The first YES or NO in the reply is the prediction.

Metrics over the held-out set, where positive means weight above zero and negative means weight below zero:

- precision = YES on positives / all YES.
- recall = YES on positives / all positives.
- F1 from those two.
- golden_recall = YES on golden_yes items / golden_yes items.

Swap rule, a pure function with its own tests:

- new F1 is at least old F1 minus 0.02, and
- new golden_recall is at least old golden_recall.

The first run compares the candidate against the seed `docs/interests.md`.
About 80 items times 2 profiles means about 160 Haiku-sized calls a week.

## Video picker

`_run_explain_track` loads the profile text through `profile.load_profile_text` and passes it where `interests.md` was passed.
The gated `build_preference_context`, `load_preference_context`, `merge_feedback`, and `load_live_feedback` are deleted from `preferences.py` with their tests.
`candidate_topics` and the topic taxonomy stay, because the video item still records `topics`.
`GOLDEN_PAPERS_PATH`, `CLASSICS_PATH`, and `INTERESTS_PATH` move to a small `repo_files.py` module that both the worker and the proposer import.

## Golden sheet converter

`scripts/build_golden_papers.py` reads `docs/golden-papers-labeling.md` and writes `docs/golden-papers.jsonl`, one object per labelled entry: `title`, `url` or null, `rating`, `why`.
Label mapping, case-insensitive on the first word of the label: `yes`, `yeah`, `implement` to COOL; `learned` to MEH; `no`, `skip` to SKIP.
A label that maps to nothing is skipped with a warning.
`url` is filled when the title fuzzy-matches a `docs/classics.json` entry, else null.
The jsonl is committed and ships in the image.
Entry 26 in the sheet, "model collapse", is renamed in the sheet to the paper title "The Curse of Recursion: Training on Generated Data Makes Models Forget" so it can match.

## Classics list

`docs/classics.json` gains the golden papers that are not in it yet.
The arXiv ids below were verified against the arXiv API by id on 2026-10-02.

| Title | arXiv id | Year |
|---|---|---|
| SWE-bench: Can Language Models Resolve Real-World GitHub Issues? | 2310.06770 | 2023 |
| Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena | 2306.05685 | 2023 |
| STaR: Bootstrapping Reasoning With Reasoning | 2203.14465 | 2022 |
| Evaluating Large Language Models Trained on Code | 2107.03374 | 2021 |
| DSPy: Compiling Declarative Language Model Calls into Self-Improving Pipelines | 2310.03714 | 2023 |
| MemGPT: Towards LLMs as Operating Systems | 2310.08560 | 2023 |
| The Curse of Recursion: Training on Generated Data Makes Models Forget | 2305.17493 | 2023 |

## Infra

- `infra/scheduler.tf`: add `consolidate-weekly` to `proposer_schedules` with `cron(0 18 ? * SUN *)` and command `["worker", "consolidate"]`.
- `infra/iam.tf`, `proposer_task` policy: `ProposalDocs` gains `s3:GetObject` on `proposals/*` and a new statement grants `s3:GetObject` and `s3:PutObject` on `profile/*`.
- `infra/iam.tf`, `explain_task` policy: `VideoArtifacts` gains `s3:GetObject` on `profile/*`.
- `infra/runtime.auto.tfvars` (gitignored, written at deploy time): `proposer_model` set to the `agentlab-sonnet` application inference profile ARN, `deep_read_model` and `pick_model` set to the values the live task definitions carry today.
- Both images are rebuilt: `scripts/build_and_push_image.sh` and `scripts/build_and_push_video_image.sh`.

## Testing

Unit tests, all offline, following the moto and monkeypatch patterns in `tests/test_proposer.py` and `tests/test_worker.py`:

- `test_episodes.py`: every row of the episode table, the 48 and 72 hour edges with a monkeypatched clock, the 60 day age factor, source type derivation, golden loading, the hash split is stable.
- `test_profile.py`: pointer round trip, S3 version write, flip with previous, fallback to the seed file, validation accepts a good profile, rejects a missing section, drops a hallucinated example, fails when fewer than 3 examples survive, replaces the source weights section.
- `test_taste_eval.py`: YES and NO parsing, metrics on a fixed set, the swap rule on boundary values.
- `test_consolidate.py`: the tally-only path with fewer than 5 new episodes, the dry run writes nothing, a passing candidate flips the pointer and pings with a REVERT button, a failing candidate pings without flipping.
- `test_proposer.py`: the prompt contains the profile and shows `IGNORED`, no GitHub source can appear, the classic slot is filed first by code, the day record lands in S3, the message shows real titles and lenses, parse accepts `why` and `lens`.
- `test_sources.py`: GitHub fetcher gone, arXiv categories widened, the proposer pool deduplicates by identity.
- `test_approvals_webhook.py`: the ECS override carries `PID` and `EXPLAIN_TITLE`, revert flips the pointer only when the version matches, a foreign user cannot revert.
- `test_worker.py`: the video item carries `pid`, the proposal gets `video_key`, the picker reads the profile with fallback, the consolidate command wires env and flags.
- `test_build_golden_papers.py`: the label mapping, URL match through classics, the warning on an unmapped label.

Real path checks before the work is called done:

1. `uv run pytest` green and `uv run ruff check` clean.
2. `uv run agentlab worker consolidate --dry-run` against the live table with local credentials prints stats, a candidate profile, and the eval.
3. A real consolidate run produces version 1, the pointer, and a Telegram ping with a REVERT button.
4. A real `worker propose` run with the new image sends a message with three real titles and lenses, and the day record exists in S3.
5. One approval from the phone starts a video whose ledger item carries the `pid`.

## Rollout

1. Merge `taste-flywheel` into `main`.
2. Build and push both images from `main`.
3. Write `infra/runtime.auto.tfvars`, run `terraform plan`, read it, then `terraform apply` from the main checkout, where the state file lives.
4. Run `worker consolidate` once by hand so Monday's proposer has version 1.
5. Watch the first weekly ping on Sunday.

## Risks

- The silent stretch from 2026-08-25 to 2026-09-19 adds 45 ignored proposals while daily videos were flooding the chat. The 60 day age factor and the human-readable diff with REVERT limit the damage.
- The model may write a profile that fits the training episodes and not Daniel. The held-out F1 gate catches the worst of it. Only more taps fix the rest.
- arXiv rate limits can empty the pool on a bad day. The fetchers fail soft and the proposer pings "no fresh sources", as today.
- The proposer image and the video image must both carry the new `episodes.py` and `profile.py`, or the picker falls back to the seed file without warning. The rollout rebuilds both.
