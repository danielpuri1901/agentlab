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
)

TABLE = "agentlab-state-test"
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
