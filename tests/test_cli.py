import json

from typer.testing import CliRunner

from agentlab.cli import app

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
