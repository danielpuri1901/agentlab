"""Telegram approvals webhook: the only inbound path into AgentLab.

Runs as a plain-Python Lambda behind a function URL (auth NONE; the secret
header IS the auth, validated first thing). Telegram calls it on every
update. The handler acts only on callback-query taps that carry the right
secret header AND come from Daniel's Telegram user id; everything else is
logged and ignored with a 200 (Telegram retries non-200s, and a retry storm
on garbage is worthless).

A valid tap writes the verdict to the proposals ledger with a conditional
update (first tap wins; a second tap gets an "already decided" toast). An
APPROVE on a proposal that carries a prepared submit_body also auto-submits
that experiment: the stored body goes to SQS verbatim and a CREATED event is
recorded, exactly what `agentlab cloud submit` would have done.

Self-contained on purpose: the deployment package is this single file, so it
imports nothing from the agentlab package (stdlib + boto3 only, both present
in the Lambda runtime). The item shape and conditional expression mirror
src/agentlab/proposals.py; change both in the same commit or not at all.
"""

import base64
import json
import os
import urllib.request
from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

_SSM: dict = {}


def _param(name: str) -> str:
    if name not in _SSM:
        client = boto3.client("ssm")
        _SSM[name] = client.get_parameter(Name=name, WithDecryption=True)[
            "Parameter"
        ]["Value"]
    return _SSM[name]


def _telegram(method: str, payload: dict) -> None:
    """POST one Telegram Bot API call. Module-level so tests monkeypatch it."""
    token = _param(os.environ["TOKEN_PARAM"])
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=10).read()


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _ok() -> dict:
    return {"statusCode": 200, "body": "ok"}


def _set_verdict(table, pid: str, verdict: str) -> bool:
    """True if this tap decided the proposal; False if it was already decided."""
    try:
        table.update_item(
            Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"},
            UpdateExpression="SET #s = :v, verdict_ts = :ts, verdict_source = :src",
            ConditionExpression="#s = :proposed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":v": verdict,
                ":proposed": "PROPOSED",
                ":ts": _now(),
                ":src": "telegram_tap",
            },
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _auto_submit(table, pid: str) -> str | None:
    """Send an approved registered re-run to the fabric; None if not registered."""
    item = table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"}
    ).get("Item") or {}
    submit_body = item.get("submit_body")
    if not submit_body:
        return None
    experiment_id = json.loads(submit_body)["experiment_id"]
    boto3.client("sqs").send_message(
        QueueUrl=os.environ["QUEUE_URL"], MessageBody=submit_body
    )
    ts = _now()
    table.put_item(
        Item={
            "experiment_id": experiment_id,
            "sk": f"event#{ts}#CREATED",
            "event": "CREATED",
            "arm": None,
            "detail": f"auto-submitted from {pid}",
            "ts": ts,
        }
    )
    return experiment_id


def _build_video(table, pid: str) -> str | None:
    """Start a video build for an approved proposal's cited source.

    A proposal with no prepared experiment used to end at its verdict: the
    status changed and nothing happened, which is what "I accept the
    proposals and they build" expects to work. Every proposal cites exactly
    one source URL (proposer.py enforces it), and the explain task already
    builds a video from any URL through EXPLAIN_URL, so the approval starts
    that task. Returns the citation it started, or None.
    """
    cluster = os.environ.get("EXPLAIN_CLUSTER")
    task_definition = os.environ.get("EXPLAIN_TASK_DEFINITION")
    subnets = [s for s in os.environ.get("EXPLAIN_SUBNETS", "").split(",") if s]
    security_group = os.environ.get("EXPLAIN_SECURITY_GROUP")
    if not (cluster and task_definition and subnets and security_group):
        return None
    item = table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"}
    ).get("Item") or {}
    citation = str(item.get("citation") or "").strip()
    if not citation.startswith("http"):
        return None
    boto3.client("ecs").run_task(
        cluster=cluster,
        taskDefinition=task_definition,
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": subnets,
                "securityGroups": [security_group],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {
                    "name": "explain",
                    "command": ["worker", "explain"],
                    "environment": [
                        {"name": "TRACK", "value": "core"},
                        {"name": "EXPLAIN_URL", "value": citation},
                    ],
                }
            ]
        },
    )
    return citation


def _strip_buttons(markup: dict, pid: str) -> list:
    prefix = f"prop:{pid}:"
    rows = markup.get("inline_keyboard") or []
    return [
        row
        for row in rows
        if not any(
            str(button.get("callback_data", "")).startswith(prefix)
            for button in row
        )
    ]


# vid:<video_key>:<implement|learned|skip> - the daily-paper-video rating
# callback. A second, independent namespace on the same secret + Daniel-only
# check as prop:*; the two never overlap in the `data` prefix, so this block
# is a self-contained addition that never touches prop:* handling below.
# COOL/MEH/SKIP measure immediate reaction (Daniel's ruling 2026-08-25);
# implement/learned remain as LEGACY aliases so buttons on already-sent
# messages keep working, mapped onto the new vocabulary.
_VID_RATINGS = {
    "cool": "COOL",
    "meh": "MEH",
    "skip": "SKIP",
    "implement": "COOL",
    "learned": "MEH",
}
_VID_CLARITY = {
    "clear": "CLEAR",
    "unclear": "UNCLEAR",
}


def _set_rating(table, video_key: str, rating: str) -> bool:
    """True if video_key names a real video# item and the rating was
    written; False if the key is unknown (no write happens). Unlike
    _set_verdict, this has no status-guard beyond existence: re-rating is
    allowed by design (ruling: last tap wins), so a second tap on a known
    key still returns True and overwrites rating/rating_ts."""
    try:
        table.update_item(
            Key={"experiment_id": f"video#{video_key}", "sk": "video"},
            UpdateExpression="SET rating = :r, rating_ts = :ts",
            ConditionExpression="attribute_exists(experiment_id)",
            ExpressionAttributeValues={":r": rating, ":ts": _now()},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _set_clarity(table, video_key: str, clarity: str) -> bool:
    try:
        table.update_item(
            Key={"experiment_id": f"video#{video_key}", "sk": "video"},
            UpdateExpression="SET clarity = :value, clarity_ts = :ts",
            ConditionExpression="attribute_exists(experiment_id)",
            ExpressionAttributeValues={":value": clarity, ":ts": _now()},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def _strip_vid_buttons(markup: dict, video_key: str, actions: set[str]) -> list:
    callbacks = {f"vid:{video_key}:{action}" for action in actions}
    rows = markup.get("inline_keyboard") or []
    return [
        row
        for row in rows
        if not any(
            str(button.get("callback_data", "")) in callbacks for button in row
        )
    ]


def _handle_vid(callback, table, video_key: str, action: str) -> dict:
    if action in _VID_RATINGS:
        value = _VID_RATINGS[action]
        known = _set_rating(table, video_key, value)
        toast = f"Rated: {value}" if known else "unknown video"
        actions = set(_VID_RATINGS)
    else:
        value = _VID_CLARITY[action]
        known = _set_clarity(table, video_key, value)
        toast = f"Clarity: {value}" if known else "unknown video"
        actions = set(_VID_CLARITY)

    try:
        _telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback["id"], "text": toast[:200]},
        )
        message = callback.get("message")
        if message and known:
            # Every button on a video message belongs to that one video (see
            # worker.py's send_video buttons), so the stripped row list is
            # always sent explicitly (even when empty) rather than only on
            # a truthy check - otherwise Telegram would leave the rated
            # video's buttons showing.
            rows = _strip_vid_buttons(
                message.get("reply_markup") or {}, video_key, actions
            )
            _telegram(
                "editMessageReplyMarkup",
                {
                    "chat_id": message["chat"]["id"],
                    "message_id": message["message_id"],
                    "reply_markup": {"inline_keyboard": rows},
                },
            )
    except Exception as exc:  # noqa: BLE001 - message cosmetics must never lose a recorded rating
        print(f"telegram call failed after rating write: {exc}")

    return _ok()


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if headers.get("x-telegram-bot-api-secret-token") != _param(
        os.environ["SECRET_PARAM"]
    ):
        return {"statusCode": 403, "body": "forbidden"}

    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    update = json.loads(body)

    callback = update.get("callback_query")
    if not callback:
        return _ok()

    if callback.get("from", {}).get("id") != int(os.environ["ALLOWED_USER_ID"]):
        print(f"foreign tap ignored: from={callback.get('from', {}).get('id')}")
        return _ok()

    parts = (callback.get("data") or "").split(":")

    video_actions = _VID_RATINGS | _VID_CLARITY
    if len(parts) == 3 and parts[0] == "vid" and parts[2] in video_actions:
        table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
        return _handle_vid(callback, table, parts[1], parts[2])

    if len(parts) != 3 or parts[0] != "prop" or parts[2] not in ("approve", "reject"):
        print(f"unknown callback data ignored: {callback.get('data')!r}")
        return _ok()
    pid, action = parts[1], parts[2]
    verdict = "APPROVED" if action == "approve" else "REJECTED"

    table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
    decided = _set_verdict(table, pid, verdict)

    submitted = None
    submit_failed = False
    building = None
    if decided and verdict == "APPROVED":
        # The verdict is already written; an SQS/DynamoDB failure here must
        # not 5xx the handler, or Telegram's retry hits "already decided" and
        # the approved run is silently never submitted. Surface it in the
        # toast instead so Daniel can resubmit manually.
        try:
            submitted = _auto_submit(table, pid)
        except Exception as exc:  # noqa: BLE001
            submit_failed = True
            print(f"auto-submit failed after verdict write: pid={pid} {exc}")
        if submitted is None and not submit_failed:
            # No prepared experiment, so build a video of what it cited.
            # Same rule as auto-submit: the verdict is already written, so a
            # failure here is reported, never raised.
            try:
                building = _build_video(table, pid)
            except Exception as exc:  # noqa: BLE001
                print(f"video build failed after verdict write: pid={pid} {exc}")

    if not decided:
        toast = f"{pid} was already decided"
    elif submit_failed:
        toast = f"APPROVED but submit FAILED: {pid}. Resubmit manually."
    elif submitted:
        toast = f"APPROVED and submitted: {submitted}"
    elif building:
        toast = "APPROVED. Building the video now."
    else:
        toast = f"{verdict}: {pid}"

    try:
        _telegram(
            "answerCallbackQuery",
            {"callback_query_id": callback["id"], "text": toast[:200]},
        )
        message = callback.get("message")
        if message and decided:
            rows = _strip_buttons(message.get("reply_markup") or {}, pid)
            decision_line = f"\n\n{verdict}: {pid}"
            if submitted:
                decision_line += f"\nsubmitted: {submitted}"
            elif building:
                decision_line += "\nbuilding the video now"
            edit = {
                "chat_id": message["chat"]["id"],
                "message_id": message["message_id"],
                "text": (message.get("text") or "") + decision_line,
            }
            if rows:
                edit["reply_markup"] = {"inline_keyboard": rows}
            _telegram("editMessageText", edit)
    except Exception as exc:  # noqa: BLE001 - message cosmetics must never lose a recorded verdict
        print(f"telegram call failed after verdict write: {exc}")

    return _ok()
