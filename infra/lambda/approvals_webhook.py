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
    if len(parts) != 3 or parts[0] != "prop" or parts[2] not in ("approve", "reject"):
        print(f"unknown callback data ignored: {callback.get('data')!r}")
        return _ok()
    pid, action = parts[1], parts[2]
    verdict = "APPROVED" if action == "approve" else "REJECTED"

    table = boto3.resource("dynamodb").Table(os.environ["STATE_TABLE"])
    decided = _set_verdict(table, pid, verdict)

    submitted = None
    submit_failed = False
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

    if not decided:
        toast = f"{pid} was already decided"
    elif submit_failed:
        toast = f"APPROVED but submit FAILED: {pid}. Resubmit manually."
    elif submitted:
        toast = f"APPROVED and submitted: {submitted}"
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
