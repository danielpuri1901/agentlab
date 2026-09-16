import json
from pathlib import Path

import pytest

from agentlab import storyboard as sb
from agentlab.scene_plan import ScenePlan

FIXTURE = Path(__file__).parent / "fixtures" / "storyboard_golden.json"
PLAN_FIXTURE = Path(__file__).parent / "fixtures" / "sample_plan.json"

DIGEST = (
    "# Headline\nCompaction keeps codes only if the prompt asks for them.\n\n"
    "## What the paper shows\nA plain summary keeps 0% of exact codes on a weak model; "
    "codes first keeps 44%. Tested on lists of up to 50 items.\n\n## Limits\n"
    "The paper does not test lists longer than 50 items."
)


@pytest.fixture
def plan() -> ScenePlan:
    return ScenePlan(**json.loads(PLAN_FIXTURE.read_text(encoding="utf-8")))


@pytest.fixture
def golden() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _fenced(data: dict) -> str:
    return "Here are three candidates...\n```json\n" + json.dumps(data) + "\n```"


def test_golden_storyboard_validates(golden):
    board = sb.Storyboard(**golden)
    assert len(board.beats) == 8
    assert board.beats[0].role == "title"
    assert board.beats[0].on_screen_text == ["Recursive Self-Improvement in AI"]


def test_parse_storyboard_tolerates_fences_and_prose(golden, plan):
    board, error = sb.parse_storyboard(_fenced(golden), DIGEST, plan)
    assert error == ""
    assert board.visual_focus.startswith("Keep the agent")


def test_parse_storyboard_clips_long_strings_instead_of_failing(golden, plan):
    golden["visual_focus"] = "x" * 500
    golden["beats"][0]["visual"] = "y" * 900
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert len(board.visual_focus) == sb.MAX_VISUAL_FOCUS
    assert len(board.beats[0].visual) == sb.MAX_VISUAL


@pytest.mark.parametrize("beat_count", [7, 11])
def test_parse_storyboard_rejects_bad_beat_counts(golden, plan, beat_count):
    beat = golden["beats"][0]
    golden["beats"] = [beat] * beat_count
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "beats" in error


def test_parse_storyboard_rejects_garbage(plan):
    board, error = sb.parse_storyboard("not json at all", DIGEST, plan)
    assert board is None
    assert "JSON" in error


def test_ungrounded_numbers_flags_invented_numbers_only(golden, plan):
    golden["beats"][3]["narration"] = (
        "A plain summary keeps 0% of the codes. Codes first keeps 91%."
    )
    golden["beats"][1]["on_screen_text"] = ["1,999 items"]
    # 1,999 normalises to 1999 (commas ignored) and is nowhere in the digest or plan.
    # Order is incidental (which beat comes first), so compare as a set.
    assert sorted(sb.ungrounded_numbers(golden, DIGEST, plan)) == ["1999", "91%"]


def test_ungrounded_numbers_accepts_numbers_from_the_plan(golden, plan):
    # 1250 and 74% are key_numbers in sample_plan.json, not in DIGEST.
    golden["beats"][3]["narration"] = (
        "The survey covers 1250 papers and 74% are from this year."
    )
    assert sb.ungrounded_numbers(golden, DIGEST, plan) == []


def test_ungrounded_numbers_compares_complete_numeric_tokens(golden, plan):
    # The plan grounds 1250 and 74%, but neither token grounds a shorter number.
    golden["beats"][3]["narration"] = "The survey covers 125 papers and retains 4%."
    assert sb.ungrounded_numbers(golden, DIGEST, plan) == ["125", "4%"]


def test_parse_storyboard_treats_ungrounded_number_as_validation_error(golden, plan):
    golden["beats"][3]["narration"] = "Codes first keeps 91%."
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "91%" in error


def test_design_storyboard_retries_once_with_the_error_then_succeeds(golden, plan):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    replies = iter([_fenced(bad), _fenced(golden)])
    calls = []

    def complete(model, messages):
        calls.append(messages)
        return next(replies)

    board = sb.design_storyboard(DIGEST, plan, complete, model="m")
    assert len(calls) == 2
    assert "beats" in calls[1][-1]["content"]
    assert len(board.beats) == 8


def test_design_storyboard_raises_after_second_failure(golden, plan):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    with pytest.raises(sb.StoryboardInvalid):
        sb.design_storyboard(
            DIGEST, plan, lambda model, messages: _fenced(bad), model="m"
        )


def test_prompt_carries_digest_plan_and_the_hard_rules(plan):
    prompt = sb.build_storyboard_prompt(DIGEST, plan)
    assert DIGEST in prompt
    assert plan.street_test_question in prompt
    for rule in ("real mechanism", "unrelated metaphor", "title", "street-test"):
        assert rule in sb.STORYBOARD_SYSTEM + prompt


def test_prompt_names_recent_visual_directions_to_avoid(plan):
    prompt = sb.build_storyboard_prompt(
        DIGEST,
        plan,
        recent_visual_directions=["Paper A: a left-to-right row of glowing boxes"],
    )

    assert "Recent visual directions" in prompt
    assert "left-to-right row" in prompt
    assert "substantially different" in prompt


def test_prompt_requires_direct_simple_explanation():
    system = sb.STORYBOARD_SYSTEM
    assert "simple definition" in system
    assert "clearest visual explanation" in system
    assert "visually compelling" in system
    assert "Abstract visual systems are welcome" in system
    assert "designed for this paper" in system
    assert "8 to 10 beats" in system
    assert "application" in system
    assert "at most 22 words" in system
    assert "Keep the same component positions" not in system


def test_parse_storyboard_removes_analogy_opening(golden, plan):
    golden["beats"][0]["narration"] = "Imagine a gate. " + golden["simple_definition"]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert board is not None
    assert board.beats[0].narration == f"{plan.title}. {board.simple_definition}"


def test_parse_storyboard_rejects_terms_outside_scene_plan(golden, plan):
    golden["mapping"][0]["paper_term"] = "magic suitcase"
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "outside the scene plan" in error


def test_parse_storyboard_accepts_normalised_scene_plan_node_ids(golden, plan):
    golden["mapping"][0]["paper_term"] = "gate"

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert board is not None


def test_parse_storyboard_requires_the_scene_plan_title(golden, plan):
    golden["title"] = "A clever gate story"
    golden["beats"][0]["on_screen_text"] = [golden["title"]]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "scene plan title" in error


def test_parse_storyboard_builds_title_beat_from_known_fields(golden, plan):
    """Changing model prose must not reject an otherwise valid storyboard."""
    golden["beats"][0]["narration"] = "Here is the big idea."
    golden["beats"][0]["on_screen_text"] = ["Main idea"]

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert board is not None
    assert board.beats[0].narration == f"{plan.title}. {board.simple_definition}"
    assert board.beats[0].on_screen_text == [plan.title]


def test_parse_storyboard_builds_question_beat_from_scene_plan(golden, plan):
    golden["beats"][-1]["narration"] = "Would this work for you?"

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert board is not None
    assert board.beats[-1].narration == plan.street_test_question


def test_parse_storyboard_result_uses_a_grounded_key_number(golden, plan):
    result = next(beat for beat in golden["beats"] if beat["role"] == "result")
    result["narration"] = "The system gets a useful result."
    result["on_screen_text"] = ["result"]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert board is None
    assert "key number" in error
