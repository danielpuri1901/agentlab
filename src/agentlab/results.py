"""Extract per-task scores and per-model token usage from Inspect eval logs.

Reads through Inspect's analysis dataframe API (`samples_df`) rather than
hand-parsing `EvalLog`/`EvalSample` objects, so the extraction stays in step
with whatever Inspect's log schema does internally.
"""

import json
from dataclasses import dataclass

from inspect_ai.analysis import SampleSummary, samples_df

from agentlab.costs import experiment_cost, resolve_price

DEFAULT_SCORER_NAME = "recall_scorer"
"""Matches the scorer function name registered in compaction_task.py; Inspect
names each `score_<name>` dataframe column after the scorer's registered
name, and this project's compaction task registers exactly one scorer."""


@dataclass
class LogResults:
    scores: dict[str, list[float]]
    """Task id (Inspect sample `id`, cast to str) -> one score per epoch/repeat.

    Shaped to drop straight into `agentlab.stats.paired_analysis`."""

    usages: list[tuple[str, int, int]]
    """One (model, input_tokens, output_tokens) tuple per sample per model used.

    Shaped to drop straight into `agentlab.costs.experiment_cost`."""


@dataclass
class CostBreakdown:
    total: float
    unpriced_models: set[str]
    """Models seen in `usages` with no entry in litellm's price map (e.g. a
    mockllm test double). Their token usage is excluded from `total` rather
    than raising, since a missing test-only price is not a reason to fail an
    otherwise-successful run; callers should still surface this set to avoid
    silently under-reporting cost for a real, mispriced model id."""


def extract_results(
    log_path: str, scorer_name: str = DEFAULT_SCORER_NAME
) -> LogResults:
    """Read one eval log's samples dataframe into scores grouped by task id
    and a flat list of per-sample, per-model token usage."""
    df = samples_df([log_path], columns=SampleSummary)

    score_column = f"score_{scorer_name}"
    scores: dict[str, list[float]] = {}
    for task_id, value in zip(df["id"], df[score_column]):
        scores.setdefault(task_id, []).append(float(value))

    usages: list[tuple[str, int, int]] = []
    for raw_usage in df["model_usage"]:
        per_model = json.loads(raw_usage) if raw_usage else {}
        for model, usage in per_model.items():
            usages.append((model, usage["input_tokens"], usage["output_tokens"]))

    return LogResults(scores=scores, usages=usages)


def mean_score(scores: dict[str, list[float]]) -> float:
    """Mean recall across tasks: the average of each task's per-repeat mean."""
    task_means = [sum(values) / len(values) for values in scores.values()]
    return sum(task_means) / len(task_means)


def total_tokens(usages: list[tuple[str, int, int]]) -> int:
    """Sum of input and output tokens across every usage entry."""
    return sum(
        input_tokens + output_tokens for _, input_tokens, output_tokens in usages
    )


def total_cost(usages: list[tuple[str, int, int]]) -> CostBreakdown:
    """Sum costs for usages with a known litellm price, skipping the rest.

    `costs.experiment_cost` raises `KeyError` on the first unpriced model,
    which is correct for that pure module but too strict here: local/test
    runs against mockllm (or any other model absent from litellm's map) must
    still produce a report, just with those tokens excluded from the cost
    total and flagged via `unpriced_models`. The summation itself stays
    `costs.experiment_cost`'s job; this function only partitions `usages`
    into priced and unpriced before delegating, rather than re-summing
    per-usage costs itself."""
    unpriced: set[str] = set()
    priced_usages: list[tuple[str, int, int]] = []
    for usage in usages:
        model = usage[0]
        try:
            resolve_price(model)
        except KeyError:
            unpriced.add(model)
        else:
            priced_usages.append(usage)
    total = experiment_cost(priced_usages) if priced_usages else 0.0
    return CostBreakdown(total=total, unpriced_models=unpriced)
