# Entrant registration: `claude_code_style`

Tournament: `docs/tournaments/001-compaction-strategies.md`.
Registered: 2026-08-17.

## Sources

1. [How Claude Code works](https://code.claude.com/docs/en/how-claude-code-works), section "When your context fills up" (fetched via Exa 2026-08-17).
Contributed the top-level claim: "Claude Code manages context automatically as you approach the limit.
It clears older tool outputs first, then summarizes the conversation if needed.
Your requests and key code snippets are preserved; detailed instructions from early in the conversation may be lost."
2. [Explore the context window](https://code.claude.com/docs/en/context-window) (fetched via Exa 2026-08-17).
Contributed the detailed content list for the summarization pass: "The summary keeps: your requests and intent, key technical concepts, files examined or modified with important code snippets, errors and how they were fixed, pending tasks, and current work.
It replaces the verbatim conversation: full tool outputs and intermediate reasoning are gone."
This is the primary source for the priority ordering used below.
3. [Glossary, "Compaction"](https://code.claude.com/docs/en/glossary) (fetched via Exa 2026-08-17).
Corroborates source 1 independently: "Automatic summarization of your conversation when the context window approaches its limit.
Older tool outputs are cleared first, then the conversation is summarized."

All three are official code.claude.com/docs pages, not third-party write-ups, so the model-free-first-then-summarize sequencing and the six-category content list are Anthropic's own documented description of the behavior, not an inference from outside commentary.
Anthropic does not publish the literal internal prompt Claude Code sends to produce the summary; only the categories and their order are documented.

## Strategy summary

Claude Code's documented compaction is two stage.
First, a model-free step clears older tool outputs before any summarization runs.
Second, if pressure remains, a summarization pass replaces the older conversation span with a structured summary that keeps six content categories in a stated order of importance: the user's requests and intent, key technical concepts, files or decisions with their important details, errors and how they were fixed, pending tasks, and current work.
Critically, the documentation does not instruct the summarizer to front-load exact identifiers or codes ahead of everything else the way `codes_first` does; identifiers are preserved only implicitly, as a byproduct of capturing "key technical concepts" and "current work" faithfully.
This entrant exists to measure exactly that difference: does a general priority-ordered category structure retain planted codes as well as a strategy engineered to spend the budget on codes first?

## Model-free pre-step

Documented step: clear older tool outputs before summarizing.
Status on this corpus: **N/A-here**.
The 001 corpus is a synthetic chat transcript of only user- and assistant-role conversational turns; there are no tool-call or tool-result turns for this step to act on, and the planted facts live in ordinary conversational turns ("note for the record, `<key>` resolved with `<CODE>`"), never in a tool output.
Running this step on our corpus would be a no-op every time, so it is recorded but not implemented as code; this keeps the entrant honest about testing the documented summarization-priority ordering specifically, not a step the corpus cannot exercise.

## Exact instruction text

```text
You are compacting this conversation to fit a strict length budget, using Claude Code's documented compaction priorities. Preserve, in priority order: the task's objectives and intent; key decisions and the current state of the work, including every exact identifier or code exactly as written; any constraints on the work; then, only if budget remains, other recent context. Never paraphrase, shorten, or drop an exact identifier or code that belongs to a decision or the current state, and drop a lower-priority category entirely before shortening a higher one.
```

## Derivation notes

Verbatim-sourced elements:
- The four-tier ordering (objectives/intent, then decisions/state, then constraints, then recent context) is a direct, compressed translation of the documented six categories in source 2, collapsed per the tournament brief's mapping: "task objectives" = requests and intent; "key decisions, constraints, current state" = key technical concepts + files/decisions + errors/fixes + current work; "recent context" = the lowest documented priority, since it is what pending/current work items reduce to once the higher categories are satisfied.
- "Identifiers/codes count as key decisions/state" is the brief's explicit instruction for how to place our corpus's planted `CODE-xxxxx` values into Claude Code's documented category list, since the corpus has no files, errors, or technical concepts in the software sense for codes to attach to otherwise.
- The clear-tool-outputs-first ordering (source 1, source 3) is recorded as the model-free pre-step, not folded into the instruction text, since it is a distinct operation in the real system, not a summarization instruction.

Interpreted elements (not verbatim from any source, because no source publishes them):
- The literal wording of the instruction string itself. Anthropic documents *what* the summary preserves and in what order, not the exact prompt text used to produce it, so every sentence above is my own operationalization of the documented priorities into an actionable instruction.
- The explicit "drop a lower-priority category entirely before shortening a higher one" rule. This is a reasonable reading of "priority order" under a hard token budget, but it is my inference, not a quoted policy.
- The choice to make the pre-step's absence a no-op rather than skip the entrant's registration entirely; the tournament brief directed recording it as N/A-here with an explanation, which is what the section above does.

## Honest-labeling note

`claude_code_style` is a best-effort, citation-anchored adaptation of Claude Code's *publicly documented* compaction priorities, translated into a summarization instruction for this lab's corpus.
It is not Claude Code's actual internal compaction prompt (which Anthropic has not published) and its measured recall must not be reported as "Claude Code's recall" on the leaderboard, only as this style's recall, per the tournament's "-style" labeling rule.
