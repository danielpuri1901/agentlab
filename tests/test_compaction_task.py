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


def test_dataset_passes_n_facts_and_filler_turns_through_to_generate_session():
    samples = compaction_dataset(seeds=[5], n_facts=3, filler_turns=8)
    sample = samples[0]
    assert len(sample.metadata["facts"]) == 3
    assert len(sample.metadata["transcript"]) == 8


def test_naive_and_structured_differ_only_in_instruction_text():
    from agentlab.compaction_task import (
        BOUNDARY_FRACTION,
        NAIVE_SUMMARY_INSTRUCTIONS,
        STRUCTURED_SUMMARY_INSTRUCTIONS,
    )

    turns = [f"turn {i}" for i in range(10)]
    boundary = int(BOUNDARY_FRACTION * len(turns))
    pre_boundary_text = "\n".join(turns[:boundary])

    naive_prompt = compact_transcript(turns, style="naive")
    structured_prompt = compact_transcript(turns, style="structured")

    assert naive_prompt == NAIVE_SUMMARY_INSTRUCTIONS + pre_boundary_text
    assert structured_prompt == STRUCTURED_SUMMARY_INSTRUCTIONS + pre_boundary_text
    assert NAIVE_SUMMARY_INSTRUCTIONS != STRUCTURED_SUMMARY_INSTRUCTIONS
    # Same pre-boundary content fed to both, byte-identical once each arm's
    # own instruction prefix is stripped off.
    assert naive_prompt.removeprefix(NAIVE_SUMMARY_INSTRUCTIONS) == (
        structured_prompt.removeprefix(STRUCTURED_SUMMARY_INSTRUCTIONS)
    )


def test_naive_instruction_is_plain_summarize_request():
    from agentlab.compaction_task import NAIVE_SUMMARY_INSTRUCTIONS

    assert "Summarize the conversation so far concisely." in NAIVE_SUMMARY_INSTRUCTIONS
    # It must NOT carry structured's identifier-preservation language.
    assert "identifier" not in NAIVE_SUMMARY_INSTRUCTIONS
    assert "CODE-12345" not in NAIVE_SUMMARY_INSTRUCTIONS


def test_compaction_task_constructs_without_resolving_a_real_model():
    # "mockllm" never makes a network call, so this proves Task construction
    # (dataset + solver + scorer wiring) succeeds with no credentials present.
    result = compaction_task(style="truncate", model="mockllm/model")
    assert isinstance(result, Task)
    assert len(result.dataset) > 0


def test_compaction_task_structured_style_also_constructs():
    result = compaction_task(style="structured", model="mockllm/model")
    assert isinstance(result, Task)


def test_compaction_task_naive_style_also_constructs():
    result = compaction_task(style="naive", model="mockllm/model")
    assert isinstance(result, Task)


def test_compaction_task_passes_corpus_knobs_through_to_dataset():
    result = compaction_task(
        style="truncate", model="mockllm/model", seeds=[1], n_facts=2, filler_turns=6
    )
    [sample] = result.dataset
    assert len(sample.metadata["facts"]) == 2
    assert len(sample.metadata["transcript"]) == 6


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


def test_naive_style_end_to_end_with_mock_model_all_correct(tmp_path):
    # Mirrors the structured all-correct e2e test, for the naive style.
    samples = compaction_dataset(seeds=[13])
    fact_values = [fact["value"] for fact in samples[0].metadata["facts"]]

    def always_correct(input, tools, tool_choice, config):
        from inspect_ai.model import ModelOutput

        return ModelOutput.from_content(model="mockllm", content=" ".join(fact_values))

    model = get_model("mockllm/model", custom_outputs=always_correct)
    task = Task(
        dataset=samples,
        solver=compaction_solver(style="naive"),
        scorer=recall_scorer(),
        model=model,
    )

    [log] = eval(task, display="none", log_dir=str(tmp_path))

    assert log.status == "success"
    [sample] = log.samples
    assert sample.scores["recall_scorer"].value == 1.0


def test_summary_budget_reaches_summarizer_generate_config_only(tmp_path):
    # The summary_budget must land as max_tokens on the GenerateConfig for the
    # summarization call, and NOT on the per-probe answering calls.
    samples = compaction_dataset(seeds=[14])
    captured_max_tokens = []

    def capture_config(input, tools, tool_choice, config):
        from inspect_ai.model import ModelOutput

        captured_max_tokens.append(config.max_tokens)
        return ModelOutput.from_content(model="mockllm", content="irrelevant")

    model = get_model("mockllm/model", custom_outputs=capture_config)
    task = Task(
        dataset=samples,
        solver=compaction_solver(style="structured", summary_budget=77),
        scorer=recall_scorer(),
        model=model,
    )

    [log] = eval(task, display="none", log_dir=str(tmp_path))

    assert log.status == "success"
    # First call is the summarizer call; the rest are per-fact probe calls.
    assert captured_max_tokens[0] == 77
    assert len(captured_max_tokens) > 1
    assert all(v is None for v in captured_max_tokens[1:])


def test_summary_budget_defaults_to_150():
    import inspect

    sig = inspect.signature(compaction_solver)
    assert sig.parameters["summary_budget"].default == 150


def test_codes_first_style_uses_shared_summary_path():
    turns = [f"turn {i}" for i in range(10)]
    prompt = compact_transcript(turns, style="codes_first")
    structured = compact_transcript(turns, style="structured")
    # Same shared machinery: both carry the pre-boundary turns; only the
    # instruction prefix differs (the no-confound guarantee extends to the
    # new arm).
    assert "turn 0" in prompt
    from agentlab.compaction_task import _SUMMARY_INSTRUCTIONS_BY_STYLE

    for style, text in (("codes_first", prompt), ("structured", structured)):
        instr = _SUMMARY_INSTRUCTIONS_BY_STYLE[style]
        assert text.startswith(instr)
    assert prompt.removeprefix(
        _SUMMARY_INSTRUCTIONS_BY_STYLE["codes_first"]
    ) == structured.removeprefix(_SUMMARY_INSTRUCTIONS_BY_STYLE["structured"])
