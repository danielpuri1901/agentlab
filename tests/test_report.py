from agentlab.report import render_report
from agentlab.stats import PairedResult


def test_report_contains_verdict_and_cost():
    r = PairedResult(0.12, 0.03, 0.21, 20, 0.19)
    md = render_report(
        result=r,
        verdict_str="PROMOTE",
        total_cost=1.42,
        log_paths=["logs/a.eval", "logs/b.eval"],
    )
    assert "PROMOTE" in md and "$1.42" in md and "logs/a.eval" in md
    assert "0.03" in md and "0.21" in md  # CI bounds visible


def test_report_states_unpriced_model_caveat_when_present():
    r = PairedResult(0.12, 0.03, 0.21, 20, 0.19)
    md = render_report(
        result=r,
        verdict_str="PROMOTE",
        total_cost=1.42,
        log_paths=["logs/a.eval", "logs/b.eval"],
        unpriced_models=["mockllm/model"],
    )
    assert "mockllm/model" in md
    assert "excludes usage from models with no known price" in md


def test_report_omits_unpriced_model_caveat_when_absent():
    r = PairedResult(0.12, 0.03, 0.21, 20, 0.19)
    md = render_report(
        result=r,
        verdict_str="PROMOTE",
        total_cost=1.42,
        log_paths=["logs/a.eval", "logs/b.eval"],
        unpriced_models=[],
    )
    assert "excludes usage from models with no known price" not in md
