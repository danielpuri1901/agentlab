"""Cloud CLI commands (submit/status/report), fully offline: moto stands in
for SQS/DynamoDB/S3. No test in this file touches live AWS or makes a
network call.
"""

import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from agentlab.cli import app
from agentlab.cloud import generate_experiment_id
from agentlab.worker import transition, upload_report

runner = CliRunner()

BUCKET = "agentlab-results-test"
TABLE = "agentlab-state-test"
QUEUE_NAME = "agentlab-experiments-test"
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
        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]
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
        yield s3, sqs, queue_url, dynamodb, table


def _submit_env(monkeypatch, queue_url):
    monkeypatch.setenv("AGENTLAB_QUEUE_URL", queue_url)
    monkeypatch.setenv("AGENTLAB_STATE_TABLE", TABLE)


def test_generate_experiment_id_format_with_injected_clock(monkeypatch):
    fixed_now = datetime(2026, 8, 16, 15, 23, 15, tzinfo=UTC)
    monkeypatch.setattr("agentlab.cloud.now", lambda: fixed_now)
    monkeypatch.setattr("agentlab.cloud.random_hex", lambda: "ab12")

    experiment_id = generate_experiment_id()

    assert experiment_id == "exp-20260816T152315Z-ab12"


def test_submit_sends_message_with_exact_contract_fields_and_writes_created(
    moto_fabric, monkeypatch
):
    _s3, sqs, queue_url, _dynamodb, table = moto_fabric
    _submit_env(monkeypatch, queue_url)
    monkeypatch.setattr(
        "agentlab.cloud.now", lambda: datetime(2026, 8, 16, 15, 23, 15, tzinfo=UTC)
    )
    monkeypatch.setattr("agentlab.cloud.random_hex", lambda: "ab12")

    result = runner.invoke(
        app,
        [
            "cloud",
            "submit",
            "--model",
            "bedrock/eu.amazon.nova-lite-v1:0",
            "--tasks",
            "51",
            "--repeats",
            "5",
            "--n-facts",
            "12",
            "--filler-turns",
            "120",
            "--summary-budget",
            "150",
            "--max-connections",
            "30",
            "--baseline-style",
            "structured",
            "--candidate-style",
            "codes_first",
        ],
    )

    assert result.exit_code == 0, result.output
    experiment_id = "exp-20260816T152315Z-ab12"
    assert experiment_id in result.output

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    received = messages.get("Messages", [])
    assert len(received) == 1
    body = json.loads(received[0]["Body"])

    assert body == {
        "experiment_id": experiment_id,
        "model": "bedrock/eu.amazon.nova-lite-v1:0",
        "tasks": 51,
        "repeats": 5,
        "n_facts": 12,
        "filler_turns": 120,
        "summary_budget": 150,
        "max_connections": 30,
        "baseline_style": "structured",
        "candidate_style": "codes_first",
    }

    items = table.scan()["Items"]
    created = [item for item in items if item["event"] == "CREATED"]
    assert len(created) == 1
    assert created[0]["experiment_id"] == experiment_id


def test_submit_rejects_bad_style(moto_fabric, monkeypatch):
    _s3, _sqs, queue_url, _dynamodb, table = moto_fabric
    _submit_env(monkeypatch, queue_url)

    result = runner.invoke(
        app,
        [
            "cloud",
            "submit",
            "--model",
            "mockllm/model",
            "--tasks",
            "2",
            "--repeats",
            "1",
            "--baseline-style",
            "not-a-real-style; DROP TABLE",
        ],
    )

    assert result.exit_code != 0
    assert "not-a-real-style; DROP TABLE" in result.output
    # Nothing should have been written or sent.
    assert table.scan()["Items"] == []


def test_submit_rejects_bad_model(moto_fabric, monkeypatch):
    _s3, _sqs, queue_url, _dynamodb, table = moto_fabric
    _submit_env(monkeypatch, queue_url)

    result = runner.invoke(
        app,
        [
            "cloud",
            "submit",
            "--model",
            'mockllm/model", "injected": "true',
            "--tasks",
            "2",
            "--repeats",
            "1",
        ],
    )

    assert result.exit_code != 0
    assert table.scan()["Items"] == []


def test_status_renders_transitions_and_finalized_verdict(moto_fabric, monkeypatch):
    _s3, _sqs, _queue_url, _dynamodb, table = moto_fabric
    monkeypatch.setenv("AGENTLAB_STATE_TABLE", TABLE)
    monkeypatch.setenv("AGENTLAB_RESULTS_BUCKET", BUCKET)
    experiment_id = "exp-test-status"

    transition(table, experiment_id, "CREATED", None, "model=mockllm/model", ts="2026-08-16T00:00:00.000000Z")
    transition(table, experiment_id, "ARM_STARTED", "truncate", "model=mockllm/model", ts="2026-08-16T00:00:01.000000Z")
    transition(table, experiment_id, "ARM_COMPLETED", "truncate", "s3-key", ts="2026-08-16T00:00:02.000000Z")
    transition(table, experiment_id, "FINALIZED", None, "PROMOTE: delta=0.12", ts="2026-08-16T00:00:03.000000Z")

    result = runner.invoke(app, ["cloud", "status", experiment_id])

    assert result.exit_code == 0, result.output
    assert "CREATED" in result.output
    assert "ARM_STARTED" in result.output
    assert "ARM_COMPLETED" in result.output
    assert "FINALIZED" in result.output
    assert "PROMOTE" in result.output
    assert f"experiments/{experiment_id}/report.md" in result.output
    # Transitions render in ts order.
    assert result.output.index("CREATED") < result.output.index("ARM_STARTED")
    assert result.output.index("ARM_STARTED") < result.output.index("ARM_COMPLETED")
    assert result.output.index("ARM_COMPLETED") < result.output.index("FINALIZED")


def test_status_without_finalized_item_omits_verdict(moto_fabric, monkeypatch):
    _s3, _sqs, _queue_url, _dynamodb, table = moto_fabric
    monkeypatch.setenv("AGENTLAB_STATE_TABLE", TABLE)
    monkeypatch.setenv("AGENTLAB_RESULTS_BUCKET", BUCKET)
    experiment_id = "exp-test-in-progress"

    transition(table, experiment_id, "CREATED", None, "model=mockllm/model", ts="2026-08-16T00:00:00.000000Z")

    result = runner.invoke(app, ["cloud", "status", experiment_id])

    assert result.exit_code == 0, result.output
    assert "CREATED" in result.output
    assert "report.md" not in result.output


def test_report_fetches_and_prints(moto_fabric, monkeypatch):
    s3, _sqs, _queue_url, _dynamodb, _table = moto_fabric
    monkeypatch.setenv("AGENTLAB_RESULTS_BUCKET", BUCKET)
    experiment_id = "exp-test-report"
    upload_report(s3, BUCKET, experiment_id, "# Report\n\nverdict: PROMOTE\n")

    result = runner.invoke(app, ["cloud", "report", experiment_id])

    assert result.exit_code == 0, result.output
    assert "verdict: PROMOTE" in result.output
