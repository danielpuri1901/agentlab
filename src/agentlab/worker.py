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
import json
import os
import re
import secrets
import subprocess
import tempfile
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial as bind
from pathlib import Path

import boto3
import httpx
import typer
from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

from agentlab.aws_costs import month_to_date_tagged_cost
from agentlab.charts import verdict_chart_png
from agentlab.costs import (
    ModelCallUsage,
    cache_static_system_prompt,
    completion_usage,
)
from agentlab.eval_runner import _run_arm, hypothesis_for
from agentlab.notify import flush_pending, notify
from agentlab.papers_db import is_seen, mark_seen, paper_identity, recent_seen_titles
from agentlab.preferences import (
    POLICY_VERSION,
    candidate_topics,
    load_preference_context,
)
from agentlab.report import render_report
from agentlab.results import extract_results, mean_score, total_cost, total_tokens
from agentlab.scene_plan import (
    DEFAULT_DEEP_READ_MODEL,
    DEFAULT_PICK_MODEL,
    deep_read,
    pick_paper,
    rank_papers,
)
from agentlab.sources import gather_exploit, gather_explore
from agentlab.stats import paired_analysis, verdict
from agentlab.story_video import StoryFailed, compose_story_video
from agentlab.video_render import narrate, render_video, scene_texts, verify_voice

worker_app = typer.Typer()

# `docs/` lives at the repo root, not under src/agentlab/, so this walks up
# from this module's location (src/agentlab/worker.py -> src/agentlab ->
# src -> repo root) rather than relying on the process cwd; the Dockerfile's
# `COPY . .` puts the whole repo under /app, so this resolves the same way
# in the worker container as it does locally.
_REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSICS_PATH = _REPO_ROOT / "docs" / "classics.json"
INTERESTS_PATH = _REPO_ROOT / "docs" / "interests.md"
GOLDEN_PAPERS_PATH = _REPO_ROOT / "docs" / "golden-papers.jsonl"

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")
EXPLAIN_TRACKS = ("core", "classic", "novel")
DIGEST_URL_EXPIRY_SECONDS = 7 * 24 * 3600
MODEL_CALL_TIMEOUT_SECONDS = 180
DEFAULT_DEEP_READ_PRICE_MODEL = "bedrock/global.anthropic.claude-sonnet-4-6"
DEFAULT_PICK_PRICE_MODEL = "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0"


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


def upload_log(
    s3_client, bucket: str, experiment_id: str, arm_style: str, local_path: str
) -> str:
    """Upload one arm's local EvalLog file to its deterministic S3 key."""
    key = _log_key(experiment_id, arm_style)
    s3_client.upload_file(str(local_path), bucket, key)
    return key


def download_log(
    s3_client, bucket: str, experiment_id: str, arm_style: str, dest_dir: Path
) -> Path:
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
    """Deliver pings queued during quiet hours (the 08:00 schedule's job).

    RESULTS_BUCKET is optional here: it is only needed to flush a queued
    video ping (worker explain's quiet-hours path), so omitting it keeps
    working exactly as before for text/photo-only queues.
    """
    state_table = _require_env("STATE_TABLE")
    results_bucket = os.environ.get("RESULTS_BUCKET")
    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    s3_client = boto3.client("s3")
    try:
        count = flush_pending(table, ssm_client, s3_client, results_bucket)
    except Exception as exc:  # noqa: BLE001 - a stuck queued ping must not fail the schedule
        typer.echo(f"flush failed, continuing: {exc}", err=True)
        count = 0
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
        flushed = flush_pending(table, ssm_client, s3_client, results_bucket)
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
            key = upload_log(
                s3_client, results_bucket, experiment_id, arm_style, log.location
            )
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
        baseline_path = download_log(
            s3_client, results_bucket, experiment_id, baseline_style, tmp_path
        )
        candidate_path = download_log(
            s3_client, results_bucket, experiment_id, candidate_style, tmp_path
        )

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
                typer.echo(
                    f"ping failed and PING_FAILED write failed: {t_exc}", err=True
                )

    typer.echo(f"verdict: {verdict_str}")


# ---------------------------------------------------------------------------
# `worker explain`: the daily-paper-videos orchestrator
# (docs/specs/2026-08-23-daily-paper-videos.md). Three tracks - core
# (exploit pool, ranked against docs/interests.md), classic (the next
# unwatched entry in docs/classics.json), novel (explore pool, picked for
# surprise) - each run fetch -> dedup -> pick -> deep-read -> story (or
# template) -> deliver -> ledger, fully isolated from one another: a failure in one
# track pings a text fallback and the next track still runs.
# ---------------------------------------------------------------------------


def _pricing_model_for(model: str) -> str | None:
    pairs = (
        ("DEEP_READ_MODEL", "DEEP_READ_PRICE_MODEL", DEFAULT_DEEP_READ_PRICE_MODEL),
        ("PICK_MODEL", "PICK_PRICE_MODEL", DEFAULT_PICK_PRICE_MODEL),
        ("STORY_MODEL", "STORY_PRICE_MODEL", DEFAULT_DEEP_READ_PRICE_MODEL),
        ("SCENE_MODEL", "SCENE_PRICE_MODEL", DEFAULT_DEEP_READ_PRICE_MODEL),
        ("JUDGE_MODEL", "JUDGE_PRICE_MODEL", DEFAULT_DEEP_READ_PRICE_MODEL),
    )
    for model_env, price_env, default_price in pairs:
        if os.environ.get(model_env) == model:
            return os.environ.get(price_env, default_price)
    if "application-inference-profile" in model:
        return None
    return model


def _run_completion(
    model: str,
    messages: list[dict],
    *,
    max_tokens: int,
    timeout: int,
    stage: str,
    usage_sink: list[ModelCallUsage] | None = None,
    pricing_model: str | None = None,
):
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    import litellm

    price_id = pricing_model or _pricing_model_for(model)
    request_messages = messages
    if price_id and "anthropic.claude" in price_id:
        request_messages = cache_static_system_prompt(messages)
    started = time.perf_counter()
    response = litellm.completion(
        model=model,
        messages=request_messages,
        max_tokens=max_tokens,
        timeout=timeout,
    )
    if usage_sink is not None and price_id is not None:
        try:
            usage_sink.append(
                completion_usage(
                    response,
                    requested_model=model,
                    pricing_model=price_id,
                    stage=stage,
                    latency_ms=round((time.perf_counter() - started) * 1000),
                )
            )
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    return response


def _complete(
    model: str,
    messages: list[dict],
    *,
    usage_sink: list[ModelCallUsage] | None = None,
    pricing_model: str | None = None,
) -> str:
    response = _run_completion(
        model,
        messages,
        max_tokens=3000,
        timeout=MODEL_CALL_TIMEOUT_SECONDS,
        stage="paper",
        usage_sink=usage_sink,
        pricing_model=pricing_model,
    )
    return response.choices[0].message.content


def _complete_long(
    model: str,
    messages: list[dict],
    *,
    usage_sink: list[ModelCallUsage] | None = None,
    pricing_model: str | None = None,
) -> str:
    """The story path's model calls allow complete generated scene files.

    A scene file does not fit the deep read's 3000-token budget, so this
    completion allows 12000 output tokens and a 180 second timeout. The lazy
    litellm import avoids the same circular import as `_complete`.
    """
    response = _run_completion(
        model,
        messages,
        max_tokens=12000,
        timeout=MODEL_CALL_TIMEOUT_SECONDS,
        stage="video",
        usage_sink=usage_sink,
        pricing_model=pricing_model,
    )
    choice = response.choices[0]
    if getattr(choice, "finish_reason", None) in {"length", "max_tokens"}:
        raise ValueError(
            "model output hit the token limit; return a shorter complete response"
        )
    return choice.message.content


def _load_classics() -> list[dict]:
    return json.loads(CLASSICS_PATH.read_text(encoding="utf-8"))


def _load_interests() -> str:
    return INTERESTS_PATH.read_text(encoding="utf-8")


def _next_unseen_classic(table) -> dict | None:
    """First docs/classics.json entry not already in the seen-papers store,
    in file order; None once every classic has been sent."""
    fuzzy_titles = recent_seen_titles(table)
    for entry in _load_classics():
        identity = paper_identity(entry["url"], entry["title"])
        if not is_seen(table, identity, fuzzy_titles):
            return entry
    return None


def _fresh_candidates(pool: list[dict], table) -> list[dict]:
    """Fetch-pool candidates not already in the seen-papers store."""
    fuzzy_titles = recent_seen_titles(table)
    return [
        c
        for c in pool
        if not is_seen(
            table, paper_identity(c.get("url", ""), c.get("title", "")), fuzzy_titles
        )
    ]


def _fetch_text(url: str) -> str:
    """Deep-read source fetch: the arXiv HTML rendering for an arxiv.org
    URL (60s timeout), falling back to the abs page if the html render
    isn't available; any other URL is fetched directly."""
    match = _ARXIV_ID_RE.search(url or "") if "arxiv.org" in (url or "") else None
    if match:
        arxiv_id = match.group(1)
        try:
            response = httpx.get(f"https://arxiv.org/html/{arxiv_id}", timeout=60)
            if response.status_code == 200:
                return response.text
        except Exception:  # noqa: BLE001, S110 - fall through to the abs page
            pass
        response = httpx.get(f"https://arxiv.org/abs/{arxiv_id}", timeout=60)
        response.raise_for_status()
        return response.text
    response = httpx.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def _generate_video_key(track: str) -> str:
    ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{track}-{ts}-{secrets.token_hex(2)}"


def _candidate_record(
    candidate: dict, source_rank: int, baseline_rank: int | None = None
) -> dict:
    """Keep the choice set small enough for one DynamoDB ledger item."""
    return {
        "pool": str(candidate.get("pool", "")),
        "baseline_rank": baseline_rank,
        "source": str(candidate.get("source", "")),
        "source_rank": source_rank,
        "title": str(candidate.get("title", "")),
        "topics": candidate_topics(candidate),
        "url": str(candidate.get("url", "")),
    }


def _model_usage_record(
    model_usage: list[ModelCallUsage],
) -> tuple[Decimal, list[dict]]:
    total = sum(
        (Decimal(str(call.estimated_cost_usd)) for call in model_usage),
        start=Decimal(0),
    )
    calls = [
        {
            **asdict(call),
            "estimated_cost_usd": Decimal(str(call.estimated_cost_usd)),
        }
        for call in model_usage
    ]
    return total, calls


def _upload_attempt_artifacts(
    s3_client,
    bucket: str,
    video_key: str,
    attempt_records: list[dict],
) -> list[dict]:
    records = deepcopy(attempt_records)
    for record in records:
        attempt = record.get("attempt")
        prefix = f"stories/{video_key}/attempts/{attempt}"
        source_path = record.pop("source_path", None)
        if source_path and Path(source_path).is_file():
            source_key = f"{prefix}/scene.py"
            s3_client.upload_file(source_path, bucket, source_key)
            record["source_key"] = source_key
        frame_keys = []
        for index, frame_path in enumerate(record.pop("frame_paths", []), start=1):
            if not Path(frame_path).is_file():
                continue
            frame_key = f"{prefix}/frame-{index:02d}.png"
            s3_client.upload_file(frame_path, bucket, frame_key)
            frame_keys.append(frame_key)
        if frame_keys:
            record["frame_keys"] = frame_keys
        sheet_keys = []
        for index, sheet_path in enumerate(
            record.pop("contact_sheet_paths", []), start=1
        ):
            if not Path(sheet_path).is_file():
                continue
            sheet_key = f"{prefix}/contact-sheet-{index:02d}.png"
            s3_client.upload_file(sheet_path, bucket, sheet_key)
            sheet_keys.append(sheet_key)
        if sheet_keys:
            record["contact_sheet_keys"] = sheet_keys
    return records


def _recent_visual_directions(table, limit: int = 6) -> list[str]:
    items = []
    request = {}
    while True:
        response = table.scan(**request)
        items.extend(
            item
            for item in response.get("Items", [])
            if item.get("sk") == "video" and item.get("visual_focus")
        )
        key = response.get("LastEvaluatedKey")
        if not key:
            break
        request["ExclusiveStartKey"] = key
    items.sort(key=lambda item: item.get("sent_ts", ""), reverse=True)
    directions = []
    seen = set()
    for item in items:
        focus = item["visual_focus"]
        if focus in seen:
            continue
        seen.add(focus)
        directions.append(f"{item.get('title', 'Previous paper')}: {focus}")
        if len(directions) == limit:
            break
    return directions


def _run_explain_track(
    track: str,
    table,
    ssm_client,
    s3_client,
    polly_client,
    bucket: str,
    deep_read_model: str,
    pick_model: str,
    story_model: str,
    scene_model: str,
    judge_model: str,
    partial: dict,
    aws_mtd_cost: Decimal | None = None,
) -> str:
    """Run one explain track end to end. Returns "sent" or "empty" (no
    candidate left to pick). Raises on any other failure; `partial`
    accumulates title/url/claim/digest_url as they become available so the
    caller can send a useful fallback ping without redoing the work."""
    model_usage: list[ModelCallUsage] = []
    complete = bind(_complete, usage_sink=model_usage)
    complete_long = bind(_complete_long, usage_sink=model_usage)

    if track == "classic":
        candidate = _next_unseen_classic(table)
        if candidate is None:
            return "empty"
        candidate_set = [_candidate_record(candidate, 1, 1)]
        baseline_rank = 1
        baseline_selection = candidate_set[0]
        exploration_status = "none"
        feedback_status = "fixed-classic"
        selection_probability = 1
    else:
        pool = gather_exploit() if track == "core" else gather_explore()
        fresh = _fresh_candidates(pool, table)
        if not fresh:
            return "empty"
        mode = "novel" if track == "novel" else "core"
        interests = _load_interests()
        preference_context = load_preference_context(table, GOLDEN_PAPERS_PATH)
        baseline_ranking = rank_papers(
            fresh, interests, complete, mode=mode, model=pick_model, limit=len(fresh)
        )
        if not baseline_ranking:
            return "empty"
        baseline_candidate = baseline_ranking[0]
        candidate = baseline_candidate
        feedback_status = "baseline"
        if preference_context:
            guided = pick_paper(
                baseline_ranking[:3],
                f"{interests}\n\n{preference_context}",
                complete,
                mode=mode,
                model=pick_model,
            )
            if guided is not None:
                candidate = guided
            feedback_status = "bounded-tiebreak"
        candidate_set = [
            _candidate_record(
                item,
                source_rank,
                baseline_ranking.index(item) + 1 if item in baseline_ranking else None,
            )
            for source_rank, item in enumerate(fresh, start=1)
        ]
        baseline_selection = next(
            record for record in candidate_set if record["baseline_rank"] == 1
        )
        baseline_rank = baseline_ranking.index(candidate) + 1
        exploration_status = "none"
        # The model ranker does not expose a calibrated choice probability.
        # Store that fact explicitly instead of inventing a propensity.
        selection_probability = None

    url = candidate["url"]
    title = candidate.get("title", "")
    partial["title"] = title
    partial["url"] = url

    # New candidates are recorded as seen the moment they are picked, not
    # when fetched, so an unpicked candidate can resurface later.
    identity = paper_identity(url, title)
    mark_seen(table, identity, url, title, candidate.get("source", track), track)

    digest, plan = deep_read(url, _fetch_text, complete, model=deep_read_model)
    partial["claim"] = plan.one_line_claim

    key = _generate_video_key(track)
    digest_key = f"digests/{key}.md"
    s3_client.put_object(Bucket=bucket, Key=digest_key, Body=digest.encode("utf-8"))
    digest_url = s3_client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": digest_key},
        ExpiresIn=DIGEST_URL_EXPIRY_SECONDS,
    )
    partial["digest_url"] = digest_url

    voice_id = verify_voice(polly_client)
    today = datetime.now(UTC).strftime("%Y%m%d")
    with tempfile.TemporaryDirectory(prefix=f"agentlab-explain-{track}-") as tmp_dir:
        tmp_path = Path(tmp_dir)
        video_path = tmp_path / "video.mp4"
        story = None
        try:
            story = compose_story_video(
                digest,
                plan,
                polly_client,
                voice_id,
                complete_long,
                tmp_path / "story",
                video_path,
                story_model=story_model,
                scene_model=scene_model,
                judge_model=judge_model,
                recent_visual_directions=_recent_visual_directions(table),
            )
        except StoryFailed as exc:
            # The worst day equals the old video: log why, render the template.
            transition(
                table,
                f"explain-{today}",
                "STORY_FALLBACK",
                track,
                str(exc)[:200],
            )
            clips = narrate(
                polly_client, scene_texts(plan), voice_id, tmp_path / "narration"
            )
            render_video(plan, clips, video_path)

        # Uploaded to S3 before notify so a quiet-hours queue can flush the
        # video later even though this tmp directory will be gone by then.
        video_key = f"videos/{key}.mp4"
        s3_client.upload_file(str(video_path), bucket, video_key)

        story_key = None
        if story is not None:
            story_key = f"stories/{key}.json"
            attempt_records = _upload_attempt_artifacts(
                s3_client, bucket, key, story.attempt_records
            )
            story_record = {
                "storyboard": story.storyboard.model_dump(),
                "judgement": story.judgement,
                "attempts": story.attempts,
                "timing": story.timing,
                "selected_attempt": story.selected_attempt,
                "passed": story.passed,
                "attempt_records": attempt_records,
            }
            s3_client.put_object(
                Bucket=bucket,
                Key=story_key,
                Body=json.dumps(story_record, indent=1).encode("utf-8"),
            )
            s3_client.put_object(
                Bucket=bucket,
                Key=f"stories/{key}.py",
                Body=story.scene_source.encode("utf-8"),
            )

        model_cost, model_calls = _model_usage_record(model_usage)
        cost_lines = [f"Estimated model cost: ${model_cost:.4f}"]
        if aws_mtd_cost is not None:
            cost_lines.append(f"Tagged AgentLab AWS MTD: ${aws_mtd_cost:.2f}")
        caption = (
            f"[{track.upper()}] {title}\n\n"
            f"{plan.one_line_claim}\n\n"
            f"{plan.street_test_question}\n\n" + "\n".join(cost_lines) + "\n\n"
            f"{digest_url}"
        )
        # Ratings measure Daniel's immediate reaction (Daniel's ruling
        # 2026-08-25): COOL = more like this, MEH = fine but nothing
        # special, SKIP = wasted my slot. "Implemented" is NOT a button:
        # it is measured by reality, via the ledger cross-link when a
        # paper's technique becomes an actual experiment.
        buttons = [
            [
                ("COOL", f"vid:{key}:cool"),
                ("MEH", f"vid:{key}:meh"),
                ("SKIP", f"vid:{key}:skip"),
            ],
            [
                ("CLEAR", f"vid:{key}:clear"),
                ("UNCLEAR", f"vid:{key}:unclear"),
            ],
        ]
        sent_ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        # Create the ledger item before Telegram exposes its buttons. A fast
        # tap can otherwise arrive before the existence-guarded callback has
        # anything to update, which loses the rating as "unknown video".
        table.put_item(
            Item={
                "experiment_id": f"video#{key}",
                "sk": "video",
                "track": track,
                "url": url,
                "title": title,
                "digest_key": digest_key,
                "video_key": video_key,
                "sent_ts": sent_ts,
                "rating": None,
                "rating_ts": None,
                "clarity": None,
                "clarity_ts": None,
                "topics": candidate_topics(candidate),
                "selection_policy": POLICY_VERSION,
                "candidate_set": candidate_set,
                "baseline_rank": baseline_rank,
                "exploration_status": exploration_status,
                "feedback_status": feedback_status,
                "baseline_selection": baseline_selection,
                "selection_probability": selection_probability,
                "render_path": "story" if story is not None else "template",
                "attempts": story.attempts if story is not None else None,
                "judge_score": story.judge_score if story is not None else None,
                "story_key": story_key,
                "visual_focus": (
                    story.storyboard.visual_focus if story is not None else None
                ),
                "selected_attempt": (
                    story.selected_attempt if story is not None else None
                ),
                "judge_passed": story.passed if story is not None else None,
                "estimated_model_cost_usd": model_cost,
                "cost_estimated": True,
                "model_calls": model_calls,
                "tagged_aws_mtd_cost_usd": aws_mtd_cost,
            }
        )
        notify(
            table,
            ssm_client,
            caption,
            buttons=buttons,
            video_path=str(video_path),
            video_s3_key=video_key,
        )
    return "sent"


@worker_app.command("explain")
def explain_command() -> None:
    """Scheduled daily-paper-video run (10:30 Amsterdam): the CORE, CLASSIC,
    and NOVEL tracks, each fetch -> dedup -> pick -> deep-read -> story (or
    template) -> deliver -> ledger. Every track is isolated in its own
    try/except: a
    failure pings a text fallback (the digest link if one was made, else
    the error, in STE style) and the run continues to the next track -
    silence must never mean broken.
    """
    state_table = _require_env("STATE_TABLE")
    results_bucket = _require_env("RESULTS_BUCKET")
    track_env = os.environ.get("TRACK", "all").strip().lower()
    deep_read_model = os.environ.get("DEEP_READ_MODEL", DEFAULT_DEEP_READ_MODEL)
    pick_model = os.environ.get("PICK_MODEL", DEFAULT_PICK_MODEL)
    story_model = os.environ.get("STORY_MODEL", deep_read_model)
    scene_model = os.environ.get("SCENE_MODEL", deep_read_model)
    judge_model = os.environ.get("JUDGE_MODEL", deep_read_model)

    tracks = list(EXPLAIN_TRACKS) if track_env == "all" else [track_env]
    if any(t not in EXPLAIN_TRACKS for t in tracks):
        typer.echo(
            f"error: invalid TRACK '{track_env}' (must be core, classic, novel, or all)",
            err=True,
        )
        raise typer.Exit(1)

    table = boto3.resource("dynamodb").Table(state_table)
    ssm_client = boto3.client("ssm")
    s3_client = boto3.client("s3")
    polly_client = boto3.client("polly")
    try:
        aws_mtd_cost = month_to_date_tagged_cost(
            boto3.client("ce", region_name="us-east-1"),
            project="agentlab",
        )
    except Exception as exc:  # noqa: BLE001 - cost visibility cannot block delivery
        typer.echo(f"cost explorer unavailable: {str(exc)[:200]}", err=True)
        aws_mtd_cost = None

    statuses: dict[str, str] = {}
    for track in tracks:
        partial: dict = {}
        try:
            statuses[track] = _run_explain_track(
                track,
                table,
                ssm_client,
                s3_client,
                polly_client,
                results_bucket,
                deep_read_model,
                pick_model,
                story_model,
                scene_model,
                judge_model,
                partial,
                aws_mtd_cost,
            )
        except Exception as exc:  # noqa: BLE001 - one track's failure must not sink the others
            statuses[track] = "failed"

            # Every failed track writes a ledger event, same shape as
            # run-arm's ARM_FAILED, before the fallback ping - a failure
            # must be recorded even if the ping itself then also fails.
            try:
                transition(
                    table,
                    f"explain-{datetime.now(UTC).strftime('%Y%m%d')}",
                    "TRACK_FAILED",
                    track,
                    str(exc)[:200],
                )
            except Exception as ledger_exc:  # noqa: BLE001 - a ledger write failure must not block the ping
                typer.echo(f"ledger write failed for {track}: {ledger_exc}", err=True)

            # A render/ffmpeg/manim subprocess failure carries the real
            # reason on .stderr; surface it so Daniel's ping says what
            # broke instead of just a bare CalledProcessError repr.
            stderr_note = ""
            if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
                stderr_text = exc.stderr
                if isinstance(stderr_text, bytes):
                    stderr_text = stderr_text.decode(errors="replace")
                stderr_note = f"\nWhat broke: {stderr_text[:400]}"

            if partial.get("digest_url"):
                claim = partial.get("claim", "")
                fallback = (
                    f"No video today for the {track} track. {claim}\n"
                    f"Read the full digest instead.\n{partial['digest_url']}{stderr_note}"
                ).strip()
            else:
                fallback = (
                    f"No video today for the {track} track. "
                    f"Error: {str(exc)[:400]}{stderr_note}"
                )
            try:
                notify(table, ssm_client, fallback)
            except Exception as ping_exc:  # noqa: BLE001 - never mask the track error
                typer.echo(f"fallback ping failed for {track}: {ping_exc}", err=True)

    summary = " ".join(f"{t}={statuses[t]}" for t in EXPLAIN_TRACKS if t in statuses)
    typer.echo(f"explain: {summary}")
