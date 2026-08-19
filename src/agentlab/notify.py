"""Telegram notification channel: event-driven pings to Daniel's phone.

Every message follows the STE rule: headline first, short sentences, no em
dash. Quiet hours (23:00-08:00 Europe/Amsterdam) queue pings into the
`pending_ping` partition of the state table instead of sending;
`flush_pending` delivers them oldest-first at the next scheduled run (the
08:00 flush schedule, or the start of any propose run).

AWS clients (SSM, the DynamoDB table) are passed in as parameters, matching
the worker.py convention, so tests run against moto without patching boto3.
"""

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from boto3.dynamodb.conditions import Key

TOKEN_PARAM = "/agentlab/telegram/bot-token"
CHAT_ID_PARAM = "/agentlab/telegram/chat-id"
QUIET_START_HOUR = 23
QUIET_END_HOUR = 8
PENDING_PARTITION = "pending_ping"
# DynamoDB items cap at 400KB; a queued photo rides inside the item as
# base64 (4/3 inflation), so anything past this raw size is dropped from
# the queue with a note rather than risking a rejected put.
MAX_QUEUED_PHOTO_BYTES = 250_000
AMSTERDAM = ZoneInfo("Europe/Amsterdam")


@dataclass(frozen=True)
class TelegramConfig:
    token: str
    chat_id: str


def now_amsterdam() -> datetime:
    """Module-level clock indirection; tests monkeypatch this."""
    return datetime.now(AMSTERDAM)


def in_quiet_hours(dt: datetime) -> bool:
    return dt.hour >= QUIET_START_HOUR or dt.hour < QUIET_END_HOUR


def load_config(ssm_client) -> TelegramConfig:
    token = ssm_client.get_parameter(Name=TOKEN_PARAM, WithDecryption=True)[
        "Parameter"
    ]["Value"]
    chat_id = ssm_client.get_parameter(Name=CHAT_ID_PARAM)["Parameter"]["Value"]
    return TelegramConfig(token=token, chat_id=chat_id)


def _inline_keyboard(buttons) -> dict:
    return {
        "inline_keyboard": [
            [{"text": text, "callback_data": data} for text, data in row]
            for row in buttons
        ]
    }


def send_message(config: TelegramConfig, text: str, buttons=None) -> None:
    payload = {"chat_id": config.chat_id, "text": text}
    if buttons:
        payload["reply_markup"] = _inline_keyboard(buttons)
    response = httpx.post(
        f"https://api.telegram.org/bot{config.token}/sendMessage",
        json=payload,
        timeout=30,
    )
    response.raise_for_status()


def send_photo(config: TelegramConfig, caption: str, photo_png: bytes) -> None:
    # Telegram caps captions at 1024 chars; callers keep photo texts short
    # (the finalize headline is well under), so truncation here is a guard,
    # not an expected path.
    response = httpx.post(
        f"https://api.telegram.org/bot{config.token}/sendPhoto",
        data={"chat_id": config.chat_id, "caption": caption[:1024]},
        files={"photo": ("chart.png", photo_png, "image/png")},
        timeout=30,
    )
    response.raise_for_status()


def _deliver(config: TelegramConfig, text: str, buttons, photo_png) -> None:
    if photo_png is not None:
        send_photo(config, text, photo_png)
        if buttons:
            send_message(config, text, buttons)
    else:
        send_message(config, text, buttons)


def queue_ping(table, text: str, buttons, photo_png) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sk = f"ping#{ts}#{secrets.token_hex(2)}"
    payload: dict = {"text": text, "buttons": buttons}
    if photo_png is not None:
        if len(photo_png) <= MAX_QUEUED_PHOTO_BYTES:
            payload["photo_b64"] = base64.b64encode(photo_png).decode("ascii")
        else:
            payload["text"] = text + "\n(chart omitted: too large to queue)"
    table.put_item(
        Item={
            "experiment_id": PENDING_PARTITION,
            "sk": sk,
            "payload": json.dumps(payload),
        }
    )
    return sk


def notify(table, ssm_client, text: str, buttons=None, photo_png=None) -> str:
    """Send now, or queue during quiet hours. Returns "sent" or "queued"."""
    if in_quiet_hours(now_amsterdam()):
        queue_ping(table, text, buttons, photo_png)
        return "queued"
    config = load_config(ssm_client)
    _deliver(config, text, buttons, photo_png)
    return "sent"


def flush_pending(table, ssm_client) -> int:
    """Deliver queued pings oldest-first.

    A send failure propagates: the failed ping and everything after it stay
    queued for the next flush, while already-delivered items are gone (each
    is deleted immediately after its successful send).
    No quiet-hours guard here by design: the callers are the 08:00 flush
    schedule and the daytime propose runs, so a flush call is always outside
    quiet hours.
    """
    # Accumulate items across pages
    items = []
    last_evaluated_key = None
    while True:
        query_kwargs = {
            "KeyConditionExpression": Key("experiment_id").eq(PENDING_PARTITION)
        }
        if last_evaluated_key:
            query_kwargs["ExclusiveStartKey"] = last_evaluated_key
        response = table.query(**query_kwargs)
        items.extend(response["Items"])
        last_evaluated_key = response.get("LastEvaluatedKey")
        if not last_evaluated_key:
            break

    if not items:
        return 0
    config = load_config(ssm_client)
    sent = 0
    for item in sorted(items, key=lambda i: i["sk"]):
        payload = json.loads(item["payload"])
        photo = (
            base64.b64decode(payload["photo_b64"])
            if "photo_b64" in payload
            else None
        )
        _deliver(config, payload["text"], payload.get("buttons"), photo)
        table.delete_item(
            Key={"experiment_id": PENDING_PARTITION, "sk": item["sk"]}
        )
        sent += 1
    return sent
