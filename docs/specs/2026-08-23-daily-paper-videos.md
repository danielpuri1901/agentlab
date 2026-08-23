# Daily paper videos: design spec

Status: designed with Daniel 2026-08-23 (deterministic fetch + dedup DB, two tracks, JSON scene plan, fixed Manim template, outcome-based feedback); this spec is the binding authority for the implementation plan.

## The product

Every day, two ~90-second videos land on Daniel's Telegram, in the house style (mechanism-first, real numbers, one street-test question):

1. FRESH track: the most relevant paper from the last 7 days.
2. CLASSIC track: the next paper from a curated list of ~60-100 all-time influential papers (AI/ML, software engineering, RSI/evals/verification; the "Attention Is All You Need" tier).

Daniel rates each video by tap: IMPLEMENT (would implement this) / LEARNED / SKIP.
The rating is the pipeline's outcome metric; "would implement" later cross-links to techniques that actually become lab experiments.

## Pipeline (six stages)

1. FETCH (deterministic): existing sources.py pulls arXiv (last 7 days), GitHub releases, HN.
2. DEDUP (deterministic): every candidate is checked against the seen-papers store before anything else sees it.
   Identity resolution, in order: exact arXiv id (extracted from any URL form), else normalized title (lowercase, punctuation and whitespace collapsed), else fuzzy title ratio >= 92 (difflib, stdlib).
   No embeddings; this is identity, not similarity.
   New candidates are recorded as seen the moment they are picked, not when fetched (unpicked papers may resurface later).
3. PICK (one model call): rank the new candidates against Daniel's interest profile (a versioned file in the repo, docs/interests.md, itself editable learned state); papers outrank HN links; output ONE paper URL for the fresh track.
   The classic track needs no model: next unwatched entry in the curated list.
4. DEEP READ -> SCENE PLAN (one model call per paper): fetch the full text, produce a STRICT JSON scene plan validated against a schema:
   {title, one_line_claim, mechanism_steps: [{label, detail}] (3-6), key_numbers: [{value, meaning}] (up to 3), street_test_question, citation_url}.
   Grounding rule: every number must appear in the fetched text; the prompt forbids attributing anything the source does not say (same rule as the proposer).
5. RENDER (deterministic): a fixed Manim template turns any valid scene plan into the video.
   Scene skeleton: title card -> claim -> mechanism steps animating one by one -> numbers -> question card.
   The model never writes animation code; a bad model day yields a boring video, never a broken one.
   Renders in the worker container (Manim + ffmpeg in a dedicated image); target under 10 minutes per video on 1 vCPU.
6. DELIVER + RATE: notify.py send_video with inline buttons [IMPLEMENT / LEARNED / SKIP]; quiet hours queue as usual.
   The approvals Lambda gains a second callback namespace vid:<paper_key>:<rating>, same secret + same Daniel-only check, writing the rating to the ledger.
   prop:* handling is untouched.

## Storage (existing DynamoDB table, no new database)

- seen_paper#<identity_key> / sk "paper": url, title, source, first_seen, picked (bool), track.
- video#<paper_key> / sk "video": scene plan S3 key, video S3 key, sent_ts, rating, rating_ts.
- The curated classics live in the repo as docs/classics.json (curated once with citations, hand-extensible), progress tracked via the same seen_paper items.

## Failure behavior (silence always means broken)

- Render failure: Daniel gets the text digest (claim + mechanism + numbers) plus a failure note instead of a video.
- Deep-read failure or no valid plan: a one-line "no video today: <reason>" ping.
- All failures also write an event item to the ledger.

## Schedule and cost

- One new EventBridge Scheduler entry (10:30 Amsterdam) runs `worker explain` on the video image: it produces the fresh video, then the classic video.
- Est. cost/day: two deep-read model calls (Sonnet-tier) + ~20 Fargate-minutes; well under $1/day. Bounded by the existing $50 alarm.

## Feedback loop (v1 records, v2 gates)

v1: ratings are recorded and shown in a weekly tally ping.
v2 (registered follow-up): the pick prompt and the template become champion/challenger subjects gated on rating outcomes, and IMPLEMENT ratings cross-link to experiments that later implement the technique.

## Explicitly out of scope for v1

Voiceover/audio, freeform animation, more than two videos/day, embedding-based similarity, auto-updating the interest profile.
