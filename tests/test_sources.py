"""Tests for the proposer's external sources fetcher."""

from agentlab.sources import fetch_arxiv, fetch_github_releases, fetch_hn_front, gather


class FakeResponse:
    """Canned HTTP response for testing."""

    def __init__(self, status_code: int, json_data=None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("No JSON data")
        return self._json_data


class FakeClient:
    """Fake HTTP client keyed by URL substring."""

    def __init__(self, responses: dict):
        """responses: dict mapping URL substring -> FakeResponse or callable that raises."""
        self.responses = responses

    def get(self, url: str, **kwargs):
        for key, response in self.responses.items():
            if key in url:
                if callable(response) and not isinstance(response, FakeResponse):
                    response()  # raises
                    return None
                return response
        # Default: not found
        return FakeResponse(404)


def test_github_releases_normalized():
    """Test GitHub releases fetcher with mixed success/failure."""
    fake_client = FakeClient(
        {
            "inspect_ai": FakeResponse(
                200,
                json_data=[
                    {
                        "name": "v1.0.0",
                        "tag_name": "v1.0.0",
                        "html_url": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v1.0.0",
                        "published_at": "2026-01-01T00:00:00Z",
                        "body": "Major release with agent eval improvements",
                    },
                    {
                        "name": "v0.9.0",
                        "tag_name": "v0.9.0",
                        "html_url": "https://github.com/UKGovernmentBEIS/inspect_ai/releases/tag/v0.9.0",
                        "published_at": "2025-12-01T00:00:00Z",
                        "body": "Minor bug fixes",
                    },
                ],
            ),
            "langgraph": FakeResponse(404),  # This repo should be silently skipped
        }
    )

    repos = [
        "UKGovernmentBEIS/inspect_ai",
        "langchain-ai/langgraph",
    ]
    result = fetch_github_releases(fake_client, repos=repos)

    # Should have exactly 2 releases from the successful repo, langgraph skipped
    assert len(result) == 2
    assert all(r["source"] == "github" for r in result)
    assert result[0]["repo"] == "UKGovernmentBEIS/inspect_ai"
    assert result[0]["title"] == "v1.0.0"
    assert "v1.0.0" in result[0]["url"]
    assert result[1]["title"] == "v0.9.0"


def test_arxiv_filters_by_keyword():
    """Test arXiv fetcher filters by keywords and collapses whitespace."""
    arxiv_xml = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Agent  Evaluation
      Framework for Large Language Models</title>
    <summary>This paper presents a comprehensive framework for evaluating agent evaluation systems using LLMs.  Multiple testing approaches are explored.</summary>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2401.00002v1</id>
    <title>Astrophysics Today: New Telescope Discoveries</title>
    <summary>Recent observations from advanced telescopes reveal surprising patterns in distant galaxies.</summary>
  </entry>
</feed>"""

    fake_client = FakeClient(
        {
            "export.arxiv.org": FakeResponse(200, text=arxiv_xml),
        }
    )

    result = fetch_arxiv(fake_client)

    # Should only match the first entry (contains "agent" and "evaluation")
    assert len(result) == 1
    assert result[0]["source"] == "arxiv"
    # Whitespace should be collapsed (double spaces and newline removed)
    assert result[0]["title"] == "Agent Evaluation Framework for Large Language Models"
    assert result[0]["url"] == "http://arxiv.org/abs/2401.00001v1"


def test_hn_front_page_filter():
    """Test HN front page fetcher filters by keywords and exercises url fallback."""
    hn_json = {
        "hits": [
            {
                "title": "New agent harness released for production",
                "url": None,  # url is null, should use objectID fallback
                "objectID": "41000001",
                "points": 250,
            },
            {
                "title": "Show HN: my cat's diary",
                "url": "https://example.com/cats",
                "objectID": "67890",
                "points": 150,
            },
        ]
    }

    fake_client = FakeClient(
        {
            "hn.algolia.com": FakeResponse(200, json_data=hn_json),
        }
    )

    result = fetch_hn_front(fake_client)

    # Should only match the first entry (contains "agent")
    assert len(result) == 1
    assert result[0]["source"] == "hn"
    assert result[0]["title"] == "New agent harness released for production"
    # Should use fallback to news.ycombinator.com when url is null
    assert result[0]["url"] == "https://news.ycombinator.com/item?id=41000001"
    assert result[0]["points"] == 250


class NetworkError(Exception):
    """Fake network error for testing."""


def test_gather_survives_total_network_failure():
    """Test gather returns empty list on complete network failure."""

    def raise_exception(*args, **kwargs):
        raise NetworkError("Network error")

    fake_client = FakeClient(
        {
            "api.github.com": raise_exception,
            "export.arxiv.org": raise_exception,
            "hn.algolia.com": raise_exception,
        }
    )

    result = gather(client=fake_client)

    # Should return empty list, not raise
    assert result == []
