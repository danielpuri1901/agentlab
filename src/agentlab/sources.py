"""Fresh external sources for the proposer: GitHub releases, arXiv, HN, HF.

Each fetcher normalizes to small dicts and fails soft: a non-200,
a malformed payload, or a network error yields fewer sources, never an
exception out of gather_exploit()/gather_explore(). The proposer treats an
empty list as "no fresh sources today" and says so instead of inventing work
(anti-collapse rule: fresh-external-source anchoring).

Two pools:
- exploit: the keyword-filtered GitHub/arXiv/HN sweep (agent/eval/harness
  territory Daniel already tracks). Every dict is tagged "pool": "exploit".
- explore: high-signal picks with no keyword filter, meant to surface
  things outside the tracked keyword set - HN front-page hits with
  points >= 80, and the Hugging Face daily papers list. Every dict is
  tagged "pool": "explore".
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


def _hn_front_hits(client) -> list[dict]:
    """One shared Algolia front-page fetch; both HN fetchers filter it locally
    so a run that builds both pools hits the endpoint once per fetcher call,
    not with duplicated request logic."""
    try:
        response = client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"tags": "front_page", "hitsPerPage": 30},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        return response.json().get("hits", [])
    except Exception:  # noqa: BLE001
        return []


def _hn_item(hit: dict) -> dict:
    return {
        "source": "hn",
        "title": hit.get("title") or "",
        "url": hit.get("url")
        or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
        "points": hit.get("points") or 0,
    }


def fetch_hn_front(client) -> list[dict]:
    return [
        _hn_item(hit)
        for hit in _hn_front_hits(client)
        if any(k in (hit.get("title") or "").lower() for k in KEYWORDS)
    ]


def fetch_hn_explore(client, min_points: int = 80) -> list[dict]:
    """HN front page for the explore pool: no keyword filter, high points only."""
    return [
        _hn_item(hit)
        for hit in _hn_front_hits(client)
        if (hit.get("points") or 0) >= min_points
    ]


def fetch_hf_daily(client) -> list[dict]:
    """Hugging Face daily papers list.

    Observed live shape (checked 2026-08-23): a bare JSON list of objects,
    each shaped like {"paper": {"id", "title", "upvotes", ...}, "title",
    "publishedAt", ...}. The fields we need - id, title, upvotes - live
    under the nested "paper" object.
    """
    try:
        response = client.get("https://huggingface.co/api/daily_papers", timeout=30)
        if response.status_code != 200:
            return []
        items = response.json()
    except Exception:  # noqa: BLE001
        return []
    out = []
    for item in items:
        paper = item.get("paper") or {}
        paper_id = paper.get("id") or ""
        if not paper_id:
            continue
        out.append(
            {
                "source": "hf",
                "title": paper.get("title") or item.get("title") or "",
                "url": f"https://arxiv.org/abs/{paper_id}",
                "upvotes": paper.get("upvotes") or 0,
            }
        )
    return out


def gather_exploit(client=None) -> list[dict]:
    client = client or httpx.Client()
    items = fetch_github_releases(client) + fetch_arxiv(client) + fetch_hn_front(client)
    for item in items:
        item["pool"] = "exploit"
    return items


# The proposer imports `gather`; keep it as a working alias for gather_exploit.
gather = gather_exploit


def gather_explore(client=None) -> list[dict]:
    client = client or httpx.Client()
    items = fetch_hn_explore(client) + fetch_hf_daily(client)
    for item in items:
        item["pool"] = "explore"
    return items
