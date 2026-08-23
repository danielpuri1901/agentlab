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
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
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


def send_video(config: TelegramConfig, caption: str, video_path, buttons=None) -> None:
    """sendVideo, multipart, caption capped at 1024 chars like send_photo.

    Unlike send_photo (whose caller sends a separate buttons message after
    the photo), sendVideo takes reply_markup directly in the same multipart
    `data` field as a JSON string - the same rule Telegram's Bot API uses
    for every complex (non-scalar) param on a multipart request - so the
    buttons ride on the video message itself, no follow-up send_message.
    """
    data: dict = {"chat_id": config.chat_id, "caption": caption[:1024]}
    if buttons:
        data["reply_markup"] = json.dumps(_inline_keyboard(buttons))
    video_path = Path(video_path)
    with video_path.open("rb") as handle:
        response = httpx.post(
            f"https://api.telegram.org/bot{config.token}/sendVideo",
            data=data,
            files={"video": (video_path.name, handle, "video/mp4")},
            timeout=120,
        )
    response.raise_for_status()


def _deliver(config: TelegramConfig, text: str, buttons, photo_png, video_path=None) -> None:
    if video_path is not None:
        send_video(config, text, video_path, buttons=buttons)
    elif photo_png is not None:
        send_photo(config, text, photo_png)
        if buttons:
            send_message(config, text, buttons)
    else:
        send_message(config, text, buttons)


def queue_ping(table, text: str, buttons, photo_png, video_s3_key: str | None = None) -> str:
    ts = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
    sk = f"ping#{ts}#{secrets.token_hex(2)}"
    payload: dict = {"text": text, "buttons": buttons}
    if video_s3_key is not None:
        # Videos are far too large to inline as base64 in a 400KB DynamoDB
        # item (unlike the photo path below); store the S3 key instead and
        # let flush_pending download it fresh at delivery time.
        payload["video_s3_key"] = video_s3_key
    elif photo_png is not None:
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


def notify(
    table,
    ssm_client,
    text: str,
    buttons=None,
    photo_png=None,
    video_path=None,
    video_s3_key: str | None = None,
) -> str:
    """Send now, or queue during quiet hours. Returns "sent" or "queued".

    `video_path` is a local file sent immediately during daytime.
    `video_s3_key` is the same video's already-uploaded S3 key, used only
    for the quiet-hours queue: the caller (worker.py's explain_command)
    uploads to S3 before calling notify, so a queued ping can be flushed
    later even after the local temp file is gone.
    """
    if in_quiet_hours(now_amsterdam()):
        queue_ping(table, text, buttons, photo_png, video_s3_key)
        return "queued"
    config = load_config(ssm_client)
    _deliver(config, text, buttons, photo_png, video_path)
    return "sent"


def flush_pending(table, ssm_client, s3_client=None, bucket: str | None = None) -> int:
    """Deliver queued pings oldest-first.

    A send failure propagates: the failed ping and everything after it stay
    queued for the next flush, while already-delivered items are gone (each
    is deleted immediately after its successful send).
    No quiet-hours guard here by design: the callers are the 08:00 flush
    schedule and the daytime propose runs, so a flush call is always outside
    quiet hours.
    A queued video ping (payload carries "video_s3_key") needs `s3_client`
    and `bucket` to re-download the file to a tmp path before sending;
    callers that never queue videos may omit both.
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
        video_path = None
        if "video_s3_key" in payload:
            if s3_client is None or bucket is None:
                raise RuntimeError(
                    "queued video ping needs s3_client and bucket to flush"
                )
            tmp_dir = tempfile.mkdtemp(prefix="agentlab-flush-video-")
            video_path = str(Path(tmp_dir) / "video.mp4")
            s3_client.download_file(bucket, payload["video_s3_key"], video_path)
        _deliver(config, payload["text"], payload.get("buttons"), photo, video_path)
        table.delete_item(
            Key={"experiment_id": PENDING_PARTITION, "sk": item["sk"]}
        )
        sent += 1
    return sent
