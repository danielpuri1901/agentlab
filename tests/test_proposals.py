"""Proposals ledger module tests, fully offline with moto."""

import json
from datetime import UTC, datetime

import boto3
import pytest
from moto import mock_aws

from agentlab import proposals as proposals_mod
from agentlab.proposals import (
    AlreadyDecided,
    count_created_today,
    file_proposal,
    generate_proposal_id,
    get_proposal,
    list_recent,
    set_verdict,
)

TABLE = "agentlab-state-test"
REGION = "us-east-1"


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
        yield table, None


def test_generate_proposal_id_format(monkeypatch):
    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 9, 30, tzinfo=UTC)
    )
    monkeypatch.setattr(proposals_mod, "random_hex", lambda: "ab12")
    assert generate_proposal_id() == "prop-20260819T093000Z-ab12"


def test_file_and_get_roundtrip(fabric):
    table, _ = fabric
    file_proposal(
        table, "prop-1", "Title", "Headline.", "https://x", "differs from 001",
        "new_hypothesis",
    )
    item = get_proposal(table, "prop-1")
    assert item["status"] == "PROPOSED"
    assert item["experiment_id"] == "proposal#prop-1"
    assert "submit_body" not in item


def test_submit_body_stored_as_json_string(fabric):
    table, _ = fabric
    file_proposal(
        table, "prop-2", "Rerun", "H.", "https://x", "d", "registered_rerun",
        submit_body={"experiment_id": "exp-1", "model": "bedrock/eu.amazon.nova-lite-v1:0"},
    )
    body = json.loads(get_proposal(table, "prop-2")["submit_body"])
    assert body["experiment_id"] == "exp-1"


def test_set_verdict_once_then_already_decided(fabric):
    table, _ = fabric
    file_proposal(table, "prop-3", "T", "H", "https://x", "d", "new_hypothesis")
    set_verdict(table, "prop-3", "APPROVED", "telegram_tap")
    item = get_proposal(table, "prop-3")
    assert item["status"] == "APPROVED"
    assert item["verdict_source"] == "telegram_tap"
    assert "verdict_ts" in item
    with pytest.raises(AlreadyDecided):
        set_verdict(table, "prop-3", "REJECTED", "telegram_tap")
    assert get_proposal(table, "prop-3")["status"] == "APPROVED"


def test_list_recent_newest_first_and_count_today(fabric, monkeypatch):
    table, _ = fabric
    # File three proposals with distinct created_ts by monkeypatching now()
    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
    )
    file_proposal(table, "prop-a", "Title A", "Headline A.", "https://x", "d1", "new_hypothesis")

    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 10, 0, tzinfo=UTC)
    )
    file_proposal(table, "prop-b", "Title B", "Headline B.", "https://x", "d2", "new_hypothesis")

    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 11, 0, tzinfo=UTC)
    )
    file_proposal(table, "prop-c", "Title C", "Headline C.", "https://x", "d3", "new_hypothesis")

    # Keep now() frozen to 2026-08-19 for count_created_today
    recent = list_recent(table, limit=2)
    assert len(recent) == 2
    assert recent[0]["created_ts"] > recent[1]["created_ts"]
    assert [r["pid"] for r in recent] == ["prop-c", "prop-b"]

    # Cross-day negative case: file a proposal from yesterday, verify count_created_today filters it out
    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 18, 12, 0, tzinfo=UTC)
    )
    file_proposal(table, "prop-old", "Old", "O.", "https://x", "d0", "new_hypothesis")

    # Re-freeze now to 2026-08-19 for count_created_today
    monkeypatch.setattr(
        proposals_mod, "now", lambda: datetime(2026, 8, 19, 15, 0, tzinfo=UTC)
    )
    assert count_created_today(table) == 3
