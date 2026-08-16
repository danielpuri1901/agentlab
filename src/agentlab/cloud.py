"""Cloud CLI commands: submit an experiment to the AWS fabric, check its
state, and fetch its finished report.

`agentlab cloud submit` writes a CREATED item to DynamoDB and sends the
experiment spec to SQS, where the EventBridge Pipe (`infra/pipe.tf`) reads
the message body's fields directly into a Step Functions execution via raw
string substitution into JSON. That input_template is not itself an escaping
boundary, so `submit` is the enforcement point: baseline_style/candidate_style
must be one of the known compaction styles and model must match a narrow
identifier pattern before anything reaches the queue - see `_validate_style`
and `_validate_model`.

`agentlab cloud status` and `agentlab cloud report` are read-only: they query
the same DynamoDB table the workers (`agentlab.worker`) write transitions to,
and fetch the same S3 report key `agentlab.worker.upload_report` writes.

experiment_id format: `exp-<utc YYYYMMDDTHHMMSSZ>-<4 hex chars>`. The clock
and hex source are module-level indirections (`now`, `random_hex`) rather
than inline `datetime.now`/`secrets` calls, so tests can monkeypatch them for
a deterministic id.
"""

import json
import re
import secrets
from datetime import UTC, datetime

import boto3
import typer
from boto3.dynamodb.conditions import Key

from agentlab.compaction_task import _SUMMARY_INSTRUCTIONS_BY_STYLE
from agentlab.worker import transition

cloud_app = typer.Typer()

BASELINE_STYLE = "truncate"
CANDIDATE_STYLE = "structured"

DEFAULT_STATE_TABLE = "agentlab-state"
DEFAULT_RESULTS_BUCKET = "agentlab-results-891377302765"

_MODEL_PATTERN = re.compile(r"^[A-Za-z0-9./:_-]+$")


def _valid_styles() -> set[str]:
    return set(_SUMMARY_INSTRUCTIONS_BY_STYLE) | {"truncate"}


def _validate_style(style: str, flag_name: str) -> None:
    valid = _valid_styles()
    if style not in valid:
        typer.echo(
            f"error: invalid {flag_name} '{style}' - must be one of {sorted(valid)}",
            err=True,
        )
        raise typer.Exit(1)


def _validate_model(model: str) -> None:
    if not _MODEL_PATTERN.fullmatch(model):
        typer.echo(
            f"error: invalid --model '{model}' - must match {_MODEL_PATTERN.pattern}",
            err=True,
        )
        raise typer.Exit(1)


def now() -> datetime:
    """Module-level clock indirection; tests monkeypatch this for a fixed time."""
    return datetime.now(UTC)


def random_hex() -> str:
    """Module-level randomness indirection; tests monkeypatch this for a fixed value."""
    return secrets.token_hex(2)


def generate_experiment_id() -> str:
    ts = now().strftime("%Y%m%dT%H%M%SZ")
    return f"exp-{ts}-{random_hex()}"


def build_message_body(
    experiment_id: str,
    model: str,
    tasks: int,
    repeats: int,
    n_facts: int,
    filler_turns: int,
    summary_budget: int,
    max_connections: int,
    baseline_style: str,
    candidate_style: str,
) -> dict:
    """The SQS message body: a flat JSON object with exactly the fields
    `infra/pipe.tf`'s input_template extracts, in the same order.

    `max_connections` must always be a concrete int, never `None`/null:
    the pipe substitutes it unquoted into JSON, JSONata's `$string(null)`
    (see `infra/stepfunctions.tf`) turns a null value into the ECS
    container-override string "null", and that reached `int()` in
    `agentlab.worker._optional_int_env` before this default existed -
    an unrecoverable ValueError with no DynamoDB transition recorded.
    `submit_command`'s Typer option defaults this to 30 for exactly that
    reason; do not reintroduce a `None` default here.
    """
    return {
        "experiment_id": experiment_id,
        "model": model,
        "tasks": tasks,
        "repeats": repeats,
        "n_facts": n_facts,
        "filler_turns": filler_turns,
        "summary_budget": summary_budget,
        "max_connections": max_connections,
        "baseline_style": baseline_style,
        "candidate_style": candidate_style,
    }


def submit_experiment(
    sqs_client,
    table,
    queue_url: str,
    experiment_id: str,
    model: str,
    tasks: int,
    repeats: int,
    n_facts: int,
    filler_turns: int,
    summary_budget: int,
    max_connections: int,
    baseline_style: str,
    candidate_style: str,
) -> None:
    """Write the CREATED transition, then send the experiment spec to SQS."""
    transition(
        table,
        experiment_id,
        "CREATED",
        None,
        f"model={model} tasks={tasks} repeats={repeats}",
    )
    body = build_message_body(
        experiment_id,
        model,
        tasks,
        repeats,
        n_facts,
        filler_turns,
        summary_budget,
        max_connections,
        baseline_style,
        candidate_style,
    )
    sqs_client.send_message(QueueUrl=queue_url, MessageBody=json.dumps(body))


@cloud_app.command("submit")
def submit_command(
    model: str = typer.Option(..., help="Inspect model id used for both arms."),
    tasks: int = typer.Option(20, help="Distinct seeded compaction sessions to run."),
    repeats: int = typer.Option(5, help="Epochs per session."),
    n_facts: int = typer.Option(12, help="Planted facts per session."),
    filler_turns: int = typer.Option(40, help="Filler turns per session."),
    summary_budget: int = typer.Option(
        150, help="max_tokens for the summarizer's GenerateConfig (naive/structured only)."
    ),
    max_connections: int = typer.Option(
        30,
        help=(
            "Inspect max concurrent model connections "
            "(cloud default 30, the value proven in v0.1 live runs)."
        ),
    ),
    baseline_style: str = typer.Option(
        BASELINE_STYLE, help="Compaction style for the baseline arm."
    ),
    candidate_style: str = typer.Option(
        CANDIDATE_STYLE, help="Compaction style for the candidate arm."
    ),
    queue_url: str = typer.Option(
        ...,
        envvar="AGENTLAB_QUEUE_URL",
        help="SQS queue URL that receives the experiment spec.",
    ),
    state_table: str = typer.Option(
        DEFAULT_STATE_TABLE,
        envvar="AGENTLAB_STATE_TABLE",
        help="DynamoDB table name for experiment state transitions.",
    ),
) -> None:
    """Submit a paired experiment to the cloud fabric: writes CREATED to
    DynamoDB, sends the spec to SQS, and prints the experiment id."""
    _validate_model(model)
    _validate_style(baseline_style, "--baseline-style")
    _validate_style(candidate_style, "--candidate-style")

    experiment_id = generate_experiment_id()
    table = boto3.resource("dynamodb").Table(state_table)
    sqs_client = boto3.client("sqs")

    submit_experiment(
        sqs_client,
        table,
        queue_url,
        experiment_id,
        model,
        tasks,
        repeats,
        n_facts,
        filler_turns,
        summary_budget,
        max_connections,
        baseline_style,
        candidate_style,
    )
    typer.echo(experiment_id)


def fetch_transitions(table, experiment_id: str) -> list[dict]:
    """Query every state-transition item for one experiment, in `sk` order
    (which embeds the event timestamp, so this is chronological)."""
    # TODO: single Query call, no LastEvaluatedKey pagination loop - fine at
    # today's transition volume per experiment (a handful of events), but
    # revisit if an experiment's item count ever approaches DynamoDB's 1MB
    # per-Query page limit.
    response = table.query(KeyConditionExpression=Key("experiment_id").eq(experiment_id))
    return response["Items"]


def _trim(detail: str | None, width: int = 80) -> str:
    detail = detail or ""
    return detail if len(detail) <= width else detail[: width - 3] + "..."


@cloud_app.command("status")
def status_command(
    experiment_id: str = typer.Argument(..., help="Experiment id from `cloud submit`."),
    state_table: str = typer.Option(
        DEFAULT_STATE_TABLE,
        envvar="AGENTLAB_STATE_TABLE",
        help="DynamoDB table name for experiment state transitions.",
    ),
    results_bucket: str = typer.Option(
        DEFAULT_RESULTS_BUCKET,
        envvar="AGENTLAB_RESULTS_BUCKET",
        help="S3 bucket for EvalLogs and reports.",
    ),
) -> None:
    """Print every recorded state transition for an experiment in order,
    and - once FINALIZED - the verdict and the report's S3 path."""
    table = boto3.resource("dynamodb").Table(state_table)
    items = fetch_transitions(table, experiment_id)

    if not items:
        typer.echo(f"no transitions recorded for {experiment_id}")
        raise typer.Exit(1)

    for item in items:
        arm = item.get("arm") or "-"
        typer.echo(f"{item['ts']}  {item['event']:<14} arm={arm:<10} {_trim(item.get('detail'))}")

    finalized = [item for item in items if item["event"] == "FINALIZED"]
    if finalized:
        verdict_str = finalized[-1].get("detail") or ""
        typer.echo(f"verdict: {verdict_str}")
        typer.echo(f"report: s3://{results_bucket}/experiments/{experiment_id}/report.md")


@cloud_app.command("report")
def report_command(
    experiment_id: str = typer.Argument(..., help="Experiment id from `cloud submit`."),
    results_bucket: str = typer.Option(
        DEFAULT_RESULTS_BUCKET,
        envvar="AGENTLAB_RESULTS_BUCKET",
        help="S3 bucket for EvalLogs and reports.",
    ),
) -> None:
    """Download and print the finalized report.md for an experiment."""
    s3_client = boto3.client("s3")
    obj = s3_client.get_object(
        Bucket=results_bucket, Key=f"experiments/{experiment_id}/report.md"
    )
    typer.echo(obj["Body"].read().decode("utf-8"))
