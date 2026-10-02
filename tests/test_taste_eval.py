"""The probe eval is the gate on every profile swap."""

import pytest

from agentlab.episodes import Episode
from agentlab.taste_eval import (
    EvalResult,
    decide_swap,
    evaluate,
    parse_yes_no,
    probe_messages,
)


def _episode(title, weight, kind="approved", source_type="arxiv"):
    return Episode(
        identity=f"title:{title.lower()}",
        title=title,
        url="",
        source_type=source_type,
        kind=kind,
        weight=weight,
        ts="2026-10-01T10:00:00.000000Z",
    )


def test_probe_messages_put_profile_in_system_and_item_in_user():
    messages = probe_messages("PROFILE TEXT", "Some Paper", "arxiv")
    assert messages[0]["role"] == "system"
    assert "PROFILE TEXT" in messages[0]["content"]
    assert "YES or NO" in messages[0]["content"]
    assert messages[1]["role"] == "user"
    assert "Some Paper (arxiv)" in messages[1]["content"]


def test_parse_yes_no():
    assert parse_yes_no("YES") is True
    assert parse_yes_no("  no.") is False
    assert parse_yes_no("Yes, Daniel would like it") is True
    assert parse_yes_no("I think not") is None
    assert parse_yes_no("") is None


def test_evaluate_metrics():
    held_out = [
        _episode("Good One", 1.0),
        _episode("Good Two", 1.0),
        _episode("Bad One", -1.0, kind="rejected", source_type="github"),
        _episode("Bad Two", -0.5, kind="ignored"),
    ]
    golden = [
        _episode("Golden One", 1.0, kind="golden_yes", source_type="classic"),
        _episode("Golden No", -1.0, kind="golden_no", source_type="classic"),
    ]
    answers = {
        "Good One": "YES",
        "Good Two": "NO",
        "Bad One": "YES",
        "Bad Two": "NO",
        "Golden One": "YES",
        "Golden No": "garbage",
    }
    seen_models = []

    def complete(model, messages):
        seen_models.append(model)
        title = messages[1]["content"].split("lesson on: ", 1)[1].split(" (", 1)[0]
        return answers[title]

    result = evaluate(held_out, golden, "profile", complete, "bedrock/probe")
    assert seen_models == ["bedrock/probe"] * 5  # 4 held-out probes + 1 golden_yes probe
    assert result.held_out_count == 4
    assert result.golden_count == 1
    assert result.yes_count == 2
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(0.5)
    assert result.f1 == pytest.approx(0.5)
    assert result.golden_recall == pytest.approx(1.0)
    assert result.as_dict()["f1"] == pytest.approx(0.5)


def test_evaluate_handles_empty_sets():
    result = evaluate([], [], "profile", lambda model, messages: "YES", "m")
    assert result.f1 == 0.0
    assert result.golden_recall == 1.0


def test_decide_swap_rule():
    old = EvalResult(precision=0.6, recall=0.6, f1=0.6, golden_recall=0.9, held_out_count=50, golden_count=20, yes_count=30)
    better = EvalResult(0.7, 0.7, 0.7, 0.9, 50, 20, 30)
    within = EvalResult(0.58, 0.58, 0.58, 0.9, 50, 20, 30)
    worse = EvalResult(0.57, 0.57, 0.57, 0.9, 50, 20, 30)
    golden_drop = EvalResult(0.8, 0.8, 0.8, 0.85, 50, 20, 30)
    assert decide_swap(old, better) == (True, "F1 0.60 -> 0.70, golden recall 0.90 -> 0.90")
    assert decide_swap(old, within)[0] is True
    assert decide_swap(old, worse) == (False, "F1 fell 0.60 -> 0.57, more than 0.02")
    assert decide_swap(old, golden_drop) == (False, "golden recall fell 0.90 -> 0.85")
