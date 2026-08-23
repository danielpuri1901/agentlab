"""Paper identity and the seen-papers store on the existing agentlab-state table.

One item per paper: PK `seen_paper#<identity>`, SK the literal "paper" (the
spec's DEDUP stage, docs/specs/2026-08-23-daily-paper-videos.md stage 2).
Identity resolution, in order: exact arXiv id extracted from any arxiv.org
URL form (abs/pdf/html, with or without a version suffix), else a normalized
title (lowercase, alphanumerics and single spaces only). Fuzzy title
matching (difflib.SequenceMatcher ratio >= 0.92) is a second pass in
is_seen, only for title-based identities and only against titles the caller
supplies (recent_seen_titles); it is not a replacement for the identity
lookup and never applies to arXiv identities, since those are already exact.
No embeddings; this is identity, not similarity, per the spec's explicit
scope cut.

New candidates are recorded as seen the moment they are picked (mark_seen),
not when fetched, so unpicked papers can resurface later.
"""

import re
from datetime import UTC, datetime
from difflib import SequenceMatcher

from boto3.dynamodb.conditions import Attr

SEEN_SK = "paper"
FUZZY_THRESHOLD = 0.92

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})")
_NON_ALNUM_SPACE_RE = re.compile(r"[^a-z0-9\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def now() -> datetime:
    """Module-level clock indirection; tests monkeypatch this."""
    return datetime.now(UTC)


def normalize_title(title: str) -> str:
    """Lowercase; drop everything but letters, digits, and spaces; collapse
    whitespace to single spaces; trim."""
    t = title.lower()
    t = _NON_ALNUM_SPACE_RE.sub("", t)
    t = _WHITESPACE_RE.sub(" ", t).strip()
    return t


def paper_identity(url: str, title: str) -> str:
    """arXiv id (from any arxiv.org URL form) prefixed `arxiv:`, version
    suffix stripped; else `title:` + normalized title."""
    match = _ARXIV_ID_RE.search(url or "")
    if "arxiv.org" in (url or "") and match:
        return f"arxiv:{match.group(1)}"
    return f"title:{normalize_title(title)}"


def is_seen(table, identity: str, fuzzy_titles: list[str] | None = None) -> bool:
    """Exact lookup first; if unseen and identity is title-based, fuzzy
    compare (ratio >= FUZZY_THRESHOLD) against caller-supplied fuzzy_titles
    (recent seen titles). arXiv identities are exact by construction and
    never fall back to fuzzy matching."""
    response = table.get_item(
        Key={"experiment_id": f"seen_paper#{identity}", "sk": SEEN_SK}
    )
    if "Item" in response:
        return True
    if not fuzzy_titles or not identity.startswith("title:"):
        return False
    target = identity[len("title:") :]
    for candidate in fuzzy_titles:
        ratio = SequenceMatcher(None, target, normalize_title(candidate)).ratio()
        if ratio >= FUZZY_THRESHOLD:
            return True
    return False


def mark_seen(
    table, identity: str, url: str, title: str, source: str, track: str
) -> None:
    table.put_item(
        Item={
            "experiment_id": f"seen_paper#{identity}",
            "sk": SEEN_SK,
            "url": url,
            "title": title,
            "source": source,
            "track": track,
            "first_seen": now().strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "picked": True,
        }
    )


def recent_seen_titles(table, limit: int = 200) -> list[str]:
    items = table.scan(FilterExpression=Attr("sk").eq(SEEN_SK))["Items"]
    items.sort(key=lambda i: i["first_seen"], reverse=True)
    return [i["title"] for i in items[:limit]]
