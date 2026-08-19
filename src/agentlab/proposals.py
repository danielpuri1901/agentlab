"""Proposals ledger on the existing agentlab-state table.

One item per proposal: PK `proposal#<pid>`, SK the literal "proposal"
(the spec's "PK proposal#<id>" slot). Status moves PROPOSED -> APPROVED |
REJECTED exactly once via a conditional update; a second tap raises
AlreadyDecided instead of overwriting. Listing and daily counts use a table
Scan, which is fine at this volume (a handful of items per day); revisit if
proposals ever reach thousands.

The approvals Lambda (infra/lambda/approvals_webhook.py) deliberately does
NOT import this module (its deployment package is a single file); it
re-implements set_verdict's exact update. If the item shape or the
conditional expression here changes, change the Lambda in the same commit.
"""

import json
import secrets
from datetime import UTC, datetime

from boto3.dynamodb.conditions import Attr
from botocore.exceptions import ClientError

PROPOSAL_SK = "proposal"
DAILY_CAP = 3


class AlreadyDecided(Exception):
    """The proposal already has a verdict; a second tap must not overwrite it."""


def now() -> datetime:
    """Module-level clock indirection; tests monkeypatch this."""
    return datetime.now(UTC)


def random_hex() -> str:
    """Module-level randomness indirection; tests monkeypatch this."""
    return secrets.token_hex(2)


def generate_proposal_id() -> str:
    return f"prop-{now().strftime('%Y%m%dT%H%M%SZ')}-{random_hex()}"


def file_proposal(
    table,
    pid: str,
    title: str,
    headline: str,
    citation: str,
    distance: str,
    kind: str,
    submit_body: dict | None = None,
) -> None:
    item = {
        "experiment_id": f"proposal#{pid}",
        "sk": PROPOSAL_SK,
        "pid": pid,
        "status": "PROPOSED",
        "title": title,
        "headline": headline,
        "citation": citation,
        "distance": distance,
        "kind": kind,
        "created_ts": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
    }
    if submit_body is not None:
        item["submit_body"] = json.dumps(submit_body)
    table.put_item(Item=item)


def get_proposal(table, pid: str) -> dict | None:
    response = table.get_item(
        Key={"experiment_id": f"proposal#{pid}", "sk": PROPOSAL_SK}
    )
    return response.get("Item")


def set_verdict(table, pid: str, verdict: str, source: str) -> None:
    try:
        table.update_item(
            Key={"experiment_id": f"proposal#{pid}", "sk": PROPOSAL_SK},
            UpdateExpression="SET #s = :v, verdict_ts = :ts, verdict_source = :src",
            ConditionExpression="#s = :proposed",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={
                ":v": verdict,
                ":proposed": "PROPOSED",
                ":ts": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                ":src": source,
            },
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise AlreadyDecided(pid) from exc
        raise


def list_recent(table, limit: int = 20) -> list[dict]:
    items = table.scan(FilterExpression=Attr("sk").eq(PROPOSAL_SK))["Items"]
    return sorted(items, key=lambda i: i["created_ts"], reverse=True)[:limit]


def count_created_today(table) -> int:
    today = now().strftime("%Y-%m-%d")
    items = table.scan(
        FilterExpression=Attr("sk").eq(PROPOSAL_SK)
        & Attr("created_ts").begins_with(today)
    )["Items"]
    return len(items)
