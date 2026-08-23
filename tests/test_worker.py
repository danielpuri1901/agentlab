"""Cloud worker commands, fully offline: moto stands in for S3/DynamoDB,
mockllm stands in for the model provider. No test in this file touches
live AWS or makes a network call.
"""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from agentlab import notify as notify_mod
from agentlab.cli import app
from agentlab.eval_runner import _run_arm
from agentlab.notify import CHAT_ID_PARAM, TOKEN_PARAM, notify
from agentlab.results import extract_results, mean_score
from agentlab.stats import paired_analysis
from agentlab.stats import verdict as compute_verdict
from agentlab.worker import _optional_int_env, transition, upload_log

runner = CliRunner()

BUCKET = "agentlab-results-test"
TABLE = "agentlab-state-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")


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


@pytest.fixture
def moto_fabric_with_ssm(moto_fabric):
    # Same s3/dynamodb/table as moto_fabric, plus an SSM client with both
    # telegram parameters loaded (mirrors tests/test_notify.py's `fabric`
    # fixture) so finalize's ping path can load_config successfully.
    s3, dynamodb, table = moto_fabric
    ssm = boto3.client("ssm", region_name=REGION)
    ssm.put_parameter(Name=TOKEN_PARAM, Value="test-token", Type="SecureString")
    ssm.put_parameter(Name=CHAT_ID_PARAM, Value="6309668956", Type="String")
    yield s3, dynamodb, table, ssm


@pytest.fixture
def telegram_calls(monkeypatch):
    calls = []

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        return FakeResponse()

    monkeypatch.setattr(notify_mod.httpx, "post", fake_post)
    return calls


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
    events = {item.get("event") for item in items}
    assert {"ARM_STARTED", "ARM_COMPLETED"} <= events
    assert all(item["arm"] == "structured" for item in items)


def _set_run_arm_env(monkeypatch, experiment_id):
    monkeypatch.setenv("EXPERIMENT_ID", experiment_id)
    monkeypatch.setenv("ARM_STYLE", "structured")
    monkeypatch.setenv("MODEL", "mockllm/model")
    monkeypatch.setenv("TASKS", "2")
    monkeypatch.setenv("REPEATS", "1")
    monkeypatch.setenv("N_FACTS", "3")
    monkeypatch.setenv("FILLER_TURNS", "8")
    monkeypatch.setenv("SUMMARY_BUDGET", "50")
    monkeypatch.setenv("RESULTS_BUCKET", BUCKET)
    monkeypatch.setenv("STATE_TABLE", TABLE)


def test_run_arm_generic_failure_writes_arm_failed_and_exits_nonzero(moto_fabric, monkeypatch):
    _s3, _dynamodb, table = moto_fabric
    _set_run_arm_env(monkeypatch, "exp-test-fail-generic")

    async def failing_run_arm(*args, **kwargs):
        raise RuntimeError("boom: simulated provider failure")

    # Patches the name as looked up inside agentlab.worker (imported there
    # from eval_runner), not the original definition.
    monkeypatch.setattr("agentlab.worker._run_arm", failing_run_arm)

    result = runner.invoke(app, ["worker", "run-arm"])

    assert result.exit_code != 0

    items = table.scan()["Items"]
    failed = [item for item in items if item.get("event") == "ARM_FAILED"]
    assert len(failed) == 1
    assert failed[0]["detail"] == "boom: simulated provider failure"
    assert failed[0]["arm"] == "structured"


def test_run_arm_check_log_status_failure_writes_diagnosis_not_bare_exit_code(
    moto_fabric, monkeypatch
):
    # Regression test: `check_log_status` raising a bare `typer.Exit(1)`
    # means `str(exc)` is just "1" (click's Exit never calls
    # `super().__init__(message)`); the ARM_FAILED item must carry the real
    # diagnosis (arm name, status, log location) instead.
    _s3, _dynamodb, table = moto_fabric
    _set_run_arm_env(monkeypatch, "exp-test-fail-status")

    async def fake_eval_async(*args, **kwargs):
        return [SimpleNamespace(status="error", location="/tmp/fake/logs/structured.eval")]

    monkeypatch.setattr("agentlab.eval_runner.eval_async", fake_eval_async)

    result = runner.invoke(app, ["worker", "run-arm"])

    assert result.exit_code != 0

    items = table.scan()["Items"]
    failed = [item for item in items if item.get("event") == "ARM_FAILED"]
    assert len(failed) == 1
    detail = failed[0]["detail"]
    assert detail != "1"
    assert "structured" in detail
    assert "/tmp/fake/logs/structured.eval" in detail


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
    finalized = [item for item in items if item.get("event") == "FINALIZED"]
    assert len(finalized) == 1
    assert finalized[0]["detail"] == expected_verdict


def test_flush_pings_command(moto_fabric_with_ssm, telegram_calls, monkeypatch):
    _s3, _dynamodb, table, ssm = moto_fabric_with_ssm
    # Queue one ping during quiet hours, directly via notify() (no CLI
    # involved yet), so there is exactly one pending item for the
    # flush-pings command to deliver.
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 2, 0, tzinfo=AMS)
    )
    status = notify(table, ssm, "queued during quiet hours")
    assert status == "queued"
    assert telegram_calls == []

    result = runner.invoke(app, ["worker", "flush-pings"], env={"STATE_TABLE": TABLE})

    assert result.exit_code == 0, result.output
    assert "flushed 1" in result.output
    assert len(telegram_calls) == 1


def test_propose_command_flushes_then_proposes(monkeypatch):
    # The propose command itself is a thin wrapper: it flushes queued pings,
    # then delegates proposing to agentlab.proposer.run_propose (tested in
    # depth in tests/test_proposer.py). Both collaborators are faked here so
    # this test only pins the CLI's own wiring and echo line, not AWS calls.
    monkeypatch.setattr("agentlab.worker.flush_pending", lambda table, ssm_client: 0)
    monkeypatch.setattr(
        "agentlab.proposer.run_propose",
        lambda table, ssm_client, s3_client, bucket, model: 2,
    )

    result = runner.invoke(
        app,
        ["worker", "propose"],
        env={"STATE_TABLE": TABLE, "RESULTS_BUCKET": BUCKET},
    )

    assert result.exit_code == 0, result.output
    assert "filed 2 proposals" in result.output


def test_finalize_sends_ping_with_chart(
    moto_fabric_with_ssm, telegram_calls, monkeypatch, tmp_path
):
    s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    experiment_id = "exp-test-ping"
    seeds = [0, 1]

    baseline_log = _make_log(tmp_path / "logs", "truncate", seeds)
    candidate_log = _make_log(tmp_path / "logs", "structured", seeds)

    upload_log(s3, BUCKET, experiment_id, "truncate", baseline_log.location)
    upload_log(s3, BUCKET, experiment_id, "structured", candidate_log.location)

    # Ground truth computed directly against the same two logs, independent
    # of the `finalize` command under test, so the caption assertions below
    # pin the exact formatted numbers finalize is expected to produce.
    baseline_results = extract_results(baseline_log.location)
    candidate_results = extract_results(candidate_log.location)
    expected_result = paired_analysis(baseline_results.scores, candidate_results.scores)
    expected_baseline_mean = mean_score(baseline_results.scores)
    expected_candidate_mean = mean_score(candidate_results.scores)

    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )

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

    assert len(telegram_calls) >= 1
    url, kwargs = telegram_calls[-1]
    assert url.endswith("/sendPhoto")
    caption = kwargs["data"]["caption"]
    assert "Verdict:" in caption
    assert experiment_id in caption
    assert f"structured scores {expected_candidate_mean:.3f}" in caption
    assert f"truncate scores {expected_baseline_mean:.3f}" in caption
    assert "95% CI" in caption
    assert (
        f"{expected_result.ci_low:+.3f} to {expected_result.ci_high:+.3f}" in caption
    )
    assert f"s3://{BUCKET}/experiments/{experiment_id}/report.md" in caption

    items = table.scan()["Items"]
    assert not [item for item in items if item.get("event") == "PING_FAILED"]


def test_finalize_ping_failure_records_transition_not_crash(
    moto_fabric_with_ssm, monkeypatch, tmp_path
):
    s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    experiment_id = "exp-test-ping-fail"
    seeds = [0, 1]

    baseline_log = _make_log(tmp_path / "logs", "truncate", seeds)
    candidate_log = _make_log(tmp_path / "logs", "structured", seeds)

    upload_log(s3, BUCKET, experiment_id, "truncate", baseline_log.location)
    upload_log(s3, BUCKET, experiment_id, "structured", candidate_log.location)

    def exploding_notify(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("agentlab.worker.notify", exploding_notify)

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

    items = table.scan()["Items"]
    ping_failed = [item for item in items if item.get("event") == "PING_FAILED"]
    assert len(ping_failed) == 1
    assert ping_failed[0]["detail"] == "boom"

    finalized = [item for item in items if item.get("event") == "FINALIZED"]
    assert len(finalized) == 1


def test_finalize_ping_and_ping_failed_write_both_fail_still_exits_zero(
    moto_fabric_with_ssm, monkeypatch, tmp_path
):
    # Cascading-failure case: notify() raises AND the PING_FAILED transition
    # write itself raises (e.g. the same outage took down DynamoDB too). The
    # invariant is that nothing after FINALIZED may fail the command, so
    # finalize must still exit 0 - the second failure is swallowed and
    # surfaced only as stderr output, not a nonzero exit.
    s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    experiment_id = "exp-test-ping-cascade-fail"
    seeds = [0, 1]

    baseline_log = _make_log(tmp_path / "logs", "truncate", seeds)
    candidate_log = _make_log(tmp_path / "logs", "structured", seeds)

    upload_log(s3, BUCKET, experiment_id, "truncate", baseline_log.location)
    upload_log(s3, BUCKET, experiment_id, "structured", candidate_log.location)

    def exploding_notify(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("agentlab.worker.notify", exploding_notify)

    real_transition = transition

    def selectively_exploding_transition(table_, experiment_id_, event, arm, detail, ts=None):
        if event == "PING_FAILED":
            raise RuntimeError("dynamo also down")
        return real_transition(table_, experiment_id_, event, arm, detail, ts=ts)

    monkeypatch.setattr("agentlab.worker.transition", selectively_exploding_transition)

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
    assert "ping failed and PING_FAILED write failed" in result.output

    items = table.scan()["Items"]
    finalized = [item for item in items if item.get("event") == "FINALIZED"]
    assert len(finalized) == 1
    assert not [item for item in items if item.get("event") == "PING_FAILED"]


def test_optional_int_env_treats_jsonata_null_string_as_absent(monkeypatch):
    # Regression test: infra/stepfunctions.tf's JSONata $string(null) renders
    # a null MAX_CONNECTIONS as the literal string "null" (not an empty or
    # missing env var) in the ECS container override, which int() previously
    # rejected with a ValueError before any DynamoDB transition was written.
    monkeypatch.setenv("MAX_CONNECTIONS", "null")
    assert _optional_int_env("MAX_CONNECTIONS") is None

    monkeypatch.setenv("MAX_CONNECTIONS", "None")
    assert _optional_int_env("MAX_CONNECTIONS") is None

    monkeypatch.setenv("MAX_CONNECTIONS", "NULL")
    assert _optional_int_env("MAX_CONNECTIONS") is None

    monkeypatch.setenv("MAX_CONNECTIONS", "")
    assert _optional_int_env("MAX_CONNECTIONS") is None

    monkeypatch.delenv("MAX_CONNECTIONS", raising=False)
    assert _optional_int_env("MAX_CONNECTIONS") is None

    monkeypatch.setenv("MAX_CONNECTIONS", "30")
    assert _optional_int_env("MAX_CONNECTIONS") == 30


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


def test_run_arm_failure_sends_ping(moto_fabric, monkeypatch):
    # A failed arm must ping Daniel (the Grok intake run died silently 3x on
    # AccessDenied and nobody knew for a day). The ping is best-effort: here
    # we capture it via a monkeypatched notify and assert content.
    _s3, _dynamodb, table = moto_fabric
    pings = []
    monkeypatch.setattr(
        "agentlab.worker.notify",
        lambda tbl, ssm, text, **kw: pings.append(text) or "sent",
    )

    def exploding_run_arm(*args, **kwargs):
        raise RuntimeError("AccessDeniedException: model not available")

    monkeypatch.setattr("agentlab.worker._run_arm", exploding_run_arm)
    env = {
        "EXPERIMENT_ID": "exp-fail-ping",
        "ARM_STYLE": "truncate",
        "MODEL": "bedrock/global.xai.grok-4.6",
        "TASKS": "2",
        "REPEATS": "1",
        "N_FACTS": "3",
        "FILLER_TURNS": "6",
        "SUMMARY_BUDGET": "50",
        "RESULTS_BUCKET": BUCKET,
        "STATE_TABLE": TABLE,
    }
    result = runner.invoke(app, ["worker", "run-arm"], env=env)

    assert result.exit_code != 0
    assert len(pings) == 1
    assert "FAILED" in pings[0] and "exp-fail-ping" in pings[0]
    events = [i["event"] for i in table.scan()["Items"]]
    assert "ARM_FAILED" in events


def test_run_arm_failure_ping_failure_does_not_mask_error(moto_fabric, monkeypatch):
    # If the failure ping itself fails, the original arm error must still
    # surface (non-zero exit + ARM_FAILED recorded), never the ping error.
    _s3, _dynamodb, table = moto_fabric

    def exploding_notify(*args, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("agentlab.worker.notify", exploding_notify)
    monkeypatch.setattr(
        "agentlab.worker._run_arm",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("real arm error")),
    )
    env = {
        "EXPERIMENT_ID": "exp-double-fail",
        "ARM_STYLE": "truncate",
        "MODEL": "bedrock/eu.amazon.nova-lite-v1:0",
        "TASKS": "2",
        "REPEATS": "1",
        "N_FACTS": "3",
        "FILLER_TURNS": "6",
        "SUMMARY_BUDGET": "50",
        "RESULTS_BUCKET": BUCKET,
        "STATE_TABLE": TABLE,
    }
    result = runner.invoke(app, ["worker", "run-arm"], env=env)

    assert result.exit_code != 0
    items = table.scan()["Items"]
    failed = [i for i in items if i["event"] == "ARM_FAILED"]
    assert len(failed) == 1 and "real arm error" in failed[0]["detail"]
