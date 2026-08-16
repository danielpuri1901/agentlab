"""Compaction Inspect task: baseline (truncate) vs candidate (structured summary)
solvers, scored by exact recall of planted facts after compaction."""

from inspect_ai import Task, task
from inspect_ai.dataset import Sample
from inspect_ai.model import get_model
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

STRUCTURED_SUMMARY_INSTRUCTIONS = (
    "Summarize the conversation turns below into a structured summary that "
    "preserves every exact identifier and code exactly as written (for "
    "example CODE-12345). Do not paraphrase, drop, or approximate any code.\n\n"
)


def compaction_dataset(seeds: list[int]) -> list[Sample]:
    """Build one Sample per seed, carrying its session's facts and transcript in metadata."""
    samples = []
    for seed in seeds:
        session = generate_session(seed=seed)
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
    """Build the compacted context for "truncate", or the summarizer prompt for "structured".

    "truncate" (baseline): keep only the post-boundary turns verbatim.
    "structured" (candidate): return a prompt asking the model to summarize the
    pre-boundary turns while preserving exact identifiers and codes; the caller
    is responsible for running that prompt through a model to get the actual
    summary.
    """
    boundary = int(BOUNDARY_FRACTION * len(turns))
    pre_boundary = turns[:boundary]
    post_boundary = turns[boundary:]
    if style == "truncate":
        return "\n".join(post_boundary)
    if style == "structured":
        return STRUCTURED_SUMMARY_INSTRUCTIONS + "\n".join(pre_boundary)
    raise ValueError(f"unknown compaction style: {style!r}")


@solver
def compaction_solver(style: str) -> Solver:
    """Compact the transcript per style, then answer each probe question using
    only the compacted context."""

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        turns = state.metadata["transcript"]
        post_boundary = turns[state.metadata["boundary"] :]
        model = get_model()

        if style == "truncate":
            compacted_context = compact_transcript(turns, style="truncate")
        elif style == "structured":
            summarizer_prompt = compact_transcript(turns, style="structured")
            summary_output = await model.generate(summarizer_prompt)
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
def compaction_task(style: str, model: str, seeds: list[int] | None = None) -> Task:
    """Build the compaction Inspect Task for the given style ("truncate" or "structured")."""
    return Task(
        dataset=compaction_dataset(seeds if seeds is not None else DEFAULT_SEEDS),
        solver=compaction_solver(style=style),
        scorer=recall_scorer(),
        model=model,
    )
