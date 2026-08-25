"""Scene plan module tests, fully offline: `complete` and `fetch_text` are
fakes injected by each test, mirroring how tests/test_proposer.py stands a
monkeypatched `_complete` in for the model provider. No network, no litellm.
"""

import copy
import json

import pytest
from pydantic import ValidationError

from agentlab.scene_plan import (
    ScenePlan,
    build_pick_prompt,
    deep_read,
    parse_scene_plan,
    pick_paper,
    split_digest_and_plan,
)

# Fences built with explicit string concatenation (not a literal triple-
# backtick block embedded in this .py file) so nothing that scans this repo
# for fenced code ever desyncs on a fence living inside a test fixture.
FENCE = "`" * 3

VALID_PLAN_DICT = {
    "title": "Retrieval memory beats longer context",
    "one_line_claim": "A small retrieval index beats raw context stuffing on long tasks.",
    "mechanism_steps": [
        {
            "label": "Chunk",
            "detail": "Split transcripts into turns.",
            "narration": "First, split the conversation into turns.",
        },
        {
            "label": "Embed",
            "detail": "Embed each turn with a small model.",
            "narration": "Then embed each turn with a small model.",
        },
        {
            "label": "Retrieve",
            "detail": "Pull the top matching turns at query time.",
            "narration": "At query time, pull back the closest turns.",
        },
    ],
    "key_numbers": [
        {"value": "12%", "meaning": "Accuracy gain over raw context."},
    ],
    "limits_or_caveats": "The paper does not test beyond 50 simulated turns.",
    "street_test_question": "Would this beat your current compaction approach on real logs.",
    "citation_url": "https://arxiv.org/abs/2601.00001",
}

DIGEST_MD = (
    "# Headline\n\nRetrieval beats raw context.\n\n"
    "## What the paper shows\n\nA 12% accuracy gain over raw context.\n\n"
    "## Mechanism\n\nChunk, embed, retrieve.\n\n"
    "## How it connects to AgentLab\n\nSame shape as our compaction task.\n\n"
    "## Limits\n\nNever tested past 50 simulated turns."
)


def _plan_kwargs(**overrides):
    data = copy.deepcopy(VALID_PLAN_DICT)
    data.update(overrides)
    return data


def _fenced(plan_dict: dict, digest: str = DIGEST_MD) -> str:
    return digest + "\n\n" + FENCE + "json\n" + json.dumps(plan_dict) + "\n" + FENCE


def test_valid_plan_constructs():
    plan = ScenePlan(**VALID_PLAN_DICT)
    assert plan.title == VALID_PLAN_DICT["title"]
    assert len(plan.mechanism_steps) == 3


def test_mechanism_steps_four_is_ok():
    four_steps = VALID_PLAN_DICT["mechanism_steps"] + [
        {"label": "Ship", "detail": "Deploy it.", "narration": "Finally, ship it."}
    ]
    plan = ScenePlan(**_plan_kwargs(mechanism_steps=four_steps))
    assert len(plan.mechanism_steps) == 4


def test_mechanism_steps_two_is_rejected():
    two_steps = VALID_PLAN_DICT["mechanism_steps"][:2]
    with pytest.raises(ValidationError):
        ScenePlan(**_plan_kwargs(mechanism_steps=two_steps))
    assert parse_scene_plan(json.dumps(_plan_kwargs(mechanism_steps=two_steps))) is None


def test_mechanism_steps_seven_is_rejected():
    seven_steps = [
        {"label": f"Step{i}", "detail": "Detail.", "narration": "Say it out loud."}
        for i in range(7)
    ]
    with pytest.raises(ValidationError):
        ScenePlan(**_plan_kwargs(mechanism_steps=seven_steps))
    assert parse_scene_plan(json.dumps(_plan_kwargs(mechanism_steps=seven_steps))) is None


def test_overlong_narration_clipped_by_parse_but_rejected_by_model():
    # Direct model construction stays strict (the schema is the contract);
    # parse_scene_plan clips lengths instead of failing (ruling 2026-08-25:
    # a truncated narration still makes a video, a dead track makes nothing).
    bad_steps = copy.deepcopy(VALID_PLAN_DICT["mechanism_steps"])
    bad_steps[0]["narration"] = "x" * 281
    with pytest.raises(ValidationError):
        ScenePlan(**_plan_kwargs(mechanism_steps=bad_steps))
    plan = parse_scene_plan(json.dumps(_plan_kwargs(mechanism_steps=bad_steps)))
    assert plan is not None
    assert len(plan.mechanism_steps[0].narration) == 280


def test_key_numbers_bounds():
    ScenePlan(**_plan_kwargs(key_numbers=[]))  # 0 is fine
    too_many = [{"value": "1", "meaning": "one"} for _ in range(4)]
    with pytest.raises(ValidationError):
        ScenePlan(**_plan_kwargs(key_numbers=too_many))


def test_parse_scene_plan_from_dirty_fenced_output():
    raw = (
        "Sure, here is the full write-up.\n\n"
        + _fenced(VALID_PLAN_DICT)
        + "\n\nHope that helps! Let me know if you want changes."
    )
    plan = parse_scene_plan(raw)
    assert plan is not None
    assert plan.title == VALID_PLAN_DICT["title"]
    assert plan.citation_url == VALID_PLAN_DICT["citation_url"]


def test_parse_scene_plan_from_unfenced_dirty_output():
    raw = "noise before " + json.dumps(VALID_PLAN_DICT) + " noise after"
    plan = parse_scene_plan(raw)
    assert plan is not None
    assert plan.one_line_claim == VALID_PLAN_DICT["one_line_claim"]


def test_parse_scene_plan_garbage_returns_none():
    assert parse_scene_plan("not json at all") is None
    assert parse_scene_plan("") is None
    assert parse_scene_plan("{not: valid json}") is None


def test_split_digest_and_plan_correctness():
    raw = _fenced(VALID_PLAN_DICT)
    digest, plan_raw = split_digest_and_plan(raw)
    assert digest == DIGEST_MD
    assert json.loads(plan_raw) == VALID_PLAN_DICT


def test_split_digest_and_plan_no_fence_returns_whole_text_as_digest():
    digest, plan_raw = split_digest_and_plan("just a digest, no plan here")
    assert digest == "just a digest, no plan here"
    assert plan_raw == ""


def test_deep_read_retries_once_then_succeeds():
    calls = []
    bad_plan = _plan_kwargs(mechanism_steps=VALID_PLAN_DICT["mechanism_steps"][:2])

    def fake_complete(model, messages):
        calls.append(messages)
        if len(calls) == 1:
            return _fenced(bad_plan, digest="# Bad draft\n\n## Limits\n\nSome limit.")
        return _fenced(VALID_PLAN_DICT)

    digest, plan = deep_read(
        "https://arxiv.org/abs/2601.00001",
        lambda url: "the full fetched source text",
        fake_complete,
    )

    assert len(calls) == 2
    assert digest == DIGEST_MD
    assert plan.title == VALID_PLAN_DICT["title"]
    # the retry prompt must carry the pydantic validation error forward
    retry_prompt = calls[1][-1]["content"]
    assert "Validation error" in retry_prompt
    assert "invalid" in retry_prompt.lower()


def test_deep_read_raises_when_still_invalid_after_retry():
    bad_plan = _plan_kwargs(mechanism_steps=VALID_PLAN_DICT["mechanism_steps"][:2])

    def fake_complete(model, messages):
        return _fenced(bad_plan)

    with pytest.raises(ValueError, match="scene plan invalid"):
        deep_read(
            "https://arxiv.org/abs/2601.00001",
            lambda url: "source text",
            fake_complete,
        )


def test_deep_read_passes_fetched_text_and_url_into_prompt():
    seen = {}

    def fake_fetch(url):
        seen["url"] = url
        return "SENTINEL SOURCE TEXT 42%"

    def fake_complete(model, messages):
        seen["prompt"] = messages[-1]["content"]
        return _fenced(VALID_PLAN_DICT)

    deep_read("https://arxiv.org/abs/9999.99999", fake_fetch, fake_complete)

    assert seen["url"] == "https://arxiv.org/abs/9999.99999"
    assert "SENTINEL SOURCE TEXT 42%" in seen["prompt"]
    assert "https://arxiv.org/abs/9999.99999" in seen["prompt"]


CANDIDATES = [
    {
        "pool": "exploit",
        "source": "arxiv",
        "title": "Paper A: agent memory",
        "url": "https://arxiv.org/abs/1",
        "summary": "a survey of agent memory systems",
    },
    {
        "pool": "exploit",
        "source": "hn",
        "title": "Paper B: harness benchmarks",
        "url": "https://x/b",
        "points": 120,
    },
    {
        "pool": "explore",
        "source": "hf",
        "title": "Paper C: diffusion world models",
        "url": "https://arxiv.org/abs/3",
        "upvotes": 40,
    },
]


def test_pick_paper_core_returns_valid_dict():
    def fake_complete(model, messages):
        assert "MOST relevant" in messages[1]["content"]
        return "2"

    chosen = pick_paper(CANDIDATES, "agents, evals, harnesses", fake_complete, mode="core")
    assert chosen == CANDIDATES[1]


def test_pick_paper_candidate_lines_render_pool_source_title_signal():
    prompt = build_pick_prompt(CANDIDATES, "agents", "core")
    assert "1. [exploit/arxiv] Paper A: agent memory (a survey of agent memory systems)" in prompt
    assert "2. [exploit/hn] Paper B: harness benchmarks (120 points)" in prompt
    assert "3. [explore/hf] Paper C: diffusion world models (40 upvotes)" in prompt


def test_pick_paper_novel_mode_changes_the_prompt():
    core_prompt = build_pick_prompt(CANDIDATES, "agents, evals", "core")
    novel_prompt = build_pick_prompt(CANDIDATES, "agents, evals", "novel")

    assert core_prompt != novel_prompt
    assert "MOST relevant" in core_prompt
    assert "MOST relevant" not in novel_prompt
    assert "LEAST likely" in novel_prompt
    assert "Ignore Daniel's interests" in novel_prompt


def test_pick_paper_novel_mode_calls_complete_with_novel_prompt():
    def fake_complete(model, messages):
        assert "LEAST likely" in messages[1]["content"]
        return "3"

    chosen = pick_paper(CANDIDATES, "agents, evals", fake_complete, mode="novel")
    assert chosen == CANDIDATES[2]


def test_pick_paper_garbage_output_returns_none():
    def fake_complete(model, messages):
        return "sorry, I cannot pick just one"

    assert pick_paper(CANDIDATES, "agents", fake_complete) is None


def test_pick_paper_out_of_range_index_returns_none():
    def fake_complete(model, messages):
        return "99"

    assert pick_paper(CANDIDATES, "agents", fake_complete) is None


def test_pick_paper_empty_candidates_returns_none_without_calling_complete():
    def exploding_complete(model, messages):
        raise AssertionError("complete must not be called with no candidates")

    assert pick_paper([], "agents", exploding_complete) is None


def test_pick_prompt_prefers_papers_over_hn_in_both_modes():
    from agentlab.scene_plan import build_pick_prompt

    for mode in ("core", "novel"):
        prompt = build_pick_prompt(
            [{"source": "hn", "title": "T", "points": 100}], "interests", mode=mode
        )
        assert "Prefer candidates from arxiv or hf" in prompt


def test_deep_read_pins_citation_to_fetch_url(monkeypatch):
    from agentlab.scene_plan import deep_read

    valid_plan = {
        "title": "T",
        "one_line_claim": "C.",
        "mechanism_steps": [
            {"label": f"L{i}", "detail": "D.", "narration": "N."} for i in range(3)
        ],
        "key_numbers": [],
        "limits_or_caveats": "L.",
        "street_test_question": "Q?",
        "citation_url": "https://evil.example/hallucinated",
    }
    import json as _json

    output = "# Digest\n\nBody.\n\n```json\n" + _json.dumps(valid_plan) + "\n```"
    _digest, plan = deep_read(
        "https://arxiv.org/abs/2607.07663",
        fetch_text=lambda url: "paper text",
        complete=lambda model, messages: output,
    )
    assert plan.citation_url == "https://arxiv.org/abs/2607.07663"


def test_mechanism_step_kind_defaults_and_validates():
    from pydantic import ValidationError as VE

    from agentlab.scene_plan import MechanismStep

    step = MechanismStep(label="L", detail="D", narration="N")
    assert step.kind == "transform"
    assert MechanismStep(label="L", detail="D", narration="N", kind="gate").kind == "gate"
    try:
        MechanismStep(label="L", detail="D", narration="N", kind="explode")
        raise AssertionError("unknown kind must be rejected")
    except VE:
        pass


def test_overlong_strings_clip_instead_of_failing():
    import json as _json

    from agentlab.scene_plan import MAX_CLAIM, MAX_NARRATION, parse_scene_plan

    plan_dict = {
        "title": "T" * 500,
        "one_line_claim": "C" * 500,
        "mechanism_steps": [
            {"label": "L" * 100, "detail": "D" * 500, "narration": "N" * 500}
            for _ in range(3)
        ],
        "key_numbers": [{"value": "V" * 50, "meaning": "M" * 200}],
        "limits_or_caveats": "X" * 500,
        "street_test_question": "Q" * 500,
        "citation_url": "https://arxiv.org/abs/2608.23493",
    }
    plan = parse_scene_plan(_json.dumps(plan_dict))
    assert plan is not None
    assert len(plan.one_line_claim) == MAX_CLAIM
    assert len(plan.mechanism_steps[0].narration) == MAX_NARRATION


def test_structural_violations_still_fail():
    import json as _json

    from agentlab.scene_plan import parse_scene_plan

    plan_dict = {
        "title": "T",
        "one_line_claim": "C",
        "mechanism_steps": [],
        "limits_or_caveats": "X",
        "street_test_question": "Q",
        "citation_url": "u",
    }
    assert parse_scene_plan(_json.dumps(plan_dict)) is None
