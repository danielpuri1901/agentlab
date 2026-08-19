"""Fresh external sources for the proposer: GitHub releases, arXiv, HN.

Each fetcher normalizes to small dicts and fails soft: a non-200,
a malformed payload, or a network error yields fewer sources, never an
exception out of gather(). The proposer treats an empty list as "no fresh
sources today" and says so instead of inventing work (anti-collapse rule:
fresh-external-source anchoring).
"""

import xml.etree.ElementTree as ET

import httpx

TRACKED_REPOS = [
    # verified in Task 4 Step 1; the DeepSeek harness slug comes from the
    # Tournament 001 entrant citation
    "UKGovernmentBEIS/inspect_ai",
    "langchain-ai/langgraph",
    "anthropics/claude-agent-sdk-python",
    "strands-agents/harness-sdk",
    "deepseek-ai/deepseek-harness",
]
KEYWORDS = (
    "agent",
    "eval",
    "harness",
    "compaction",
    "context",
    "memory",
    "benchmark",
    "tool use",
    "llm",
)


def fetch_github_releases(client, repos=None) -> list[dict]:
    out = []
    for repo in repos if repos is not None else TRACKED_REPOS:
        try:
            response = client.get(
                f"https://api.github.com/repos/{repo}/releases",
                params={"per_page": 3},
                headers={"Accept": "application/vnd.github+json"},
                timeout=30,
            )
            if response.status_code != 200:
                continue
            releases = response.json()
        except Exception:  # noqa: BLE001, S112 - one dead repo must not sink the run
            continue
        for release in releases:
            out.append(
                {
                    "source": "github",
                    "repo": repo,
                    "title": release.get("name") or release.get("tag_name") or "",
                    "url": release.get("html_url") or "",
                    "published": release.get("published_at") or "",
                    "notes": (release.get("body") or "")[:500],
                }
            )
    return out


def fetch_arxiv(client, max_results: int = 25) -> list[dict]:
    try:
        response = client.get(
            "https://export.arxiv.org/api/query",
            params={
                "search_query": "cat:cs.CL OR cat:cs.AI",
                "sortBy": "submittedDate",
                "sortOrder": "descending",
                "max_results": max_results,
            },
            timeout=30,
        )
        if response.status_code != 200:
            return []
        root = ET.fromstring(response.text)
    except Exception:  # noqa: BLE001
        return []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entries = []
    for entry in root.findall("a:entry", ns):
        title = " ".join((entry.findtext("a:title", "", ns) or "").split())
        summary = " ".join((entry.findtext("a:summary", "", ns) or "").split())
        url = entry.findtext("a:id", "", ns) or ""
        if any(k in f"{title} {summary}".lower() for k in KEYWORDS):
            entries.append(
                {
                    "source": "arxiv",
                    "title": title,
                    "url": url,
                    "summary": summary[:400],
                }
            )
    return entries


def fetch_hn_front(client) -> list[dict]:
    try:
        response = client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": 30},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        hits = response.json().get("hits", [])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for hit in hits:
        title = hit.get("title") or ""
        if any(k in title.lower() for k in KEYWORDS):
            out.append(
                {
                    "source": "hn",
                    "title": title,
                    "url": hit.get("url")
                    or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "points": hit.get("points") or 0,
                }
            )
    return out


def gather(client=None) -> list[dict]:
    client = client or httpx.Client()
    return fetch_github_releases(client) + fetch_arxiv(client) + fetch_hn_front(client)
