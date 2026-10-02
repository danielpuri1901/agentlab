"""Build docs/golden-papers.jsonl from Daniel's labeling sheet.

The sheet (docs/golden-papers-labeling.md) is hand-edited markdown: a
numbered title line, then a `Label: <word>. Why: <sentence>` line. Labels
map through agentlab.episodes.canonical_rating, so "yes", "Yeah,",
"IMPLEMENT", and "seems cool.implement" all become COOL, "learned" becomes
MEH, and "no" becomes SKIP. A URL is attached when the title names an arXiv
id or fuzzy-matches an entry in docs/classics.json; otherwise it is null and
the paper is identified by title. Run from the repo root:

    uv run python scripts/build_golden_papers.py

Commit the jsonl; both worker images ship it.
"""

from __future__ import annotations

import json
import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agentlab.episodes import canonical_rating

REPO_ROOT = Path(__file__).resolve().parents[1]
SHEET_PATH = REPO_ROOT / "docs" / "golden-papers-labeling.md"
CLASSICS_PATH = REPO_ROOT / "docs" / "classics.json"
OUT_PATH = REPO_ROOT / "docs" / "golden-papers.jsonl"

ENTRY_RE = re.compile(r"^\s*(\d+)\.\s+(.+?)\s*$")
LABEL_RE = re.compile(r"^\s*Label:\s*(?P<label>.*?)\s*(?:Why:\s*(?P<why>.*))?$")
ARXIV_RE = re.compile(r"arXiv\s+(\d{4}\.\d{4,5})", re.IGNORECASE)
TRAILING_PAREN_RE = re.compile(r"\s*\([^()]*\)\s*$")
TITLE_MATCH = 0.86


def parse_sheet(text: str) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in text.splitlines():
        if line.lstrip().startswith("Label:"):
            match = LABEL_RE.match(line)
            if match and current is not None:
                current["label"] = (match.group("label") or "").strip()
                current["why"] = (match.group("why") or "").strip()
            continue
        match = ENTRY_RE.match(line)
        if match:
            current = {
                "number": int(match.group(1)),
                "raw_title": match.group(2).strip(),
                "label": "",
                "why": "",
            }
            entries.append(current)
    return entries


def clean_title(raw_title: str) -> str:
    return TRAILING_PAREN_RE.sub("", raw_title).strip()


def arxiv_url(raw_title: str) -> str | None:
    match = ARXIV_RE.search(raw_title)
    return f"https://arxiv.org/abs/{match.group(1)}" if match else None


def _plain(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def match_classic(title: str, classics: list[dict]) -> str | None:
    best_ratio, best_url = 0.0, None
    for entry in classics:
        ratio = SequenceMatcher(None, _plain(title), _plain(entry["title"])).ratio()
        if ratio > best_ratio:
            best_ratio, best_url = ratio, entry["url"]
    return best_url if best_ratio >= TITLE_MATCH else None


def build(entries: list[dict], classics: list[dict]) -> tuple[list[dict], list[str]]:
    records: list[dict] = []
    warnings: list[str] = []
    for entry in entries:
        title = clean_title(entry["raw_title"])
        rating = canonical_rating(entry["label"])
        if not title or title.strip("_") == "" or rating is None:
            warnings.append(f"entry {entry['number']}: label {entry['label']!r} not mapped, skipped")
            continue
        records.append(
            {
                "title": title,
                "url": arxiv_url(entry["raw_title"]) or match_classic(title, classics),
                "rating": rating,
                "why": entry["why"],
            }
        )
    return records, warnings


def main() -> None:
    entries = parse_sheet(SHEET_PATH.read_text(encoding="utf-8"))
    classics = json.loads(CLASSICS_PATH.read_text(encoding="utf-8"))
    records, warnings = build(entries, classics)
    OUT_PATH.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    for warning in warnings:
        print(warning, file=sys.stderr)
    print(f"wrote {len(records)} golden papers to {OUT_PATH}")


if __name__ == "__main__":
    main()
