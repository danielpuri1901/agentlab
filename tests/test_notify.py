"""Telegram notify channel, fully offline: moto stands in for SSM/DynamoDB,
a monkeypatched httpx.post captures every Telegram call."""

import base64
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3
import pytest
from moto import mock_aws

from agentlab import notify as notify_mod
from agentlab.notify import (
    CHAT_ID_PARAM,
    PENDING_PARTITION,
    TOKEN_PARAM,
    flush_pending,
    in_quiet_hours,
    notify,
    send_video,
)

TABLE = "agentlab-state-test"
BUCKET = "agentlab-results-test"
REGION = "us-east-1"
AMS = ZoneInfo("Europe/Amsterdam")


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def fabric():
    with mock_aws():
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
        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name=TOKEN_PARAM, Value="test-token", Type="SecureString")
        ssm.put_parameter(Name=CHAT_ID_PARAM, Value="6309668956", Type="String")
        yield table, ssm


@pytest.fixture
def fabric_with_s3(fabric):
    # Same table/ssm as `fabric`, plus a real (moto-mocked) S3 bucket so the
    # video-queue flush test can round-trip an actual upload/download, not a
    # stubbed one.
    table, ssm = fabric
    s3 = boto3.client("s3", region_name=REGION)
    s3.create_bucket(Bucket=BUCKET)
    yield table, ssm, s3


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


def _daytime(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 14, 0, tzinfo=AMS)
    )


def _quiet(monkeypatch):
    monkeypatch.setattr(
        notify_mod, "now_amsterdam", lambda: datetime(2026, 8, 19, 2, 0, tzinfo=AMS)
    )


def test_quiet_hours_boundaries():
    assert not in_quiet_hours(datetime(2026, 8, 19, 22, 59, tzinfo=AMS))
    assert in_quiet_hours(datetime(2026, 8, 19, 23, 0, tzinfo=AMS))
    assert in_quiet_hours(datetime(2026, 8, 19, 7, 59, tzinfo=AMS))
    assert not in_quiet_hours(datetime(2026, 8, 19, 8, 0, tzinfo=AMS))


def test_notify_daytime_sends_with_buttons(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _daytime(monkeypatch)
    status = notify(
        table, ssm, "hello", buttons=[[("APPROVE 1", "prop:p1:approve")]]
    )
    assert status == "sent"
    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    assert kwargs["json"]["chat_id"] == "6309668956"
    assert kwargs["json"]["reply_markup"]["inline_keyboard"][0][0] == {
        "text": "APPROVE 1",
        "callback_data": "prop:p1:approve",
    }


def test_notify_quiet_queues_instead_of_sending(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    status = notify(table, ssm, "night finding", photo_png=b"pngbytes")
    assert status == "queued"
    assert telegram_calls == []
    items = table.scan()["Items"]
    pending = [i for i in items if i["experiment_id"] == PENDING_PARTITION]
    assert len(pending) == 1
    payload = json.loads(pending[0]["payload"])
    assert payload["text"] == "night finding"
    assert base64.b64decode(payload["photo_b64"]) == b"pngbytes"


def test_flush_sends_in_order_and_deletes(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "first")
    notify(table, ssm, "second", buttons=[[("A", "prop:x:approve")]])
    _daytime(monkeypatch)
    sent = flush_pending(table, ssm)
    assert sent == 2
    texts = [kwargs["json"]["text"] for _, kwargs in telegram_calls]
    assert texts == ["first", "second"]
    assert telegram_calls[1][1]["json"]["reply_markup"]["inline_keyboard"] == [
        [{"text": "A", "callback_data": "prop:x:approve"}]
    ]
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert remaining == []


def test_flush_photo_roundtrip_uses_sendphoto(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "with chart", photo_png=b"img")
    sent = flush_pending(table, ssm)
    assert sent == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendPhoto")
    assert kwargs["files"]["photo"][1] == b"img"


def test_flush_stops_at_first_failure_and_keeps_remainder(fabric, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "one")
    notify(table, ssm, "two")

    def exploding_post(url, **kwargs):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(notify_mod.httpx, "post", exploding_post)
    with pytest.raises(RuntimeError):
        flush_pending(table, ssm)
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert len(remaining) == 2


def test_flush_partial_failure_keeps_only_unsent(fabric, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "one")
    notify(table, ssm, "two")

    call_count = [0]

    class FakeResponse:
        def raise_for_status(self):
            pass

    def selective_post(url, **kwargs):
        call_count[0] += 1
        if call_count[0] > 1:
            raise RuntimeError("telegram down")
        return FakeResponse()

    monkeypatch.setattr(notify_mod.httpx, "post", selective_post)
    with pytest.raises(RuntimeError):
        flush_pending(table, ssm)
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert len(remaining) == 1
    payload = json.loads(remaining[0]["payload"])
    assert payload["text"] == "two"


def test_photo_caption_truncated_to_1024(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _daytime(monkeypatch)
    long_text = "x" * 1500
    notify(table, ssm, long_text, photo_png=b"img")
    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendPhoto")
    assert len(kwargs["data"]["caption"]) == 1024


def test_oversized_photo_dropped_from_queue(fabric, telegram_calls, monkeypatch):
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(table, ssm, "big chart", photo_png=b"x" * 300_000)
    items = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    payload = json.loads(items[0]["payload"])
    assert "photo_b64" not in payload
    assert "chart omitted" in payload["text"]


# ---------------------------------------------------------------------------
# send_video / notify(video_path=...) / video queue + flush
# ---------------------------------------------------------------------------


def _config():
    return notify_mod.TelegramConfig(token="test-token", chat_id="6309668956")


def test_send_video_caption_truncated_and_buttons_present(telegram_calls, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")
    long_caption = "x" * 1500

    send_video(
        _config(),
        long_caption,
        video_path,
        buttons=[[("IMPLEMENT", "vid:k1:implement"), ("SKIP", "vid:k1:skip")]],
    )

    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendVideo")
    assert len(kwargs["data"]["caption"]) == 1024
    assert kwargs["files"]["video"][0] == "clip.mp4"
    reply_markup = json.loads(kwargs["data"]["reply_markup"])
    assert reply_markup["inline_keyboard"][0] == [
        {"text": "IMPLEMENT", "callback_data": "vid:k1:implement"},
        {"text": "SKIP", "callback_data": "vid:k1:skip"},
    ]


def test_send_video_omits_reply_markup_without_buttons(telegram_calls, tmp_path):
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")

    send_video(_config(), "caption", video_path)

    assert "reply_markup" not in telegram_calls[0][1]["data"]


def test_notify_daytime_sends_video_directly_no_followup_message(
    fabric, telegram_calls, monkeypatch, tmp_path
):
    # Unlike the photo path (send_photo, then a separate buttons message),
    # sendVideo carries reply_markup itself, so exactly one Telegram call is
    # expected here.
    table, ssm = fabric
    _daytime(monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")

    status = notify(
        table,
        ssm,
        "claim\n\nstreet test question\n\nhttps://digest.example/x",
        buttons=[[("IMPLEMENT", "vid:k1:implement")]],
        video_path=video_path,
        video_s3_key="videos/k1.mp4",
    )

    assert status == "sent"
    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendVideo")
    assert kwargs["data"]["caption"].startswith("claim")
    reply_markup = json.loads(kwargs["data"]["reply_markup"])
    assert reply_markup["inline_keyboard"][0][0]["callback_data"] == "vid:k1:implement"


def test_notify_quiet_hours_queues_video_s3_key_not_bytes(
    fabric, telegram_calls, monkeypatch, tmp_path
):
    table, ssm = fabric
    _quiet(monkeypatch)
    video_path = tmp_path / "clip.mp4"
    video_path.write_bytes(b"fake-mp4-bytes")

    status = notify(
        table,
        ssm,
        "no video tonight",
        buttons=[[("LEARNED", "vid:k2:learned")]],
        video_path=video_path,
        video_s3_key="videos/k2.mp4",
    )

    assert status == "queued"
    assert telegram_calls == []
    items = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert len(items) == 1
    payload = json.loads(items[0]["payload"])
    assert payload["video_s3_key"] == "videos/k2.mp4"
    assert "photo_b64" not in payload
    assert payload["buttons"] == [[["LEARNED", "vid:k2:learned"]]]


def test_flush_video_downloads_from_s3_and_sends(fabric_with_s3, monkeypatch):
    table, ssm, s3 = fabric_with_s3
    _quiet(monkeypatch)
    video_bytes = b"real-mp4-bytes-from-s3"
    s3.put_object(Bucket=BUCKET, Key="videos/k3.mp4", Body=video_bytes)

    notify(
        table,
        ssm,
        "queued video",
        buttons=[[("SKIP", "vid:k3:skip")]],
        video_path="/tmp/does-not-exist-at-flush-time.mp4",
        video_s3_key="videos/k3.mp4",
    )

    # A dedicated fake_post, not the shared `telegram_calls` fixture: it must
    # read the video field's bytes while send_video's `with video_path.open`
    # block is still open (i.e. during the call itself), because that block
    # closes the handle the moment send_video returns - capturing the raw
    # kwargs dict for later inspection would hold a reference to an
    # already-closed file.
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            pass

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["reply_markup"] = kwargs["data"].get("reply_markup")
        filename, handle, _content_type = kwargs["files"]["video"]
        captured["video_filename"] = filename
        captured["video_bytes"] = handle.read()
        return FakeResponse()

    monkeypatch.setattr(notify_mod.httpx, "post", fake_post)

    _daytime(monkeypatch)
    sent = flush_pending(table, ssm, s3, BUCKET)

    assert sent == 1
    assert captured["url"].endswith("/sendVideo")
    assert captured["video_bytes"] == video_bytes
    reply_markup = json.loads(captured["reply_markup"])
    assert reply_markup["inline_keyboard"][0][0]["callback_data"] == "vid:k3:skip"
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert remaining == []


def test_flush_video_s3_download_failure_degrades_and_later_pings_still_flush(
    fabric_with_s3, telegram_calls, monkeypatch
):
    # The queued object is missing from S3 (deleted, expired, whatever);
    # download_file raises a real ClientError from moto. That one ping must
    # degrade to text-plus-note and get deleted rather than raising and
    # leaving the rest of the queue (including the ping queued after it)
    # stuck forever.
    table, ssm, s3 = fabric_with_s3
    _quiet(monkeypatch)
    notify(
        table,
        ssm,
        "video that vanished",
        buttons=[[("SKIP", "vid:k5:skip")]],
        video_path="/tmp/does-not-exist.mp4",
        video_s3_key="videos/does-not-exist.mp4",
    )
    notify(table, ssm, "a plain ping queued right after")

    _daytime(monkeypatch)
    sent = flush_pending(table, ssm, s3, BUCKET)

    assert sent == 2
    assert len(telegram_calls) == 2
    first_url, first_kwargs = telegram_calls[0]
    assert first_url.endswith("/sendMessage")
    assert "(video unavailable)" in first_kwargs["json"]["text"]
    second_url, second_kwargs = telegram_calls[1]
    assert second_url.endswith("/sendMessage")
    assert second_kwargs["json"]["text"] == "a plain ping queued right after"
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert remaining == []


def test_flush_video_without_s3_client_degrades_to_text_not_raise(
    fabric, telegram_calls, monkeypatch
):
    # A queued video ping flushed with no s3_client/bucket (e.g. flush-pings
    # ran without RESULTS_BUCKET set) must not wedge the queue: it degrades
    # to the text ping with a "(video unavailable)" note, and the item is
    # still deleted like any other successful delivery.
    table, ssm = fabric
    _quiet(monkeypatch)
    notify(
        table,
        ssm,
        "queued video, no s3 client at flush time",
        video_path="/tmp/whatever.mp4",
        video_s3_key="videos/k4.mp4",
    )
    _daytime(monkeypatch)
    sent = flush_pending(table, ssm)

    assert sent == 1
    assert len(telegram_calls) == 1
    url, kwargs = telegram_calls[0]
    assert url.endswith("/sendMessage")
    assert "(video unavailable)" in kwargs["json"]["text"]
    remaining = [
        i for i in table.scan()["Items"] if i["experiment_id"] == PENDING_PARTITION
    ]
    assert remaining == []
