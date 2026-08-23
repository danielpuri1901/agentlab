# Daily paper videos: design spec

Status: designed with Daniel 2026-08-23 (deterministic fetch + dedup DB, two tracks, JSON scene plan, fixed Manim template, outcome-based feedback); this spec is the binding authority for the implementation plan.

## The product

Every day, two ~90-second videos land on Daniel's Telegram, in the house style (mechanism-first, real numbers, one street-test question):

1. FRESH track: the most relevant paper from the last 7 days.
2. CLASSIC track: the next paper from a curated list of ~60-100 all-time influential papers (AI/ML, software engineering, RSI/evals/verification; the "Attention Is All You Need" tier).

Daniel rates each video by tap: IMPLEMENT (would implement this) / LEARNED / SKIP.
The rating is the pipeline's outcome metric; "would implement" later cross-links to techniques that actually become lab experiments.

## Pipeline (six stages)

1. FETCH (deterministic), TWO POOLS (Daniel's explore/exploit split, 2026-08-23):
   - EXPLOIT: the existing keyword-filtered pull (arXiv last 7 days, tracked GitHub repos, HN matching KEYWORDS).
   - EXPLORE: no keywords by design (you cannot keyword-search the unknown); selected purely on crowd traction: HN front-page items above a points threshold regardless of topic, plus HuggingFace Daily Papers (community-upvoted, all-of-AI; endpoint verified at build time).
   Fresh-track scheduling is deterministic: five exploit days, two fixed explore days (Wed, Sat) per week.
   Explore papers that earn IMPLEMENT or LEARNED ratings feed back: the weekly tuner proposes their topics as new keywords in docs/interests.md via the same gated PR, so the interest profile evolves from evidence.
2. DEDUP (deterministic): every candidate is checked against the seen-papers store before anything else sees it.
   Identity resolution, in order: exact arXiv id (extracted from any URL form), else normalized title (lowercase, punctuation and whitespace collapsed), else fuzzy title ratio >= 92 (difflib, stdlib).
   No embeddings; this is identity, not similarity.
   New candidates are recorded as seen the moment they are picked, not when fetched (unpicked papers may resurface later).
3. PICK (one model call): rank the new candidates against Daniel's interest profile (a versioned file in the repo, docs/interests.md, itself editable learned state); papers outrank HN links; output ONE paper URL for the fresh track.
   The classic track needs no model: next unwatched entry in the curated list.
4. DEEP READ -> TWO ARTIFACTS (the layering rule: compression must never mean loss).
   One deep read produces both:
   a. The FULL DIGEST: a complete deep-dive document (the rsi-survey-digest treatment: every load-bearing idea, mechanisms, numbers, limits), committed to docs/digests/ and linked in the Telegram message. This is where the paper's full value lives.
   b. The SCENE PLAN, distilled from the digest: a STRICT JSON plan validated against a schema:
   {title, one_line_claim, mechanism_steps: [{label, detail, narration}] (3-6), key_numbers: [{value, meaning}] (up to 3), limits_or_caveats (one sentence: what the paper does NOT claim), street_test_question, citation_url}.
   The video is the retention layer; the digest is the depth layer; the street-test question is the bridge between them.
   Grounding rule: every number must appear in the fetched text; the prompt forbids attributing anything the source does not say (same rule as the proposer).
5. RENDER (deterministic): a fixed Manim template turns any valid scene plan into the video.
   Scene skeleton: title card -> claim -> mechanism steps animating one by one -> numbers -> caveat -> question card.
   VOICEOVER: Amazon Polly (neural TTS) narrates each scene's narration text; scene durations are set from the measured audio clip lengths; ffmpeg muxes audio in.
   SUBTITLES: burned in from the same narration text (we own every spoken word, so subtitles are exact, no transcription).
   Polly voice availability in eu-west-1 is verified at build time, not assumed.
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

## The flywheel (incremental, outcome-driven)

The pipeline is deterministic everywhere except two prompts (pick, deep-read), so improving it means aligning those prompts with Daniel's labels.
- Daniel's ratings (IMPLEMENT / LEARNED / SKIP) are the human labels; the pick prompt is an LLM judge choosing what he sees.
- The loop: ratings accumulate in the ledger -> review where the judge's picks disagree with the labels -> tweak the prompt, following production prompt practice (Anthropic and LangChain prompt-engineering guidance) -> agreement climbs.
- IMPLEMENT ratings additionally cross-link to lab experiments that later implement the technique; that link is the true outcome behind the label.
- A weekly tally ping keeps the label data visible without dashboards.

### The golden set and the weekly auto-tuner (Daniel's design, 2026-08-23)

- GOLDEN SET: docs/golden-papers.jsonl, entries {title, url, label: IMPLEMENT|LEARNED|SKIP, why (one line)}.
  Seeded with this session's evidence-backed labels (SPADE, covert-coordination, RSI survey, Voyager: IMPLEMENT by demonstrated action; the meta-agent paper: "seemed implementable, premise failed scrutiny") plus 10-20 examples from Daniel.
  Every week's ratings append to it, so the set grows itself.
- WEEKLY AUTO-TUNER (activates once the golden set supports its gate, ~30+ labels): a Friday-evening scheduled agent aggregates the week's ratings, finds where the pick-judge disagreed with the labels, drafts a revised pick prompt, and evaluates old vs new on the golden set.
  The swap gate is deterministic: the new prompt must score strictly better on agreement AND misrank none of a protected core subset; otherwise no change, findings ping Daniel instead.
  This is the lab's first automated self-modification loop, deliberately bounded: creative rewrite, deterministic acceptance, regression-protected, human-visible.

## Explicitly out of scope for v1

Freeform animation, more than two videos/day, embedding-based similarity, auto-updating the interest profile.
