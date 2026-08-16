"""Cloud worker commands, fully offline: moto stands in for S3/DynamoDB,
mockllm stands in for the model provider. No test in this file touches
live AWS or makes a network call.
"""

import asyncio

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from agentlab.cli import app
from agentlab.eval_runner import _run_arm
from agentlab.results import extract_results
from agentlab.stats import paired_analysis
from agentlab.stats import verdict as compute_verdict
from agentlab.worker import transition, upload_log

runner = CliRunner()

BUCKET = "agentlab-results-test"
TABLE = "agentlab-state-test"
REGION = "us-east-1"


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    # moto never calls real AWS, but boto3 still needs *some* creds/region to
    # construct a client; these are fake and only used against the mock.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def moto_fabric():
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=BUCKET)
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        table = dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "experiment_id", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "experiment_id", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        yield s3, dynamodb, table


def _make_log(log_dir, style, seeds):
    return asyncio.run(
        _run_arm(
            style=style,
            model="mockllm/model",
            seeds=seeds,
            repeats=1,
            log_dir=log_dir,
            summary_budget=50,
            n_facts=3,
            filler_turns=8,
        )
    )


def test_run_arm_uploads_log_and_writes_transitions(moto_fabric, monkeypatch):
    s3, _dynamodb, table = moto_fabric
    monkeypatch.setenv("EXPERIMENT_ID", "exp-test-1")
    monkeypatch.setenv("ARM_STYLE", "structured")
    monkeypatch.setenv("MODEL", "mockllm/model")
    monkeypatch.setenv("TASKS", "2")
    monkeypatch.setenv("REPEATS", "1")
    monkeypatch.setenv("N_FACTS", "3")
    monkeypatch.setenv("FILLER_TURNS", "8")
    monkeypatch.setenv("SUMMARY_BUDGET", "50")
    monkeypatch.setenv("RESULTS_BUCKET", BUCKET)
    monkeypatch.setenv("STATE_TABLE", TABLE)

    result = runner.invoke(app, ["worker", "run-arm"])

    assert result.exit_code == 0, result.output

    objects = s3.list_objects_v2(Bucket=BUCKET, Prefix="experiments/exp-test-1/logs/")
    keys = [obj["Key"] for obj in objects.get("Contents", [])]
    assert keys == ["experiments/exp-test-1/logs/structured.eval"]

    items = table.scan()["Items"]
    events = {item["event"] for item in items}
    assert {"ARM_STARTED", "ARM_COMPLETED"} <= events
    assert all(item["arm"] == "structured" for item in items)


def test_finalize_writes_report_and_matches_local_verdict(moto_fabric, monkeypatch, tmp_path):
    s3, _dynamodb, table = moto_fabric
    experiment_id = "exp-test-2"
    seeds = [0, 1]

    baseline_log = _make_log(tmp_path / "logs", "truncate", seeds)
    candidate_log = _make_log(tmp_path / "logs", "structured", seeds)

    upload_log(s3, BUCKET, experiment_id, "truncate", baseline_log.location)
    upload_log(s3, BUCKET, experiment_id, "structured", candidate_log.location)

    # Ground truth computed directly against the same two logs, independent
    # of the `finalize` command under test.
    baseline_results = extract_results(baseline_log.location)
    candidate_results = extract_results(candidate_log.location)
    expected_result = paired_analysis(baseline_results.scores, candidate_results.scores)
    expected_verdict = compute_verdict(expected_result, protected=[])

    monkeypatch.setenv("EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("MODEL", "mockllm/model")
    monkeypatch.setenv("TASKS", "2")
    monkeypatch.setenv("REPEATS", "1")
    monkeypatch.setenv("BASELINE_STYLE", "truncate")
    monkeypatch.setenv("CANDIDATE_STYLE", "structured")
    monkeypatch.setenv("RESULTS_BUCKET", BUCKET)
    monkeypatch.setenv("STATE_TABLE", TABLE)

    result = runner.invoke(app, ["worker", "finalize"])
    assert result.exit_code == 0, result.output

    report_obj = s3.get_object(Bucket=BUCKET, Key=f"experiments/{experiment_id}/report.md")
    report_text = report_obj["Body"].read().decode("utf-8")
    assert expected_verdict in report_text
    assert "mockllm/model" in report_text

    items = table.scan()["Items"]
    finalized = [item for item in items if item["event"] == "FINALIZED"]
    assert len(finalized) == 1
    assert finalized[0]["detail"] == expected_verdict


def test_transition_is_idempotent_on_retry(moto_fabric):
    _s3, _dynamodb, table = moto_fabric
    ts = "2026-08-16T00:00:00.000000Z"

    transition(table, "exp-idem", "ARM_STARTED", "structured", "model=mockllm/model", ts=ts)
    # Simulates a retried task re-emitting the identical event: must not
    # duplicate the item or raise.
    transition(table, "exp-idem", "ARM_STARTED", "structured", "model=mockllm/model", ts=ts)

    items = table.scan()["Items"]
    assert len(items) == 1
    assert items[0]["event"] == "ARM_STARTED"
