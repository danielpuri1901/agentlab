# Entrant registration: `deepseek_style`

Tournament: `docs/tournaments/001-compaction-strategies.md`.
Registered: 2026-08-17.

## Sources

All fetched from `github.com/deepseek-ai/deepseek-harness` at commit `99f6f02fecdb7dff40c3fbc9470f5907c29f74ca` via the GitHub code-search/contents MCP tool on 2026-08-17.
This is the repository's resolved default-branch HEAD at fetch time; pinning the commit SHA makes this registration reproducible even if the branch moves.

1. [`packages/compaction/compaction-basic/src/summarizer.ts`](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/compaction/compaction-basic/src/summarizer.ts).
This is the actual TypeScript source, not documentation: it contains the literal `COMPACTION_INSTRUCTION` constant, sent verbatim as the final user message after the replayed conversation, and the `CHECKPOINT_PREAMBLE` used to frame the replacement message.
This is the primary source; every verbatim phrase below is quoted from this file.
2. [`packages/compaction/compaction-basic/README.md`](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/compaction/compaction-basic/README.md), section "Model Experience > Auxiliary summarizer request".
Reproduces the same instruction text as source 1 under "What the model sees", which I used to cross-check that the documentation and the source code have not drifted apart; they match exactly.
Also contributed the policy description: "token-budget retention," "Model-free pruning... rewrites oversized tool results before range selection," and the KV-cache-preserving design rationale for why the instruction is appended rather than issued as a separate system prompt.
3. [`docs/subsystems/compaction.md`](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/docs/subsystems/compaction.md).
Contributed the architectural framing (compaction as an optional capability seam: Service Definition / Provider / Consumer) and confirmed that tool-result pruning runs before range selection as the model-free step, ahead of any summarization call.
4. [`packages/compaction/compaction-tool-result-pruner/README.md`](https://github.com/deepseek-ai/deepseek-harness/blob/99f6f02fecdb7dff40c3fbc9470f5907c29f74ca/packages/compaction/compaction-tool-result-pruner/README.md).
Contributed the precise mechanics of the model-free pre-step: it rewrites over-budget `tool/result` nodes to "a bounded head, a fixed omission marker, and a bounded tail," is explicitly "syntactic" (retains beginning and end "without interpreting which middle lines are semantically important"), and runs with no model call.

## Strategy summary

`dsh-compaction-basic` is token-pressure triggered: it compacts once measured context crosses a configured threshold ratio of the routed model's context window, not on a fixed turn count.
Before any summarization, an optional model-free pass (`ctx.toolResultPruner`) deterministically truncates oversized tool results to a head/marker/tail shape, and compaction skips the summarization call entirely if that alone relieves pressure.
If summarization is still needed, one auxiliary model call replays the conversation's own system prompt, tools, and the shadowed message range verbatim, then appends a single fixed instruction as the final user message asking for a structured Markdown checkpoint with explicit rules to preserve exact identifiers, numeric values, and other literal fragments without paraphrasing.
The real prompt is coding-agent specific (it asks for sections like "Files and Code" and "Errors and Fixes" that presuppose a software task), so this adaptation keeps the literal framing, the terse-bullets directive, and the exact-preservation rule for identifiers and numeric values, while dropping the software-specific section headers that our chat corpus has nothing to put under them.

## Model-free pre-step

Documented step: `ctx.toolResultPruner` truncates over-budget tool-result content to a bounded head, fixed marker, and bounded tail, before compaction attempts summarization; this can avoid the summarization call entirely if it relieves pressure on its own.
Status on this corpus: **N/A-here**, for the same underlying reason as `claude_code_style`.
The 001 corpus has no tool-call or tool-result turns; the pruner's target (`tool/result` surface nodes) does not exist in this corpus's transcript format, so the step is a documented no-op here rather than an implemented one.

## Exact instruction text

```text
You are now acting as a compaction engine. Condense the conversation above into a structured checkpoint that lets another model resume the work with no loss of essential context. Write terse bullets, not prose paragraphs. Preserve exact identifiers, codes, and numeric values exactly as written; do not paraphrase, drop, or approximate any of them. Capture the decisions and current state faithfully, especially anything stated for the record.
```

## Derivation notes

Verbatim or near-verbatim from `COMPACTION_INSTRUCTION` in source 1:
- "You are now acting as a compaction engine" is quoted exactly.
- "Condense the conversation ABOVE into a structured checkpoint that lets another model resume the work with no loss of essential context" is quoted with only "ABOVE" lowercased for register consistency with this lab's other prompts and "for this AI coding assistant" dropped, since our corpus is not a coding session.
- "Write terse bullets, not prose paragraphs" is quoted exactly from the "Output EXACTLY the Markdown structure below" rule block.
- "Preserve exact... identifiers, numeric values... exactly as written; do not paraphrase, drop, or approximate any of them" adapts the literal rule "Preserve exact file paths, commands, error strings, identifiers, numeric values, function signatures, and syntax fragments," keeping only the two items applicable to this corpus (identifiers, numeric values) and adding "codes" explicitly, since that is this lab's term for the corpus's planted `CODE-xxxxx` values and the vendor's "identifiers" is the closest documented category they fall under.
- "Capture... faithfully, especially anything stated for the record" adapts "Capture user feedback and explicit instructions faithfully, especially corrections," substituting this corpus's "note for the record" phrasing for the vendor's "user feedback / corrections" framing, since our transcripts have no corrections to capture but do have facts explicitly marked "for the record."

Deliberately dropped, and why (this is the material judgment call in this registration):
- The full eight-section Markdown structure ("Primary Request and Intent," "Key Technical Concepts," "Files and Code," "Errors and Fixes," "Pending Jobs," "Current Work," "Next Step," "Critical Context"). Five of the eight sections (files/code, errors/fixes, pending jobs, next step, current work) presuppose an ongoing software task; our corpus is a static planted-fact chat transcript with no files, errors, or pending work to report, so forcing those headers would either produce "(none)" boilerplate that burns the 150-token summary budget on structure instead of content, or force the model to invent content. Keeping all eight headers verbatim would also make this entrant roughly 4 to 5x the length register of every other entry in `_SUMMARY_INSTRUCTIONS_BY_STYLE`, which the tournament brief explicitly asked to avoid.
- "Do NOT mention this summarization request or that the context was compacted" and "Output only the checkpoint text: do not call any tool or take any other action." These are output-hygiene rules for a production agent loop (stopping the model from breaking character or invoking tools); they do not bear on identifier recall, which is what this tournament measures, so they were cut for length rather than for irrelevance to the vendor's design.
- The multi-turn merge rule ("If the conversation already contains a `<compacted-summary>` block... merge newer information into a single consolidated summary"). Our harness runs one compaction per task, never a second compaction over a prior summary, so this rule has no applicable case on this corpus.
- The path/commands/error-strings/function-signatures items from the preservation rule, dropped because this corpus's transcripts never contain any of them; keeping only identifiers and numeric values is a corpus-fit trim, not a disagreement with the vendor's rule.

## Honest-labeling note

`deepseek_style` is an adaptation of `dsh-compaction-basic`'s actual, literal, commit-pinned summarization prompt, trimmed for length register and corpus fit as detailed above; it is not a full reproduction of the vendor's prompt, and it omits the vendor's model-free tool-result pruning step, threshold-ratio trigger policy, and multi-cycle merge behavior entirely, since none of those apply within this lab's single-shot, fixed-budget compaction harness.
Report results as this style's recall, not as "DeepSeek Harness's recall," per the tournament's "-style" labeling rule.

## Confidence note

Confidence that the instruction text is faithful is high for the sentences marked verbatim or near-verbatim above: they are pulled from a commit-pinned source file, and the README (a second, independently maintained file in the same repo) reproduces the identical text, which is a real cross-check against drift, not just a single citation.
Confidence is necessarily lower on the net effect of the trims, since dropping five of eight sections and the output-hygiene rules is a real design choice this registration made, not something the vendor tested at this length; the tournament's honest "-style" framing exists exactly for this reason.
