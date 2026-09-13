"""Cloud worker commands, fully offline: moto stands in for S3/DynamoDB,
mockllm stands in for the model provider. No test in this file touches
live AWS or makes a network call.
"""

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws
from typer.testing import CliRunner

from agentlab import notify as notify_mod
from agentlab import worker as worker_mod
from agentlab.cli import app
from agentlab.eval_runner import _run_arm
from agentlab.notify import CHAT_ID_PARAM, TOKEN_PARAM, notify
from agentlab.results import extract_results, mean_score
from agentlab.scene_plan import ScenePlan
from agentlab.stats import paired_analysis
from agentlab.stats import verdict as compute_verdict
from agentlab.story_video import StoryFailed, StoryResult
from agentlab.storyboard import Storyboard
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


def test_flush_pings_command_survives_flush_pending_failure(moto_fabric, monkeypatch):
    # Matches propose_command's own guard: a stuck queued ping (Telegram
    # down, a bad payload, whatever) must not fail the whole scheduled
    # flush-pings run.
    _s3, _dynamodb, _table = moto_fabric

    def exploding_flush_pending(*args, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr("agentlab.worker.flush_pending", exploding_flush_pending)

    result = runner.invoke(app, ["worker", "flush-pings"], env={"STATE_TABLE": TABLE})

    assert result.exit_code == 0, result.output
    assert "flush failed, continuing" in result.output
    assert "flushed 0" in result.output


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


# ---------------------------------------------------------------------------
# `worker explain`: three tracks (core/classic/novel), fully offline.
# gather_exploit/gather_explore/pick_paper/deep_read/verify_voice/narrate/
# render_video are all monkeypatched on `agentlab.worker` (the names looked
# up at call time inside explain_command's own module, same pattern as
# `agentlab.worker._run_arm` above); DynamoDB/S3/Telegram stay real against
# moto/the monkeypatched httpx.post, so mark_seen/is_seen, the S3 digest
# upload + presign, and the video ledger item all run for real.
# ---------------------------------------------------------------------------

CORE_CANDIDATE = {
    "source": "arxiv",
    "title": "Core Candidate Paper",
    "url": "https://arxiv.org/abs/9911.00001",
    "pool": "exploit",
}
NOVEL_CANDIDATE = {
    "source": "hf",
    "title": "Novel Candidate Paper",
    "url": "https://arxiv.org/abs/9911.00002",
    "pool": "explore",
}
ATTENTION_URL = "https://arxiv.org/abs/1706.03762"
COT_URL = "https://arxiv.org/abs/2201.11903"
GOLDEN_BOARD = json.loads(
    (Path(__file__).parent / "fixtures" / "storyboard_golden.json").read_text(
        encoding="utf-8"
    )
)


def _make_scene_plan(url: str, title: str) -> ScenePlan:
    return ScenePlan(
        title=title[:70],
        one_line_claim=f"{title} shows something new.",
        diagram={
            "nodes": [{"id": "input", "label": "Input"}, {"id": "output", "label": "Output"}],
            "edges": [{"source": "input", "target": "output"}],
        },
        mechanism_steps=[
            {
                "label": "Step 1",
                "detail": "First mechanism detail.",
                "narration": "First, this happens.",
                "activates": ["input"],
            },
            {
                "label": "Step 2",
                "detail": "Second mechanism detail.",
                "narration": "Then, this happens.",
                "activates": ["input->output"],
            },
            {
                "label": "Step 3",
                "detail": "Third mechanism detail.",
                "narration": "Finally, this happens.",
                "activates": ["output"],
            },
        ],
        key_numbers=[],
        limits_or_caveats="The paper does not claim to solve everything.",
        street_test_question="Would you implement this in your own harness?",
        citation_url=url,
    )


def _make_fake_deep_read(fail_urls=frozenset()):
    def fake_deep_read(url, fetch_text, complete, model=None):
        if url in fail_urls:
            raise ValueError(f"scene plan invalid after retry for {url}: boom")
        digest = f"# Digest\n\nContent for {url}.\n\n## Limits\n\nNone stated."
        return digest, _make_scene_plan(url, f"Paper at {url}")

    return fake_deep_read


def _fake_pick_paper(candidates, interests_text, complete, mode="core", model=None):
    return candidates[0]


def _fake_rank_papers(
    candidates, interests_text, complete, mode="core", model=None, limit=3
):
    return candidates[:limit]


def _fake_render_video(plan, clips, out_path):
    Path(out_path).write_bytes(b"fake-mp4-bytes")
    return Path(out_path)


def _fake_story_failed(*args, **kwargs):
    raise StoryFailed("test: forced fallback")


def _fake_story_success(
    digest, plan, polly_client, voice_id, complete, work_dir, out_path, **kwargs
):
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(b"story-mp4")
    srt = out_path.with_suffix(".srt")
    srt.write_text("1\n00:00:00,000 --> 00:00:05,000\nhi\n", encoding="utf-8")
    return StoryResult(
        video_path=out_path,
        srt_path=srt,
        storyboard=Storyboard(**GOLDEN_BOARD),
        scene_source="class PaperStory: pass",
        attempts=2,
        judge_score=8,
        judgement={"score": 8, "beats": [], "verdict": "pass", "note": None},
        timing={"beats": [], "total": 25.0},
    )


def _set_explain_env(monkeypatch, track="all"):
    monkeypatch.setenv("STATE_TABLE", TABLE)
    monkeypatch.setenv("RESULTS_BUCKET", BUCKET)
    monkeypatch.setenv("TRACK", track)


def _set_daytime(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )


def _patch_explain_render_stages(monkeypatch, fail_urls=frozenset(), story=None):
    monkeypatch.setattr("agentlab.worker.deep_read", _make_fake_deep_read(fail_urls))
    monkeypatch.setattr("agentlab.worker.verify_voice", lambda polly_client: "Joanna")
    monkeypatch.setattr(
        "agentlab.worker.narrate", lambda polly_client, texts, voice_id, out_dir: ["clip"]
    )
    monkeypatch.setattr("agentlab.worker.render_video", _fake_render_video)
    monkeypatch.setattr("agentlab.worker.compose_story_video", story or _fake_story_failed)


def _patch_explain_pools(monkeypatch):
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    monkeypatch.setattr("agentlab.worker.gather_explore", lambda: [NOVEL_CANDIDATE])
    monkeypatch.setattr("agentlab.worker.pick_paper", _fake_pick_paper)
    monkeypatch.setattr("agentlab.worker.rank_papers", _fake_rank_papers)


def test_complete_long_allows_story_output_budget_and_timeout(monkeypatch):
    calls = []

    def fake_completion(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="complete"))]
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))

    messages = [{"role": "user", "content": "Write the scene."}]
    assert worker_mod._complete_long("bedrock/story-model", messages) == "complete"
    assert calls == [
        {
            "model": "bedrock/story-model",
            "messages": messages,
            "max_tokens": 12000,
            "timeout": 600,
        }
    ]


@pytest.mark.parametrize("finish_reason", ["length", "max_tokens"])
def test_complete_long_rejects_truncated_output(monkeypatch, finish_reason):
    def fake_completion(**kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content="```python\npartial"),
                )
            ]
        )

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=fake_completion))

    with pytest.raises(ValueError, match="token limit"):
        worker_mod._complete_long("bedrock/story-model", [])


def test_explain_story_path_ships_and_records_artifacts(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _set_daytime(monkeypatch)
    _set_explain_env(monkeypatch, track="core")
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert "explain: core=sent" in result.output

    table = boto3.resource("dynamodb").Table(TABLE)
    videos = [i for i in table.scan()["Items"] if i["experiment_id"].startswith("video#")]
    assert len(videos) == 1
    row = videos[0]
    assert row["render_path"] == "story"
    assert row["attempts"] == 2 and row["judge_score"] == 8
    key = row["experiment_id"].removeprefix("video#")
    assert row["story_key"] == f"stories/{key}.json"

    s3 = boto3.client("s3")
    keys = {o["Key"] for o in s3.list_objects_v2(Bucket=BUCKET)["Contents"]}
    assert f"stories/{key}.json" in keys and f"stories/{key}.py" in keys
    with s3.get_object(Bucket=BUCKET, Key=f"stories/{key}.json")["Body"] as body:
        story = json.loads(body.read())
    assert story["storyboard"]["title"] == "Recursive Self-Improvement in AI"
    assert story["judgement"]["score"] == 8

    events = [i for i in table.scan()["Items"] if i["sk"].startswith("event#")]
    assert not any(e["event"] == "STORY_FALLBACK" for e in events)


def test_explain_story_failure_falls_back_to_template_and_logs(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _set_daytime(monkeypatch)
    _set_explain_env(monkeypatch, track="core")
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert "explain: core=sent" in result.output

    table = boto3.resource("dynamodb").Table(TABLE)
    items = table.scan()["Items"]
    row = next(i for i in items if i["experiment_id"].startswith("video#"))
    assert row["render_path"] == "template"
    assert row["story_key"] is None
    fallback = [
        i
        for i in items
        if i["sk"].startswith("event#") and i["event"] == "STORY_FALLBACK"
    ]
    assert len(fallback) == 1
    assert "forced fallback" in fallback[0]["detail"]
    assert fallback[0]["arm"] == "core"


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        (
            {"DEEP_READ_MODEL": "bedrock/deep-model"},
            {
                "story_model": "bedrock/deep-model",
                "scene_model": "bedrock/deep-model",
                "judge_model": "bedrock/deep-model",
            },
        ),
        (
            {
                "DEEP_READ_MODEL": "bedrock/deep-model",
                "STORY_MODEL": "bedrock/story-model",
                "SCENE_MODEL": "bedrock/scene-model",
                "JUDGE_MODEL": "bedrock/judge-model",
            },
            {
                "story_model": "bedrock/story-model",
                "scene_model": "bedrock/scene-model",
                "judge_model": "bedrock/judge-model",
            },
        ),
    ],
)
def test_explain_passes_story_model_defaults_and_overrides(
    moto_fabric_with_ssm, telegram_calls, monkeypatch, env, expected
):
    _set_daytime(monkeypatch)
    _set_explain_env(monkeypatch, track="core")
    _patch_explain_pools(monkeypatch)
    captured = {}

    def fake_story(*args, **kwargs):
        captured.update(kwargs)
        return _fake_story_success(*args, **kwargs)

    _patch_explain_render_stages(monkeypatch, story=fake_story)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert captured == expected


def test_explain_all_three_tracks_send_three_videos_and_mark_seen(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="all")
    _set_daytime(monkeypatch)
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output

    sendvideo_calls = [c for c in telegram_calls if c[0].endswith("/sendVideo")]
    assert len(sendvideo_calls) == 3

    items = table.scan()["Items"]
    video_items = [i for i in items if i.get("sk") == "video"]
    assert len(video_items) == 3
    assert {i["track"] for i in video_items} == {"core", "classic", "novel"}

    seen_items = [i for i in items if i.get("sk") == "paper"]
    assert len(seen_items) == 3
    assert {i["track"] for i in seen_items} == {"core", "classic", "novel"}

    assert "explain: core=sent classic=sent novel=sent" in result.output


def test_explain_track_core_runs_only_core(moto_fabric_with_ssm, telegram_calls, monkeypatch):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="core")
    _set_daytime(monkeypatch)
    novel_pool_calls = []
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    monkeypatch.setattr(
        "agentlab.worker.gather_explore",
        lambda: novel_pool_calls.append(1) or [NOVEL_CANDIDATE],
    )
    monkeypatch.setattr("agentlab.worker.pick_paper", _fake_pick_paper)
    monkeypatch.setattr("agentlab.worker.rank_papers", _fake_rank_papers)
    _patch_explain_render_stages(monkeypatch)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output
    assert novel_pool_calls == []

    sendvideo_calls = [c for c in telegram_calls if c[0].endswith("/sendVideo")]
    assert len(sendvideo_calls) == 1

    video_items = [i for i in table.scan()["Items"] if i.get("sk") == "video"]
    assert len(video_items) == 1
    assert video_items[0]["track"] == "core"

    assert result.output.strip().endswith("explain: core=sent")


def test_explain_uses_gated_feedback_and_collects_clarity(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="core")
    _set_daytime(monkeypatch)
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    monkeypatch.setattr(
        "agentlab.worker.load_preference_context",
        lambda table_arg, path: "Preference evidence (topic-feedback-v1).",
    )
    captured = {"calls": []}

    def rank(candidates, interests_text, complete, mode="core", model=None, limit=3):
        captured["calls"].append(list(candidates))
        return candidates[:limit]

    def pick(candidates, interests_text, complete, mode="core", model=None):
        captured["calls"].append(list(candidates))
        captured["interests"] = interests_text
        return candidates[0]

    monkeypatch.setattr("agentlab.worker.pick_paper", pick)
    monkeypatch.setattr("agentlab.worker.rank_papers", rank)
    _patch_explain_render_stages(monkeypatch, story=_fake_story_success)
    real_notify = worker_mod.notify

    def notify_after_ledger(*args, **kwargs):
        assert any(item.get("sk") == "video" for item in table.scan()["Items"])
        return real_notify(*args, **kwargs)

    monkeypatch.setattr("agentlab.worker.notify", notify_after_ledger)

    result = runner.invoke(app, ["worker", "explain"])

    assert result.exit_code == 0, result.output
    assert "Preference evidence (topic-feedback-v1)." in captured["interests"]
    assert len(captured["calls"]) == 2
    assert len(captured["calls"][1]) <= 3
    _url, kwargs = next(call for call in telegram_calls if call[0].endswith("/sendVideo"))
    assert kwargs["data"]["caption"].startswith("[CORE] Core Candidate Paper\n")
    keyboard = json.loads(kwargs["data"]["reply_markup"])["inline_keyboard"]
    assert [button["text"] for button in keyboard[0]] == ["COOL", "MEH", "SKIP"]
    assert [button["text"] for button in keyboard[1]] == ["CLEAR", "UNCLEAR"]
    row = next(item for item in table.scan()["Items"] if item.get("sk") == "video")
    assert row["topics"]
    assert row["selection_policy"] == "topic-feedback-v1"
    assert row["candidate_set"] == [
        {
            "baseline_rank": 1,
            "pool": "exploit",
            "source": "arxiv",
            "source_rank": 1,
            "title": "Core Candidate Paper",
            "topics": ["other"],
            "url": CORE_CANDIDATE["url"],
        }
    ]
    assert row["baseline_rank"] == 1
    assert row["baseline_selection"] == row["candidate_set"][0]
    assert row["feedback_status"] == "bounded-tiebreak"
    assert row["exploration_status"] == "none"
    assert row["selection_probability"] is None
    assert row["clarity"] is None


def test_explain_core_deep_read_failure_pings_fallback_novel_still_sends(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="all")
    _set_daytime(monkeypatch)
    _patch_explain_pools(monkeypatch)
    _patch_explain_render_stages(monkeypatch, fail_urls={CORE_CANDIDATE["url"]})

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output

    text_calls = [c for c in telegram_calls if c[0].endswith("/sendMessage")]
    assert len(text_calls) == 1
    fallback_text = text_calls[0][1]["json"]["text"]
    assert "core" in fallback_text.lower()
    assert "error" in fallback_text.lower()

    sendvideo_calls = [c for c in telegram_calls if c[0].endswith("/sendVideo")]
    # classic and novel both still succeed; only core failed, before ever
    # producing a digest (the fake deep_read raises before any S3 upload).
    assert len(sendvideo_calls) == 2

    video_items = [i for i in table.scan()["Items"] if i.get("sk") == "video"]
    assert {i["track"] for i in video_items} == {"classic", "novel"}

    # Core is still marked seen: papers_db.mark_seen runs the moment a
    # candidate is picked, before deep_read, so a failed deep-read must not
    # leave the candidate eligible to resurface tomorrow.
    seen_items = [i for i in table.scan()["Items"] if i.get("sk") == "paper"]
    assert {i["track"] for i in seen_items} == {"core", "classic", "novel"}

    assert "explain: core=failed classic=sent novel=sent" in result.output

    # A failed track must also write a ledger event (spec: every failure
    # writes an event item to the ledger, not just a ping).
    ledger_id = f"explain-{datetime.now(UTC).strftime('%Y%m%d')}"
    track_failed = [
        i
        for i in table.scan()["Items"]
        if i.get("experiment_id") == ledger_id and i.get("event") == "TRACK_FAILED"
    ]
    assert len(track_failed) == 1
    assert track_failed[0]["arm"] == "core"


def test_explain_render_failure_includes_stderr_in_fallback_ping(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    # A render/ffmpeg/manim subprocess failure raises CalledProcessError
    # with the real reason on .stderr (video_render.run_subprocess always
    # passes text=True, so .stderr is str, not bytes); the fallback ping
    # must surface it, not just a bare exception repr.
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="core")
    _set_daytime(monkeypatch)
    monkeypatch.setattr("agentlab.worker.gather_exploit", lambda: [CORE_CANDIDATE])
    monkeypatch.setattr("agentlab.worker.gather_explore", lambda: [NOVEL_CANDIDATE])
    monkeypatch.setattr("agentlab.worker.pick_paper", _fake_pick_paper)
    monkeypatch.setattr("agentlab.worker.rank_papers", _fake_rank_papers)
    monkeypatch.setattr("agentlab.worker.deep_read", _make_fake_deep_read())
    monkeypatch.setattr("agentlab.worker.verify_voice", lambda polly_client: "Joanna")
    monkeypatch.setattr(
        "agentlab.worker.narrate", lambda polly_client, plan, voice_id, out_dir: ["clip"]
    )

    def fake_render_video_raises(plan, clips, out_path):
        raise subprocess.CalledProcessError(
            1, ["uvx", "manim"], stderr="ffmpeg: No such filter: 'subtitles'"
        )

    monkeypatch.setattr("agentlab.worker.render_video", fake_render_video_raises)
    monkeypatch.setattr("agentlab.worker.compose_story_video", _fake_story_failed)

    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code == 0, result.output

    text_calls = [c for c in telegram_calls if c[0].endswith("/sendMessage")]
    assert len(text_calls) == 1
    fallback_text = text_calls[0][1]["json"]["text"]
    assert "What broke:" in fallback_text
    assert "No such filter: 'subtitles'" in fallback_text

    ledger_id = f"explain-{datetime.now(UTC).strftime('%Y%m%d')}"
    track_failed = [
        i
        for i in table.scan()["Items"]
        if i.get("experiment_id") == ledger_id and i.get("event") == "TRACK_FAILED"
    ]
    assert len(track_failed) == 1
    assert track_failed[0]["arm"] == "core"


def test_explain_classic_track_advances_to_next_unseen_entry_across_two_runs(
    moto_fabric_with_ssm, telegram_calls, monkeypatch
):
    _s3, _dynamodb, table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="classic")
    _set_daytime(monkeypatch)
    _patch_explain_render_stages(monkeypatch)

    result1 = runner.invoke(app, ["worker", "explain"])
    assert result1.exit_code == 0, result1.output
    video_items = [i for i in table.scan()["Items"] if i.get("sk") == "video"]
    assert len(video_items) == 1
    assert video_items[0]["url"] == ATTENTION_URL

    result2 = runner.invoke(app, ["worker", "explain"])
    assert result2.exit_code == 0, result2.output
    video_items = sorted(
        (i for i in table.scan()["Items"] if i.get("sk") == "video"),
        key=lambda i: i["sent_ts"],
    )
    assert len(video_items) == 2
    assert video_items[0]["url"] == ATTENTION_URL
    assert video_items[1]["url"] == COT_URL
    assert video_items[1]["url"] != video_items[0]["url"]

    sendvideo_calls = [c for c in telegram_calls if c[0].endswith("/sendVideo")]
    assert len(sendvideo_calls) == 2


def test_explain_invalid_track_env_exits_nonzero(moto_fabric_with_ssm, monkeypatch):
    _s3, _dynamodb, _table, _ssm = moto_fabric_with_ssm
    _set_explain_env(monkeypatch, track="bogus")
    result = runner.invoke(app, ["worker", "explain"])
    assert result.exit_code != 0
