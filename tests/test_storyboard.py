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


def test_parse_storyboard_pins_application_to_grounded_plan(golden, plan):
    application = next(
        beat for beat in golden["beats"] if beat["role"] == "application"
    )
    application["narration"] = "Use it everywhere because it always works."

    board, error = sb.parse_storyboard(_fenced(golden), DIGEST, plan)

    assert error == ""
    assert board is not None
    actual = next(beat for beat in board.beats if beat.role == "application")
    assert actual.narration == plan.application_or_implication


def test_parse_storyboard_accepts_a_mapping_term_the_scene_plan_never_names(
    golden, plan
):
    """Six of 24 failed runs up to 2026-10-04 died on the rule that every
    mapping term must appear in the scene plan. The rule is gone."""
    golden["mapping"][0]["paper_term"] = "magic suitcase"

    board, error = sb.parse_storyboard(_fenced(golden), DIGEST, plan)

    assert error == ""
    assert board.mapping[0].paper_term == "magic suitcase"


def test_parse_storyboard_clips_long_strings_instead_of_failing(golden, plan):
    golden["visual_focus"] = "x" * 500
    golden["beats"][0]["visual"] = "y" * 900
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert len(board.visual_focus) == sb.MAX_VISUAL_FOCUS
    assert len(board.beats[0].visual) == sb.MAX_VISUAL


@pytest.mark.parametrize("mechanism_beats", [0, 6])
def test_parse_storyboard_accepts_six_to_twelve_beats(golden, plan, mechanism_beats):
    beats = golden["beats"]
    mechanism = [beat for beat in beats if beat["role"] == "mechanism"]
    golden["beats"] = (
        beats[:2] + [mechanism[i % len(mechanism)] for i in range(mechanism_beats)]
        + beats[-4:]
    )

    board, error = sb.parse_storyboard(_fenced(golden), DIGEST, plan)

    assert error == ""
    assert len(board.beats) == 6 + mechanism_beats


@pytest.mark.parametrize("beat_count", [5, 13])
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


def test_parse_storyboard_repairs_ungrounded_number_from_scene_plan(golden, plan):
    golden["beats"][3]["narration"] = "Codes first keeps 91%."
    golden["beats"][3]["on_screen_text"] = ["91% retained"]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert board is not None
    assert "91%" not in board.model_dump_json()
    assert sb.ungrounded_numbers(board.model_dump(), DIGEST, plan) == []


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


def test_design_storyboard_uses_a_second_correction_for_repeated_invalid_structure(
    golden, plan
):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    replies = iter([_fenced(bad), _fenced(bad), _fenced(golden)])
    calls = []

    def complete(model, messages):
        calls.append(messages)
        return next(replies)

    board = sb.design_storyboard(DIGEST, plan, complete, model="m")

    assert len(calls) == 3
    assert "beats" in calls[2][-1]["content"]
    assert len(board.beats) == 8


def test_design_storyboard_raises_after_all_attempts_fail(golden, plan):
    bad = json.loads(json.dumps(golden))
    bad["beats"] = bad["beats"][:2]
    calls = []

    def complete(model, messages):
        calls.append(messages)
        return _fenced(bad)

    with pytest.raises(sb.StoryboardInvalid):
        sb.design_storyboard(DIGEST, plan, complete, model="m")
    assert len(calls) == sb.MAX_STORYBOARD_ATTEMPTS


def test_prompt_carries_digest_plan_and_the_hard_rules(plan):
    prompt = sb.build_storyboard_prompt(DIGEST, plan)
    assert DIGEST in prompt
    assert plan.street_test_question in prompt
    for rule in ("real mechanism", "3Blue1Brown", "title", "street-test"):
        assert rule in sb.STORYBOARD_SYSTEM + prompt


def test_prompt_names_recent_visual_directions_to_avoid(plan):
    prompt = sb.build_storyboard_prompt(
        DIGEST,
        plan,
        recent_visual_directions=["Paper A: a left-to-right row of glowing boxes"],
    )

    assert "Recent visual directions" in prompt
    assert "left-to-right row" in prompt
    assert "Do not reuse these visual ideas" in prompt


def test_prompt_directs_a_3b1b_explainer_without_a_forced_metaphor():
    system = sb.STORYBOARD_SYSTEM
    assert "3Blue1Brown" in system
    for gone in ("one bold visual metaphor", "Prefer creative over safe", "visually striking"):
        assert gone not in system
    for rule in (
        "concrete to abstract",
        "one colour",
        "decoration",
        "Use 3D only for spatial ideas",
        "simple definition",
        "6 to 12 beats",
        "application",
        "at most 22 words",
    ):
        assert rule in system

def test_parse_storyboard_removes_analogy_opening(golden, plan):
    golden["beats"][0]["narration"] = "Imagine a gate. " + golden["simple_definition"]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert board is not None
    assert board.beats[0].narration == f"{plan.title}. {board.simple_definition}"


def test_parse_storyboard_repairs_title_from_scene_plan(golden, plan):
    golden["title"] = "A clever gate story"
    golden["beats"][0]["on_screen_text"] = [golden["title"]]
    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)
    assert error == ""
    assert board is not None
    assert board.title == plan.title
    assert board.beats[0].narration == f"{plan.title}. {board.simple_definition}"
    assert board.beats[0].on_screen_text == [plan.title]


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
    plan.street_test_question = (
        "How would you know whether this memory and reflection system improves "
        "decisions when the environment changes and old lessons become misleading "
        "rather than useful?"
    )
    golden["beats"][-1]["narration"] = "Would this work for you?"

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert board is not None
    assert board.beats[-1].narration == plan.street_test_question


def test_parse_storyboard_accepts_any_role_order_and_a_result_without_a_key_number(
    golden, plan
):
    """The rule that the result beat must quote a key number killed the
    approved "Scaling Laws for Neural Language Models" video on 2026-10-04
    after three storyboard attempts. No rule places content in a beat now."""
    result = next(beat for beat in golden["beats"] if beat["role"] == "result")
    result["narration"] = "The system gets a useful result."
    result["on_screen_text"] = ["result"]
    golden["beats"] = list(reversed(golden["beats"]))

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert [beat.role for beat in board.beats][-1] == "title"


@pytest.mark.parametrize("escaped", [False, True])
def test_parse_storyboard_turns_an_em_dash_into_a_hyphen(golden, plan, escaped):
    golden["beats"][2]["narration"] = "The gate opens \u2014 then it checks."
    golden["visual_focus"] = "A gate\u2014and a path."

    board, error = sb.parse_storyboard(
        json.dumps(golden, ensure_ascii=escaped), DIGEST, plan
    )

    assert error == ""
    assert board.beats[2].narration == "The gate opens - then it checks."
    assert board.visual_focus == "A gate-and a path."


def test_parse_storyboard_clips_a_definition_too_long_for_the_title_beat(golden, plan):
    golden["simple_definition"] = "word " * 40

    board, error = sb.parse_storyboard(json.dumps(golden), DIGEST, plan)

    assert error == ""
    assert len(board.beats[0].narration) <= sb.MAX_NARRATION
    assert board.beats[0].narration.startswith(f"{plan.title}. word word")
