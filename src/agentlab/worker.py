"""Cloud worker commands: cloud execution entrypoints for one experiment arm.

`agentlab worker run-arm` runs exactly one arm of a paired experiment (a
worker owns one arm; pairing happens because Step Functions runs two workers
then a finalizer) and `agentlab worker finalize` combines both arms' logs
into the same verdict report the local `agentlab run` command produces.

Both commands are entirely env-var driven (the container-overrides contract
from the Step Functions state machine) and read no CLI flags. The AWS-facing
helpers (`transition`, `upload_log`, `download_log`, `upload_report`) take
their boto3 client/table as a parameter rather than constructing one
internally, so tests can hit them directly against a moto-mocked client.
"""

import asyncio
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import boto3
import typer
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from agentlab.charts import verdict_chart_png
from agentlab.eval_runner import _run_arm, hypothesis_for
from agentlab.notify import flush_pending, notify
from agentlab.report import render_report
from agentlab.results import extract_results, mean_score, total_cost, total_tokens
from agentlab.stats import paired_analysis, verdict

worker_app = typer.Typer()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        typer.echo(f"error: missing required env var {name}", err=True)
        raise typer.Exit(1)
    return value


def _optional_int_env(name: str) -> int | None:
    # infra/stepfunctions.tf renders a null max_connections via JSONata's
    # $string(null), which produces the literal ECS container-override env
    # value "null" (not an empty/missing var) - int() must not be attempted
    # on that string, or "None" (defensive - not produced by that template,
    # but the same failure mode), so both are treated as absent alongside
    # empty/missing.
    value = os.environ.get(name)
    if not value or value.strip().lower() in ("null", "none"):
        return None
    return int(value)


def _log_key(experiment_id: str, arm_style: str) -> str:
    return f"experiments/{experiment_id}/logs/{arm_style}.eval"


def _report_key(experiment_id: str) -> str:
    return f"experiments/{experiment_id}/report.md"


def transition(
    table,
    experiment_id: str,
    event: str,
    arm: str | None,
    detail: str | None,
    ts: str | None = None,
) -> str:
    """Append one state-transition item, swallowing a duplicate write.

    SK is `event#<ts>#<event>`; the conditional put on
    `attribute_not_exists(sk)` makes a retried write of the exact same
    (experiment_id, ts, event) idempotent: it either creates the item once,
    or hits `ConditionalCheckFailedException` on retry - swallowed here
    rather than raised, since "already recorded" is success, not an error.
    """
    ts = ts or datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    # `sk` deliberately does NOT include `arm` - only (experiment_id, ts, event) make it
    # unique. That's safe today because stepfunctions.tf runs ArmA and ArmB sequentially
    # (ArmA -> ArmB -> Finalize), never concurrently, so no two arms can produce the same
    # ts for the same event name. If the arms ever ran in parallel, ArmA and ArmB writing
    # the same event (e.g. both ARM_STARTED) within the same microsecond would collide on
    # this sk: the second write hits ConditionalCheckFailedException and is swallowed
    # below as "already recorded", silently dropping that arm's transition item instead
    # of writing it under a distinct key.
    sk = f"event#{ts}#{event}"
    item = {
        "experiment_id": experiment_id,
        "sk": sk,
        "event": event,
        "arm": arm,
        "detail": detail,
        "ts": ts,
    }
    try:
        table.put_item(Item=item, ConditionExpression=Attr("sk").not_exists())
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
    return sk


def upload_log(s3_client, bucket: str, experiment_id: str, arm_style: str, local_path: str) -> str:
    """Upload one arm's local EvalLog file to its deterministic S3 key."""
    key = _log_key(experiment_id, arm_style)
    s3_client.upload_file(str(local_path), bucket, key)
    return key


def download_log(s3_client, bucket: str, experiment_id: str, arm_style: str, dest_dir: Path) -> Path:
    """Download one arm's EvalLog from S3 to `dest_dir` for extraction."""
    key = _log_key(experiment_id, arm_style)
    dest_path = Path(dest_dir) / f"{arm_style}.eval"
    s3_client.download_file(bucket, key, str(dest_path))
    return dest_path


def upload_report(s3_client, bucket: str, experiment_id: str, report_md: str) -> str:
    """Upload the finalized Markdown report to its deterministic S3 key."""
    key = _report_key(experiment_id)
    s3_client.put_object(Bucket=bucket, Key=key, Body=report_md.encode("utf-8"))
    return key


@worker_app.command("flush-pings")
def flush_pings_command() -> None:
    """Deliver pings queued during quiet hours (the 08:00 schedule's job)."""
    state_table = _require_env("STATE_TABLE")
    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    count = flush_pending(table, ssm_client)
    typer.echo(f"flushed {count} pending pings")


@worker_app.command("propose")
def propose_command() -> None:
    """Scheduled proposer run: flush queued pings, then propose new work."""
    from agentlab.proposer import DEFAULT_PROPOSER_MODEL, run_propose

    state_table = _require_env("STATE_TABLE")
    results_bucket = _require_env("RESULTS_BUCKET")
    model = os.environ.get("PROPOSER_MODEL", DEFAULT_PROPOSER_MODEL)
    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    s3_client = boto3.client("s3")
    try:
        flushed = flush_pending(table, ssm_client)
    except Exception as exc:  # noqa: BLE001 - a stuck queued ping must not block proposing
        typer.echo(f"flush failed, continuing: {exc}", err=True)
        flushed = 0
    count = run_propose(table, ssm_client, s3_client, results_bucket, model)
    typer.echo(f"flushed {flushed} pending pings, filed {count} proposals")


@worker_app.command("run-arm")
def run_arm_command() -> None:
    """Run one arm of a paired experiment: env-var driven, reuses the local
    eval machinery for exactly one style, uploads the resulting EvalLog to
    S3, and records ARM_STARTED/ARM_COMPLETED (or ARM_FAILED) in DynamoDB."""
    experiment_id = _require_env("EXPERIMENT_ID")
    arm_style = _require_env("ARM_STYLE")
    model = _require_env("MODEL")
    tasks = int(_require_env("TASKS"))
    repeats = int(_require_env("REPEATS"))
    n_facts = int(_require_env("N_FACTS"))
    filler_turns = int(_require_env("FILLER_TURNS"))
    summary_budget = int(_require_env("SUMMARY_BUDGET"))
    max_connections = _optional_int_env("MAX_CONNECTIONS")
    results_bucket = _require_env("RESULTS_BUCKET")
    state_table = _require_env("STATE_TABLE")

    table = boto3.resource("dynamodb").Table(state_table)
    s3_client = boto3.client("s3")
    seeds = list(range(tasks))

    transition(table, experiment_id, "ARM_STARTED", arm_style, f"model={model}")
    try:
        with tempfile.TemporaryDirectory() as tmp_dir:
            log = asyncio.run(
                _run_arm(
                    arm_style,
                    model,
                    seeds,
                    repeats,
                    Path(tmp_dir),
                    summary_budget,
                    n_facts,
                    filler_turns,
                    max_connections,
                )
            )
            key = upload_log(s3_client, results_bucket, experiment_id, arm_style, log.location)
    except Exception as exc:
        transition(table, experiment_id, "ARM_FAILED", arm_style, str(exc))
        # A failed experiment must never be silent (lesson: the Grok intake
        # run died 3x with AccessDenied and Daniel only noticed a day later).
        # Ping failure must not mask the original error, hence the guard.
        try:
            ssm_client = boto3.client("ssm")
            notify(
                table,
                ssm_client,
                f"Experiment {experiment_id} FAILED.\n"
                f"Arm '{arm_style}' error: {str(exc)[:400]}\n"
                "The fabric will retry up to 3 times; repeated pings mean it is dead.",
            )
        except Exception as ping_exc:  # noqa: BLE001 - never mask the arm error
            typer.echo(f"failure ping could not be sent: {ping_exc}", err=True)
        raise

    transition(table, experiment_id, "ARM_COMPLETED", arm_style, key)
    typer.echo(f"arm '{arm_style}' completed: s3 key {key}")


@worker_app.command("finalize")
def finalize_command() -> None:
    """Combine both arms' EvalLogs into the paired verdict: downloads both
    logs from S3, reuses the local extraction/stats/report pipeline, writes
    report.md to S3, and records the FINALIZED verdict in DynamoDB."""
    experiment_id = _require_env("EXPERIMENT_ID")
    model = _require_env("MODEL")
    tasks = int(_require_env("TASKS"))
    repeats = int(_require_env("REPEATS"))
    baseline_style = _require_env("BASELINE_STYLE")
    candidate_style = _require_env("CANDIDATE_STYLE")
    results_bucket = _require_env("RESULTS_BUCKET")
    state_table = _require_env("STATE_TABLE")

    table = boto3.resource("dynamodb").Table(state_table)
    s3_client = boto3.client("s3")
    seeds = list(range(tasks))

    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        baseline_path = download_log(s3_client, results_bucket, experiment_id, baseline_style, tmp_path)
        candidate_path = download_log(s3_client, results_bucket, experiment_id, candidate_style, tmp_path)

        baseline = extract_results(str(baseline_path))
        candidate = extract_results(str(candidate_path))
        result = paired_analysis(baseline.scores, candidate.scores)
        cost = total_cost(baseline.usages + candidate.usages)
        verdict_str = verdict(result, protected=[])
        baseline_mean = mean_score(baseline.scores)
        candidate_mean = mean_score(candidate.scores)

        report_md = render_report(
            result=result,
            verdict_str=verdict_str,
            total_cost=cost.total,
            log_paths=[str(baseline_path), str(candidate_path)],
            hypothesis=hypothesis_for(baseline_style, candidate_style),
            model=model,
            baseline_style=baseline_style,
            candidate_style=candidate_style,
            seeds=seeds,
            repeats=repeats,
            baseline_recall=baseline_mean,
            candidate_recall=candidate_mean,
            total_tokens=total_tokens(baseline.usages + candidate.usages),
            unpriced_models=sorted(cost.unpriced_models),
        )
        upload_report(s3_client, results_bucket, experiment_id, report_md)

        transition(table, experiment_id, "FINALIZED", None, verdict_str)
        try:
            ssm_client = boto3.client("ssm")
            chart = verdict_chart_png(
                baseline_style,
                candidate_style,
                baseline_mean,
                candidate_mean,
                result.mean_delta,
                result.ci_low,
                result.ci_high,
            )
            text = (
                f"Experiment {experiment_id} is done.\n"
                f"Verdict: {verdict_str}.\n"
                f"{candidate_style} scores {candidate_mean:.3f}. "
                f"{baseline_style} scores {baseline_mean:.3f}.\n"
                f"The 95% CI of the difference is {result.ci_low:+.3f} to {result.ci_high:+.3f}.\n"
                f"Report: s3://{results_bucket}/experiments/{experiment_id}/report.md"
            )
            ping_status = notify(table, ssm_client, text, photo_png=chart)
            typer.echo(f"finalize ping: {ping_status}")
        except Exception as exc:  # noqa: BLE001 - a ping failure must never fail a finished experiment
            # The invariant is that nothing after FINALIZED may fail the
            # command: if the PING_FAILED write itself fails (e.g. the same
            # DynamoDB outage that broke the ping), swallow that too instead
            # of letting it escape and turn a finished experiment into a
            # nonzero exit.
            try:
                transition(table, experiment_id, "PING_FAILED", None, str(exc))
            except Exception as t_exc:  # noqa: BLE001
                typer.echo(f"ping failed and PING_FAILED write failed: {t_exc}", err=True)

    typer.echo(f"verdict: {verdict_str}")
