"""Small, deterministic Cost Explorer queries used by the video worker."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal


def month_to_date_tagged_cost(
    ce_client,
    project: str,
    now: datetime | None = None,
) -> Decimal:
    current = (now or datetime.now(UTC)).astimezone(UTC)
    start = current.date().replace(day=1).isoformat()
    end = (current.date() + timedelta(days=1)).isoformat()
    request = {
        "TimePeriod": {"Start": start, "End": end},
        "Granularity": "MONTHLY",
        "Metrics": ["UnblendedCost"],
        "Filter": {
            "And": [
                {"Tags": {"Key": "project", "Values": [project]}},
                {
                    "Not": {
                        "Dimensions": {
                            "Key": "RECORD_TYPE",
                            "Values": ["Credit", "Refund"],
                        }
                    }
                },
            ]
        },
    }

    total = Decimal(0)
    while True:
        response = ce_client.get_cost_and_usage(**request)
        for period in response.get("ResultsByTime", []):
            amount = period.get("Total", {}).get("UnblendedCost", {}).get("Amount")
            if amount is not None:
                total += Decimal(amount)
        token = response.get("NextPageToken")
        if not token:
            return total
        request["NextPageToken"] = token
