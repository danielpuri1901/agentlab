"""Paths to the data files that ship inside the worker images.

`docs/` lives at the repo root, not under src/agentlab/, so every path walks
up from this module (src/agentlab/repo_files.py -> src/agentlab -> src ->
repo root). Both Dockerfiles `COPY . .` the whole repo under /app, so the
same walk resolves inside the containers.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLASSICS_PATH = REPO_ROOT / "docs" / "classics.json"
INTERESTS_PATH = REPO_ROOT / "docs" / "interests.md"
GOLDEN_PAPERS_PATH = REPO_ROOT / "docs" / "golden-papers.jsonl"
