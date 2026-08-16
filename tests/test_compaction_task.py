from inspect_ai import Task, eval
from inspect_ai.model import get_model

from agentlab.compaction_task import (
    compact_transcript,
    compaction_dataset,
    compaction_solver,
    compaction_task,
    recall_scorer,
)


def test_dataset_one_sample_per_seed():
    samples = compaction_dataset(seeds=[1, 2, 3])
    assert len(samples) == 3
    assert all("facts" in s.metadata and "boundary" in s.metadata for s in samples)


def test_truncate_drops_preboundary_and_structured_keeps_all_input():
    turns = [f"turn {i}" for i in range(10)]
    truncated = compact_transcript(turns, style="truncate")
    assert "turn 0" not in truncated and "turn 9" in truncated
    structured_prompt = compact_transcript(turns, style="structured")
    assert (
        "turn 0" in structured_prompt
    )  # pre-boundary content goes INTO the summarizer prompt


def test_dataset_boundary_matches_boundary_fraction():
    from agentlab.compaction_task import BOUNDARY_FRACTION

    samples = compaction_dataset(seeds=[5])
    sample = samples[0]
    transcript = sample.metadata["transcript"]
    assert sample.metadata["boundary"] == int(BOUNDARY_FRACTION * len(transcript))


def test_compaction_task_constructs_without_resolving_a_real_model():
    # "mockllm" never makes a network call, so this proves Task construction
    # (dataset + solver + scorer wiring) succeeds with no credentials present.
    result = compaction_task(style="truncate", model="mockllm/model")
    assert isinstance(result, Task)
    assert len(result.dataset) > 0


def test_compaction_task_structured_style_also_constructs():
    result = compaction_task(style="structured", model="mockllm/model")
    assert isinstance(result, Task)


def test_recall_scorer_end_to_end_with_mock_model_all_correct(tmp_path):
    # Drive a real eval() run entirely offline: mockllm answers every probe
    # (and the structured-summary call) with the exact planted code, so recall
    # should be a perfect 1.0 for every sample. No network, no credentials.
    samples = compaction_dataset(seeds=[11])
    fact_values = [fact["value"] for fact in samples[0].metadata["facts"]]

    def always_correct(input, tools, tool_choice, config):
        from inspect_ai.model import ModelOutput

        # Echo every planted value back so every probe (and the summary call)
        # contains the exact code being asked about.
        return ModelOutput.from_content(model="mockllm", content=" ".join(fact_values))

    model = get_model("mockllm/model", custom_outputs=always_correct)
    task = Task(
        dataset=samples,
        solver=compaction_solver(style="structured"),
        scorer=recall_scorer(),
        model=model,
    )

    [log] = eval(task, display="none", log_dir=str(tmp_path))

    assert log.status == "success"
    [sample] = log.samples
    assert sample.scores["recall_scorer"].value == 1.0


def test_recall_scorer_end_to_end_with_mock_model_all_wrong(tmp_path):
    # Default mockllm output never contains a planted code, so recall is 0.0.
    samples = compaction_dataset(seeds=[12])
    task = Task(
        dataset=samples,
        solver=compaction_solver(style="truncate"),
        scorer=recall_scorer(),
        model="mockllm/model",
    )

    [log] = eval(task, display="none", log_dir=str(tmp_path))

    assert log.status == "success"
    [sample] = log.samples
    assert sample.scores["recall_scorer"].value == 0.0
