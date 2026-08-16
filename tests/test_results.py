from inspect_ai import Task, eval

from agentlab.compaction_task import (
    compaction_dataset,
    compaction_solver,
    recall_scorer,
)
from agentlab.results import extract_results, mean_score, total_cost, total_tokens


def _run(seeds, style, epochs, tmp_path):
    samples = compaction_dataset(seeds=seeds)
    task = Task(
        dataset=samples,
        solver=compaction_solver(style=style),
        scorer=recall_scorer(),
        model="mockllm/model",
    )
    [log] = eval(task, display="none", log_dir=str(tmp_path), epochs=epochs)
    return log


def test_extract_results_groups_scores_by_task_id_across_epochs(tmp_path):
    log = _run(seeds=[1, 2], style="truncate", epochs=3, tmp_path=tmp_path)

    results = extract_results(log.location)

    assert set(results.scores) == {"1", "2"}
    assert len(results.scores["1"]) == 3
    assert len(results.scores["2"]) == 3
    assert all(
        isinstance(v, float) for values in results.scores.values() for v in values
    )


def test_extract_results_captures_token_usage_per_model(tmp_path):
    log = _run(seeds=[1], style="truncate", epochs=1, tmp_path=tmp_path)

    results = extract_results(log.location)

    assert len(results.usages) == 1
    model, input_tokens, output_tokens = results.usages[0]
    assert model == "mockllm/model"
    assert input_tokens > 0
    assert output_tokens > 0


def test_mean_score_averages_task_means():
    scores = {"a": [0.0, 1.0], "b": [0.5]}
    assert mean_score(scores) == 0.5  # task a means 0.5, task b means 0.5


def test_total_tokens_sums_input_and_output_across_usages():
    usages = [("model-a", 100, 10), ("model-b", 50, 5)]
    assert total_tokens(usages) == 165


def test_total_cost_skips_unpriced_models_without_raising():
    usages = [("mockllm/model", 100, 10)]

    result = total_cost(usages)

    assert result.total == 0.0
    assert result.unpriced_models == {"mockllm/model"}


def test_total_cost_prices_known_models():
    usages = [("bedrock/amazon.nova-lite-v1:0", 1_000_000, 100_000)]

    result = total_cost(usages)

    assert result.total > 0.0
    assert result.unpriced_models == set()
