from datetime import UTC, datetime
from decimal import Decimal

from agentlab.aws_costs import month_to_date_tagged_cost


class FakeCostExplorer:
    def __init__(self):
        self.calls = []

    def get_cost_and_usage(self, **kwargs):
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            return {
                "ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "12.3401"}}}],
                "NextPageToken": "next",
            }
        return {"ResultsByTime": [{"Total": {"UnblendedCost": {"Amount": "0.0099"}}}]}


def test_month_to_date_tagged_cost_uses_exclusive_tomorrow_and_paginates():
    client = FakeCostExplorer()

    total = month_to_date_tagged_cost(
        client,
        project="agentlab",
        now=datetime(2026, 9, 16, 20, 0, tzinfo=UTC),
    )

    assert total == Decimal("12.3500")
    assert len(client.calls) == 2
    first = client.calls[0]
    assert first["TimePeriod"] == {"Start": "2026-09-01", "End": "2026-09-17"}
    assert first["Granularity"] == "MONTHLY"
    assert first["Metrics"] == ["UnblendedCost"]
    assert first["Filter"] == {
        "And": [
            {"Tags": {"Key": "project", "Values": ["agentlab"]}},
            {
                "Not": {
                    "Dimensions": {
                        "Key": "RECORD_TYPE",
                        "Values": ["Credit", "Refund"],
                    }
                }
            },
        ]
    }
    assert client.calls[1]["NextPageToken"] == "next"
