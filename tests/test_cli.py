import json
from types import SimpleNamespace

import pytest
import typer
from typer.testing import CliRunner

from agentlab.cli import app, check_log_status

runner = CliRunner()


def test_run_command_writes_report_with_verdict_offline(tmp_path):
    # Fully offline: mockllm never makes a network call. Drives both arms
    # through eval(), extraction, paired_analysis, verdict, and report
    # rendering, in place of the credentialed Bedrock pilot (PENDING-CREDENTIALS,
    # skipped in this environment; see task-6-report.md).
    result = runner.invoke(
        app,
        [
            "run",
            "--tasks",
            "2",
            "--repeats",
            "1",
            "--model",
            "mockllm/model",
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "verdict:" in result.output

    experiment_dirs = list(tmp_path.glob("experiment-*"))
    assert len(experiment_dirs) == 1
    report_path = experiment_dirs[0] / "report.md"
    assert report_path.exists()

    report_text = report_path.read_text()
    assert any(v in report_text for v in ("PROMOTE", "REJECT", "INCONCLUSIVE", "HOLD"))
    assert "mockllm/model" in report_text
    assert "Seed list: [0, 1]" in report_text
    # mockllm has no litellm price, so the persisted report must carry the
    # cost-honesty caveat on record, not just a transient stderr warning.
    assert "excludes usage from models with no known price" in report_text


def test_run_command_accepts_experiment_001_flags_offline(tmp_path):
    # New knobs from experiment 001: naive/structured arm selection, corpus
    # size, and summary budget, all pass through the CLI offline via mockllm.
    result = runner.invoke(
        app,
        [
            "run",
            "--tasks",
            "2",
            "--repeats",
            "1",
            "--model",
            "mockllm/model",
            "--results-dir",
            str(tmp_path),
            "--n-facts",
            "3",
            "--filler-turns",
            "8",
            "--summary-budget",
            "50",
            "--baseline-style",
            "naive",
            "--candidate-style",
            "structured",
        ],
    )

    assert result.exit_code == 0, result.output

    experiment_dirs = list(tmp_path.glob("experiment-*"))
    assert len(experiment_dirs) == 1
    report_text = (experiment_dirs[0] / "report.md").read_text()
    assert "Baseline arm: `naive`" in report_text
    assert "Candidate arm: `structured`" in report_text


def test_run_command_defaults_preserve_current_arms(tmp_path):
    # No new flags passed: baseline/candidate arms must stay truncate/structured.
    result = runner.invoke(
        app,
        [
            "run",
            "--tasks",
            "2",
            "--repeats",
            "1",
            "--model",
            "mockllm/model",
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    experiment_dirs = list(tmp_path.glob("experiment-*"))
    report_text = (experiment_dirs[0] / "report.md").read_text()
    assert "Baseline arm: `truncate`" in report_text
    assert "Candidate arm: `structured`" in report_text


def test_pilot_command_accepts_experiment_001_flags_offline(tmp_path):
    result = runner.invoke(
        app,
        [
            "pilot",
            "--tasks",
            "2",
            "--repeats",
            "2",
            "--model",
            "mockllm/model",
            "--results-dir",
            str(tmp_path),
            "--n-facts",
            "3",
            "--filler-turns",
            "8",
            "--summary-budget",
            "50",
            "--baseline-style",
            "naive",
            "--candidate-style",
            "structured",
        ],
    )

    assert result.exit_code == 0, result.output

    data = json.loads((tmp_path / "pilot.json").read_text())
    assert data["baseline_style"] == "naive"
    assert data["candidate_style"] == "structured"


def test_pilot_command_writes_pilot_json_offline(tmp_path):
    result = runner.invoke(
        app,
        [
            "pilot",
            "--tasks",
            "2",
            "--repeats",
            "2",
            "--model",
            "mockllm/model",
            "--results-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output

    pilot_path = tmp_path / "pilot.json"
    assert pilot_path.exists()

    data = json.loads(pilot_path.read_text())
    assert data["model"] == "mockllm/model"
    assert data["seeds"] == [0, 1]
    assert data["repeats"] == 2
    assert "sd_task_delta" in data
    assert "required_tasks" in data
    assert data["log_paths"]


def test_check_log_status_raises_and_names_arm_status_and_location(capsys):
    # A live provider failure leaves an EvalLog with status != "success" and
    # no scores; check_log_status must abort cleanly here rather than let a
    # confusing KeyError surface later inside extract_results.
    errored_log = SimpleNamespace(status="error", location="/tmp/fake/logs/truncate.eval")

    with pytest.raises(typer.Exit):
        check_log_status(errored_log, arm_name="truncate")

    captured = capsys.readouterr()
    assert "truncate" in captured.err
    assert "error" in captured.err
    assert "/tmp/fake/logs/truncate.eval" in captured.err


def test_check_log_status_passes_on_success():
    successful_log = SimpleNamespace(status="success", location="/tmp/fake/logs/structured.eval")

    check_log_status(successful_log, arm_name="structured")  # must not raise
