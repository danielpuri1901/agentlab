"""Telegram approvals webhook Lambda tests, fully offline with moto.

The handler is the only inbound path into AgentLab from the internet, so
these tests are the security surface: wrong secret, foreign Telegram user,
malformed callback data, double-tap idempotency, and the auto-submit path
must all behave exactly as specced. The Lambda is loaded by file path (its
deployment package is a single stdlib+boto3 file with no agentlab imports),
mirroring how it actually ships.
"""

import base64
import importlib.util
import json
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

_LAMBDA_PATH = Path(__file__).parent.parent / "infra" / "lambda" / "approvals_webhook.py"
spec = importlib.util.spec_from_file_location("approvals_webhook", _LAMBDA_PATH)
webhook = importlib.util.module_from_spec(spec)
sys.modules["approvals_webhook"] = webhook
spec.loader.exec_module(webhook)

TABLE = "agentlab-state-test"
QUEUE_NAME = "agentlab-experiments-test"
REGION = "us-east-1"
TOKEN_PARAM = "/agentlab/telegram/bot-token"
SECRET_PARAM = "/agentlab/telegram/webhook-secret"
ALLOWED_USER_ID = "123456789"

SUBMIT_BODY = json.dumps(
    {
        "experiment_id": "exp-9",
        "model": "mockllm/model",
        "tasks": 2,
        "repeats": 1,
        "n_facts": 3,
        "filler_turns": 8,
        "summary_budget": 50,
        "max_connections": 30,
        "baseline_style": "truncate",
        "candidate_style": "structured",
    }
)


@pytest.fixture(autouse=True)
def aws_credentials(monkeypatch):
    # moto never calls real AWS, but boto3 still needs *some* creds/region to
    # construct a client; these are fake and only used against the mock.
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture(autouse=True)
def reset_ssm_cache(monkeypatch):
    monkeypatch.setattr(webhook, "_SSM", {})


@pytest.fixture
def recorder(monkeypatch):
    """Captures (method, payload) tuples in place of real Telegram calls."""
    calls = []

    def fake_telegram(method, payload):
        calls.append((method, payload))

    monkeypatch.setattr(webhook, "_telegram", fake_telegram)
    return calls


@pytest.fixture
def fabric(monkeypatch):
    # moto's mock_aws must still be active when the handler runs, since it
    # constructs its own boto3 clients/resources at call time rather than
    # accepting injected ones; yielding from inside the `with` block keeps
    # the mock alive for the whole test body.
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

        sqs = boto3.client("sqs", region_name=REGION)
        queue_url = sqs.create_queue(QueueName=QUEUE_NAME)["QueueUrl"]

        ssm = boto3.client("ssm", region_name=REGION)
        ssm.put_parameter(Name=TOKEN_PARAM, Value="test-token", Type="SecureString")
        ssm.put_parameter(Name=SECRET_PARAM, Value="test-secret", Type="SecureString")

        monkeypatch.setenv("STATE_TABLE", TABLE)
        monkeypatch.setenv("QUEUE_URL", queue_url)
        monkeypatch.setenv("ALLOWED_USER_ID", ALLOWED_USER_ID)
        monkeypatch.setenv("TOKEN_PARAM", TOKEN_PARAM)
        monkeypatch.setenv("SECRET_PARAM", SECRET_PARAM)

        yield table, sqs, queue_url


def make_event(
    secret="test-secret",
    from_id=123456789,
    data="prop:p1:approve",
    text="msg text",
    keyboard=None,
):
    update = {
        "callback_query": {
            "id": "cb1",
            "from": {"id": from_id},
            "data": data,
            "message": {
                "message_id": 7,
                "chat": {"id": 123456789},
                "text": text,
                "reply_markup": {"inline_keyboard": keyboard or []},
            },
        }
    }
    return {
        "headers": {"x-telegram-bot-api-secret-token": secret},
        "body": json.dumps(update),
    }


def _ecs_recorder(real_client, runs, run=None):
    """Stand in for boto3.client("ecs") and record run_task calls."""

    class _Ecs:
        def run_task(self, **kwargs):
            if run is not None:
                return run(**kwargs)
            runs.append(kwargs)
            return {"tasks": [{"taskArn": "arn:task/1"}]}

    def factory(name, *args, **kwargs):
        if name == "ecs":
            return _Ecs()
        return real_client(name, *args, **kwargs)

    return factory


def _file_proposal(table, pid, submit_body=None):
    """Raw put_item mirroring src/agentlab/proposals.py file_proposal's shape."""
    item = {
        "experiment_id": f"proposal#{pid}",
        "sk": "proposal",
        "pid": pid,
        "status": "PROPOSED",
        "title": "Title",
        "headline": "Headline.",
        "citation": "https://example.com/paper",
        "distance": "differs from 001 in prompt structure",
        "kind": "new_hypothesis",
        "created_ts": "2026-08-19T09:00:00.000000Z",
    }
    if submit_body is not None:
        item["submit_body"] = submit_body
    table.put_item(Item=item)


def _get_proposal(table, pid):
    return table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": "proposal"}
    ).get("Item")


def _file_video(table, key, track="core"):
    """Raw put_item mirroring worker.py's video#<key> item shape (rating
    fields start unset, as the worker writes them right after send_video)."""
    table.put_item(
        Item={
            "experiment_id": f"video#{key}",
            "sk": "video",
            "track": track,
            "url": "https://example.com/paper",
            "title": "Title",
            "digest_key": f"digests/{key}.md",
            "video_key": f"videos/{key}.mp4",
            "sent_ts": "2026-08-24T10:30:00.000000Z",
            "rating": None,
            "rating_ts": None,
        }
    )


def _get_video(table, key):
    return table.get_item(Key={"experiment_id": f"video#{key}", "sk": "video"}).get(
        "Item"
    )


def _vid_keyboard(key):
    return [
        [
            {"text": "IMPLEMENT", "callback_data": f"vid:{key}:implement"},
            {"text": "LEARNED", "callback_data": f"vid:{key}:learned"},
            {"text": "SKIP", "callback_data": f"vid:{key}:skip"},
        ]
    ]


def test_wrong_secret_403(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")

    response = webhook.handler(make_event(secret="wrong-secret"), None)

    assert response["statusCode"] == 403
    assert _get_proposal(table, "p1")["status"] == "PROPOSED"
    assert recorder == []


def test_foreign_tap_ignored(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")

    response = webhook.handler(make_event(from_id=42), None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "PROPOSED"
    assert recorder == []


def test_non_callback_update_ok(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    event = {
        "headers": {"x-telegram-bot-api-secret-token": "test-secret"},
        "body": json.dumps({"message": {"text": "hello", "from": {"id": 1}}}),
    }

    response = webhook.handler(event, None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "PROPOSED"
    assert recorder == []


def test_malformed_callback_data_ignored(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")

    response = webhook.handler(make_event(data="drop tables"), None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "PROPOSED"
    assert recorder == []


def test_approve_sets_verdict_and_edits(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    _file_proposal(table, "p2")
    keyboard = [
        [
            {"text": "Approve p1", "callback_data": "prop:p1:approve"},
            {"text": "Reject p1", "callback_data": "prop:p1:reject"},
        ],
        [
            {"text": "Approve p2", "callback_data": "prop:p2:approve"},
            {"text": "Reject p2", "callback_data": "prop:p2:reject"},
        ],
    ]

    response = webhook.handler(
        make_event(data="prop:p1:approve", keyboard=keyboard), None
    )

    assert response["statusCode"] == 200
    item = _get_proposal(table, "p1")
    assert item["status"] == "APPROVED"
    assert item["verdict_source"] == "telegram_tap"
    assert "verdict_ts" in item
    # p2 must be untouched by p1's verdict.
    assert _get_proposal(table, "p2")["status"] == "PROPOSED"

    assert [method for method, _payload in recorder] == [
        "answerCallbackQuery",
        "editMessageText",
    ]
    _method, edit = recorder[1]
    assert edit["text"].endswith("APPROVED: p1")
    rows = edit["reply_markup"]["inline_keyboard"]
    assert len(rows) == 1
    remaining_data = {button["callback_data"] for button in rows[0]}
    assert remaining_data == {"prop:p2:approve", "prop:p2:reject"}


def test_reject_sets_rejected(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")

    response = webhook.handler(make_event(data="prop:p1:reject"), None)

    assert response["statusCode"] == 200
    item = _get_proposal(table, "p1")
    assert item["status"] == "REJECTED"
    assert item["verdict_source"] == "telegram_tap"

    assert [method for method, _payload in recorder] == [
        "answerCallbackQuery",
        "editMessageText",
    ]
    _method, edit = recorder[1]
    assert edit["text"].endswith("REJECTED: p1")


def test_double_tap_answers_already_decided(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    event = make_event(data="prop:p1:approve")

    first = webhook.handler(event, None)
    assert first["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    recorder.clear()

    second = webhook.handler(event, None)

    assert second["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    # Only the toast fires on the second tap, no editMessageText.
    assert len(recorder) == 1
    method, payload = recorder[0]
    assert method == "answerCallbackQuery"
    assert "already decided" in payload["text"]


def test_approve_without_submit_body_builds_a_video_of_the_citation(
    fabric, recorder, monkeypatch
):
    """Approving an idea has to build something, not just change a status."""
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    monkeypatch.setenv("EXPLAIN_CLUSTER", "agentlab")
    monkeypatch.setenv("EXPLAIN_TASK_DEFINITION", "agentlab-explain")
    monkeypatch.setenv("EXPLAIN_SUBNETS", "subnet-a,subnet-b")
    monkeypatch.setenv("EXPLAIN_SECURITY_GROUP", "sg-1")
    runs = []
    monkeypatch.setattr(
        webhook.boto3, "client", _ecs_recorder(webhook.boto3.client, runs)
    )

    response = webhook.handler(make_event(), None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    assert len(runs) == 1
    env = runs[0]["overrides"]["containerOverrides"][0]["environment"]
    assert {"name": "TRACK", "value": "core"} in env
    assert {"name": "EXPLAIN_URL", "value": "https://example.com/paper"} in env
    toasts = [c[1]["text"] for c in recorder if c[0] == "answerCallbackQuery"]
    assert any("Building the video" in t for t in toasts)


def test_approve_does_not_build_a_video_when_the_citation_is_not_a_url(
    fabric, recorder, monkeypatch
):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    table.update_item(
        Key={"experiment_id": "proposal#p1", "sk": "proposal"},
        UpdateExpression="SET citation = :c",
        ExpressionAttributeValues={":c": "see the paper"},
    )
    monkeypatch.setenv("EXPLAIN_CLUSTER", "agentlab")
    monkeypatch.setenv("EXPLAIN_TASK_DEFINITION", "agentlab-explain")
    monkeypatch.setenv("EXPLAIN_SUBNETS", "subnet-a")
    monkeypatch.setenv("EXPLAIN_SECURITY_GROUP", "sg-1")
    runs = []
    monkeypatch.setattr(
        webhook.boto3, "client", _ecs_recorder(webhook.boto3.client, runs)
    )

    webhook.handler(make_event(), None)

    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    assert runs == []


def test_a_failed_video_build_never_loses_the_verdict(fabric, recorder, monkeypatch):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    monkeypatch.setenv("EXPLAIN_CLUSTER", "agentlab")
    monkeypatch.setenv("EXPLAIN_TASK_DEFINITION", "agentlab-explain")
    monkeypatch.setenv("EXPLAIN_SUBNETS", "subnet-a")
    monkeypatch.setenv("EXPLAIN_SECURITY_GROUP", "sg-1")

    def boom(*args, **kwargs):
        raise RuntimeError("ecs unavailable")

    monkeypatch.setattr(
        webhook.boto3, "client", _ecs_recorder(webhook.boto3.client, [], run=boom)
    )

    response = webhook.handler(make_event(), None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"


def test_approve_with_submit_body_auto_submits(fabric, recorder):
    table, sqs, queue_url = fabric
    _file_proposal(table, "p1", submit_body=SUBMIT_BODY)

    response = webhook.handler(make_event(data="prop:p1:approve"), None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    received = messages.get("Messages", [])
    assert len(received) == 1
    assert received[0]["Body"] == SUBMIT_BODY

    items = table.scan()["Items"]
    created = [i for i in items if i.get("event") == "CREATED"]
    assert len(created) == 1
    assert created[0]["experiment_id"] == "exp-9"
    assert created[0]["detail"] == "auto-submitted from p1"

    answer_calls = [payload for method, payload in recorder if method == "answerCallbackQuery"]
    assert len(answer_calls) == 1
    assert "submitted" in answer_calls[0]["text"]


def test_base64_encoded_body(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    update = {
        "callback_query": {
            "id": "cb1",
            "from": {"id": 123456789},
            "data": "prop:p1:approve",
            "message": {
                "message_id": 7,
                "chat": {"id": 123456789},
                "text": "msg text",
                "reply_markup": {"inline_keyboard": []},
            },
        }
    }
    raw = json.dumps(update).encode("utf-8")
    event = {
        "headers": {"x-telegram-bot-api-secret-token": "test-secret"},
        "body": base64.b64encode(raw).decode("ascii"),
        "isBase64Encoded": True,
    }

    response = webhook.handler(event, None)

    assert response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    assert [method for method, _payload in recorder] == [
        "answerCallbackQuery",
        "editMessageText",
    ]


def test_telegram_failure_does_not_lose_verdict(fabric, monkeypatch):
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")

    def raiser(method, payload):
        raise RuntimeError("telegram is down")

    monkeypatch.setattr(webhook, "_telegram", raiser)

    response = webhook.handler(make_event(data="prop:p1:approve"), None)

    assert response["statusCode"] == 200
    item = _get_proposal(table, "p1")
    assert item["status"] == "APPROVED"
    assert item["verdict_source"] == "telegram_tap"


def test_double_tap_with_submit_body_submits_exactly_once(fabric, recorder):
    # Pins `if decided and verdict == "APPROVED":` - a mutant that drops the
    # `decided` guard would re-run _auto_submit on the second (already
    # decided) tap, since `verdict` alone is still "APPROVED" for an approve
    # callback regardless of whether this tap actually won the race.
    table, sqs, queue_url = fabric
    submit_body = json.dumps({"experiment_id": "exp-race", "model": "mockllm/model"})
    _file_proposal(table, "p1", submit_body=submit_body)
    event = make_event(data="prop:p1:approve")

    first = webhook.handler(event, None)
    assert first["statusCode"] == 200
    recorder.clear()

    second = webhook.handler(event, None)
    assert second["statusCode"] == 200

    assert _get_proposal(table, "p1")["status"] == "APPROVED"

    messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
    received = messages.get("Messages", [])
    assert len(received) == 1

    items = table.scan()["Items"]
    created = [i for i in items if i.get("event") == "CREATED"]
    assert len(created) == 1
    assert created[0]["experiment_id"] == "exp-race"

    # Second tap: only its toast fires, no repeat submit.
    assert len(recorder) == 1
    method, payload = recorder[0]
    assert method == "answerCallbackQuery"
    assert "already decided" in payload["text"]


def test_wrong_secret_rejected_before_parsing(fabric, recorder):
    # Pins that the secret header check happens before json.loads(body); a
    # malformed body must still produce a clean 403, never an unhandled
    # JSONDecodeError / 500.
    event = {
        "headers": {"x-telegram-bot-api-secret-token": "wrong-secret"},
        "body": "not valid json {{{",
    }

    response = webhook.handler(event, None)

    assert response["statusCode"] == 403
    assert recorder == []


def test_auto_submit_failure_keeps_verdict_and_warns(fabric, recorder, monkeypatch):
    # The verdict write is the point of no return: if auto-submit then blows
    # up (SQS outage), the handler must still return 200, the proposal must
    # stay APPROVED, and the toast must say the submit failed so Daniel can
    # resubmit manually instead of trusting a misleading "submitted" toast.
    table, _, _ = fabric
    _file_proposal(
        table,
        "p-subfail",
        submit_body=json.dumps({"experiment_id": "exp-subfail", "model": "bedrock/x"}),
    )

    def exploding_submit(table_arg, pid_arg):
        raise RuntimeError("sqs down")

    monkeypatch.setattr(webhook, "_auto_submit", exploding_submit)
    response = webhook.handler(make_event(data="prop:p-subfail:approve"), None)

    assert response["statusCode"] == 200
    item = _get_proposal(table, "p-subfail")
    assert item["status"] == "APPROVED"
    toasts = [p["text"] for m, p in recorder if m == "answerCallbackQuery"]
    assert len(toasts) == 1 and "FAILED" in toasts[0] and "p-subfail" in toasts[0]


def test_vid_rating_happy_path(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_video(table, "core-20260824T103000Z-a1b2")
    keyboard = _vid_keyboard("core-20260824T103000Z-a1b2")

    response = webhook.handler(
        make_event(
            data="vid:core-20260824T103000Z-a1b2:implement",
            text="video caption",
            keyboard=keyboard,
        ),
        None,
    )

    assert response["statusCode"] == 200
    item = _get_video(table, "core-20260824T103000Z-a1b2")
    assert item["rating"] == "COOL"
    assert item["rating_ts"] is not None

    assert [method for method, _payload in recorder] == [
        "answerCallbackQuery",
        "editMessageReplyMarkup",
    ]
    _toast_method, toast_payload = recorder[0]
    assert toast_payload["text"] == "Rated: COOL"
    _edit_method, edit = recorder[1]
    # The video's own buttons are stripped entirely (single-video message,
    # so the row list goes to empty, sent explicitly).
    assert edit["reply_markup"]["inline_keyboard"] == []
    assert "text" not in edit


def test_vid_unknown_key_ignored(fabric, recorder):
    table, _sqs, _queue_url = fabric

    response = webhook.handler(make_event(data="vid:no-such-key:implement"), None)

    assert response["statusCode"] == 200
    assert _get_video(table, "no-such-key") is None
    # Only the toast fires; no button edit for an unknown key.
    assert [method for method, _payload in recorder] == ["answerCallbackQuery"]
    _method, payload = recorder[0]
    assert payload["text"] == "unknown video"


def test_vid_second_rating_overwrites(fabric, recorder):
    # Ruling: re-rating is allowed, last tap wins - unlike prop:*'s
    # first-tap-wins semantics, a second vid: tap on the same key still
    # succeeds and the stored rating reflects the most recent tap.
    table, _sqs, _queue_url = fabric
    _file_video(table, "novel-20260824T103000Z-c3d4")
    keyboard = _vid_keyboard("novel-20260824T103000Z-c3d4")

    first = webhook.handler(
        make_event(data="vid:novel-20260824T103000Z-c3d4:learned", keyboard=keyboard),
        None,
    )
    assert first["statusCode"] == 200
    item_after_first = _get_video(table, "novel-20260824T103000Z-c3d4")
    assert item_after_first["rating"] == "MEH"
    first_ts = item_after_first["rating_ts"]
    recorder.clear()

    second = webhook.handler(
        make_event(data="vid:novel-20260824T103000Z-c3d4:skip", keyboard=keyboard),
        None,
    )

    assert second["statusCode"] == 200
    item_after_second = _get_video(table, "novel-20260824T103000Z-c3d4")
    assert item_after_second["rating"] == "SKIP"
    assert item_after_second["rating_ts"] != first_ts

    # The second tap gets its own "Rated:" toast, not an "already decided"
    # style rejection - this is the behavior that distinguishes vid: from
    # prop:'s first-tap-wins conditional.
    toasts = [p["text"] for m, p in recorder if m == "answerCallbackQuery"]
    assert toasts == ["Rated: SKIP"]


def test_vid_clarity_is_collected_separately_from_topic_rating(fabric, recorder):
    table, _sqs, _queue_url = fabric
    key = "core-20260824T103000Z-clear"
    _file_video(table, key)
    keyboard = [
        [
            {"text": "COOL", "callback_data": f"vid:{key}:cool"},
            {"text": "MEH", "callback_data": f"vid:{key}:meh"},
            {"text": "SKIP", "callback_data": f"vid:{key}:skip"},
        ],
        [
            {"text": "CLEAR", "callback_data": f"vid:{key}:clear"},
            {"text": "UNCLEAR", "callback_data": f"vid:{key}:unclear"},
        ],
    ]

    response = webhook.handler(
        make_event(data=f"vid:{key}:unclear", keyboard=keyboard),
        None,
    )

    assert response["statusCode"] == 200
    item = _get_video(table, key)
    assert item["clarity"] == "UNCLEAR"
    assert item["clarity_ts"] is not None
    edit = next(
        payload for method, payload in recorder if method == "editMessageReplyMarkup"
    )
    remaining = edit["reply_markup"]["inline_keyboard"]
    assert remaining == keyboard[:1]


def test_vid_topic_rating_preserves_clarity_buttons(fabric, recorder):
    table, _sqs, _queue_url = fabric
    key = "core-20260824T103000Z-topic"
    _file_video(table, key)
    keyboard = [
        [
            {"text": "COOL", "callback_data": f"vid:{key}:cool"},
            {"text": "MEH", "callback_data": f"vid:{key}:meh"},
            {"text": "SKIP", "callback_data": f"vid:{key}:skip"},
        ],
        [
            {"text": "CLEAR", "callback_data": f"vid:{key}:clear"},
            {"text": "UNCLEAR", "callback_data": f"vid:{key}:unclear"},
        ],
    ]

    response = webhook.handler(
        make_event(data=f"vid:{key}:cool", keyboard=keyboard),
        None,
    )

    assert response["statusCode"] == 200
    item = _get_video(table, key)
    assert item["rating"] == "COOL"
    assert "clarity" not in item
    edit = next(
        payload for method, payload in recorder if method == "editMessageReplyMarkup"
    )
    remaining = edit["reply_markup"]["inline_keyboard"]
    assert remaining == keyboard[1:]


def test_vid_foreign_user_ignored(fabric, recorder):
    table, _sqs, _queue_url = fabric
    _file_video(table, "classic-20260824T103000Z-e5f6")

    response = webhook.handler(
        make_event(from_id=42, data="vid:classic-20260824T103000Z-e5f6:implement"),
        None,
    )

    assert response["statusCode"] == 200
    item = _get_video(table, "classic-20260824T103000Z-e5f6")
    assert item["rating"] is None
    assert recorder == []


def test_vid_malformed_action_ignored(fabric, recorder):
    # A vid: prefix with a rating word outside {implement, learned, skip}
    # must fall through to the generic "unknown callback data" branch (same
    # as prop:'s malformed-action guard), not crash on the _VID_RATINGS
    # lookup.
    table, _sqs, _queue_url = fabric
    _file_video(table, "core-20260824T103000Z-a1b2")

    response = webhook.handler(
        make_event(data="vid:core-20260824T103000Z-a1b2:maybe"), None
    )

    assert response["statusCode"] == 200
    item = _get_video(table, "core-20260824T103000Z-a1b2")
    assert item["rating"] is None
    assert recorder == []


def test_prop_and_vid_coexist(fabric, recorder):
    # prop:* and vid:* are independent namespaces on the same webhook; a
    # prop tap must not touch any video item and vice versa.
    table, _sqs, _queue_url = fabric
    _file_proposal(table, "p1")
    _file_video(table, "core-20260824T103000Z-a1b2")

    prop_response = webhook.handler(make_event(data="prop:p1:approve"), None)
    assert prop_response["statusCode"] == 200
    assert _get_proposal(table, "p1")["status"] == "APPROVED"
    assert _get_video(table, "core-20260824T103000Z-a1b2")["rating"] is None
    recorder.clear()

    vid_response = webhook.handler(
        make_event(data="vid:core-20260824T103000Z-a1b2:learned"), None
    )
    assert vid_response["statusCode"] == 200
    assert _get_video(table, "core-20260824T103000Z-a1b2")["rating"] == "MEH"
    assert _get_proposal(table, "p1")["status"] == "APPROVED"


def test_verdict_write_stays_in_sync_with_proposals_module():
    # Tripwire for the deliberate duplication: the Lambda re-implements
    # proposals.set_verdict because its deployment package is a single file.
    # If either side's update drifts, this fails before production does.
    from pathlib import Path

    lambda_src = _LAMBDA_PATH.read_text()
    module_src = (
        Path(__file__).parent.parent / "src" / "agentlab" / "proposals.py"
    ).read_text()
    for literal in (
        'UpdateExpression="SET #s = :v, verdict_ts = :ts, verdict_source = :src"',
        'ConditionExpression="#s = :proposed"',
    ):
        assert literal in lambda_src, f"lambda lost: {literal}"
        assert literal in module_src, f"proposals.py lost: {literal}"
