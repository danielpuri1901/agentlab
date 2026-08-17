"""Compaction Inspect task: baseline (truncate) vs candidate (structured summary)
solvers, scored by exact recall of planted facts after compaction."""

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import GenerateConfig, get_model
from inspect_ai.scorer import Score, Scorer, Target, mean, scorer
from inspect_ai.solver import Generate, Solver, TaskState, solver

from agentlab.corpus import DEFAULT_PLANT_FRACTION, generate_session

BOUNDARY_FRACTION = DEFAULT_PLANT_FRACTION
"""Fraction of a session's transcript turns that fall before the compaction
boundary. Equal to corpus.DEFAULT_PLANT_FRACTION by construction (imported,
not redefined) so every fact corpus.generate_session plants lands strictly
before this boundary."""

DEFAULT_SEEDS = list(range(10))
"""Seeds used to build compaction_task's dataset when no seeds are supplied."""

NAIVE_SUMMARY_INSTRUCTIONS = "Summarize the conversation so far concisely.\n\n"

STRUCTURED_SUMMARY_INSTRUCTIONS = (
    "Summarize the conversation turns below into a structured summary that "
    "preserves every exact identifier and code exactly as written (for "
    "example CODE-12345). Do not paraphrase, drop, or approximate any code.\n\n"
)

_SUMMARY_INSTRUCTIONS_BY_STYLE = {
    "naive": NAIVE_SUMMARY_INSTRUCTIONS,
    "structured": STRUCTURED_SUMMARY_INSTRUCTIONS,
    "codes_first": (
        "You are compacting a conversation under a strict length budget. "
        "FIRST, list every exact identifier and its code verbatim, one per line, "
        "in the form '<identifier> resolved with <CODE>'. "
        "THEN, only if budget remains, add one sentence summarizing the rest. "
        "Never paraphrase, shorten, or omit any code."
    ),
    # Tournament 001 entrants; derivations + citations in docs/tournaments/001-entrants/.
    "claude_code_style": (
        "You are compacting this conversation to fit a strict length budget, "
        "using Claude Code's documented compaction priorities. Preserve, in "
        "priority order: the task's objectives and intent; key decisions and "
        "the current state of the work, including every exact identifier or "
        "code exactly as written; any constraints on the work; then, only if "
        "budget remains, other recent context. Never paraphrase, shorten, or "
        "drop an exact identifier or code that belongs to a decision or the "
        "current state, and drop a lower-priority category entirely before "
        "shortening a higher one."
    ),
    "deepseek_style": (
        "You are now acting as a compaction engine. Condense the conversation "
        "above into a structured checkpoint that lets another model resume the "
        "work with no loss of essential context. Write terse bullets, not "
        "prose paragraphs. Preserve exact identifiers, codes, and numeric "
        "values exactly as written; do not paraphrase, drop, or approximate "
        "any of them. Capture the decisions and current state faithfully, "
        "especially anything stated for the record."
    ),
}
"""The only difference between the "naive" and "structured" summary styles:
the instruction text prepended to the pre-boundary turns before they go to
the summarizer. compact_transcript and compaction_solver route both styles
through this single lookup so nothing else (boundary, which turns feed the
summarizer, how the summary and post-boundary turns combine, the probe
flow) can drift between them."""


def compaction_dataset(
    seeds: list[int], n_facts: int = 12, filler_turns: int = 40
) -> list[Sample]:
    """Build one Sample per seed, carrying its session's facts and transcript in metadata."""
    samples = []
    for seed in seeds:
        session = generate_session(seed=seed, n_facts=n_facts, filler_turns=filler_turns)
        boundary = int(BOUNDARY_FRACTION * len(session.transcript))
        samples.append(
            Sample(
                input="\n".join(session.transcript),
                target=[fact.value for fact in session.facts],
                id=seed,
                metadata={
                    "facts": [fact.model_dump() for fact in session.facts],
                    "boundary": boundary,
                    "transcript": session.transcript,
                },
            )
        )
    return samples


def compact_transcript(turns: list[str], style: str) -> str:
    """Build the compacted context for "truncate", or the summarizer prompt for
    "naive"/"structured".

    "truncate" (baseline): keep only the post-boundary turns verbatim.
    "naive"/"structured": return a prompt asking the model to summarize the
    pre-boundary turns; the caller is responsible for running that prompt
    through a model to get the actual summary. The two styles differ only in
    the instruction text prepended (see _SUMMARY_INSTRUCTIONS_BY_STYLE):
    "naive" is a plain summarize request, "structured" asks to preserve
    exact identifiers and codes.
    """
    boundary = int(BOUNDARY_FRACTION * len(turns))
    pre_boundary = turns[:boundary]
    post_boundary = turns[boundary:]
    if style == "truncate":
        return "\n".join(post_boundary)
    if style in _SUMMARY_INSTRUCTIONS_BY_STYLE:
        return _SUMMARY_INSTRUCTIONS_BY_STYLE[style] + "\n".join(pre_boundary)
    raise ValueError(f"unknown compaction style: {style!r}")


@solver
def compaction_solver(style: str, summary_budget: int = 150) -> Solver:
    """Compact the transcript per style, then answer each probe question using
    only the compacted context.

    `summary_budget` caps `max_tokens` on the summarization model call only
    (for "naive" and "structured"); it does not apply to the per-probe
    answering calls below.
    """

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        turns = state.metadata["transcript"]
        post_boundary = turns[state.metadata["boundary"] :]
        model = get_model()

        if style == "truncate":
            compacted_context = compact_transcript(turns, style="truncate")
        elif style in _SUMMARY_INSTRUCTIONS_BY_STYLE:
            summarizer_prompt = compact_transcript(turns, style=style)
            summary_output = await model.generate(
                summarizer_prompt, config=GenerateConfig(max_tokens=summary_budget)
            )
            compacted_context = "\n".join([summary_output.completion, *post_boundary])
        else:
            raise ValueError(f"unknown compaction style: {style!r}")

        answers = []
        for fact in state.metadata["facts"]:
            probe_prompt = (
                f"{compacted_context}\n\n"
                f"Question: {fact['probe_question']}\n"
                "Answer with only the exact code, nothing else."
            )
            probe_output = await model.generate(probe_prompt)
            answers.append(probe_output.completion)

        state.metadata["compacted_context"] = compacted_context
        state.store.set("answers", answers)
        return state

    return solve


@scorer(metrics=[mean()])
def recall_scorer() -> Scorer:
    """Fraction of probes answered with the exact planted value.

    Deterministic string-contains match against the exact CODE-xxxxx target
    value for each fact, in the order the solver asked the probes. No model
    judge is involved.
    """

    async def score(state: TaskState, target: Target) -> Score:
        expected = list(target)
        answers = state.store.get("answers", [])
        if not expected:
            return Score(value=0.0, explanation="no facts to recall")
        correct = sum(1 for value, answer in zip(expected, answers) if value in answer)
        return Score(
            value=correct / len(expected),
            answer="; ".join(answers),
            explanation=f"{correct}/{len(expected)} probes answered with exact planted value",
        )

    return score


@task
def compaction_task(
    style: str,
    model: str,
    seeds: list[int] | None = None,
    summary_budget: int = 150,
    n_facts: int = 12,
    filler_turns: int = 40,
) -> Task:
    """Build the compaction Inspect Task for the given style ("truncate",
    "naive", or "structured")."""
    return Task(
        dataset=compaction_dataset(
            seeds if seeds is not None else DEFAULT_SEEDS,
            n_facts=n_facts,
            filler_turns=filler_turns,
        ),
        solver=compaction_solver(style=style, summary_budget=summary_budget),
        scorer=recall_scorer(),
        model=model,
    )
