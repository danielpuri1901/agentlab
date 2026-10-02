"""The golden sheet converter: Daniel's markdown labels become the jsonl the
consolidator and the probe eval read."""

import importlib.util
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

_SCRIPT = Path(__file__).parent.parent / "scripts" / "build_golden_papers.py"
spec = importlib.util.spec_from_file_location("build_golden_papers", _SCRIPT)
build_golden_papers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_golden_papers)

SHEET = """# Golden paper set: labeling sheet

Intro text with no numbers.

## Pre-filled

1. Recursive Self-Improvement in AI (the RSI survey, arXiv 2607.07663)
   Label: IMPLEMENT. Why: adopted as the lab's design rubric.
2. Eureka-style meta-agent orchestration (arXiv 2608.19047)
   Label: LEARNED. Why: interesting frame, nothing implementable.

## For you to label

6. Attention Is All You Need (the Transformer paper)
   Label: yes. Why: The GOAT paper.
7. Chain-of-Thought Prompting Elicits Reasoning in Large Language Models
   Label: Yes. . Why: Very important.
8. Constitutional AI: Harmlessness from AI Feedback
   Label: no. Why: Doesn't really have a catchy title.
9. Something Unlabelled
   Label: ___. Why: ___
10. STaR: Self-Taught Reasoner (bootstrapping reasoning with reasoning)
    Label: Yeah,  . Why: GOAT thing.

## Your own additions

26. The Curse of Recursion: Training on Generated Data Makes Models Forget
    Label: seems cool.implement
27. ___
    Label: ___. Why: ___
"""

CLASSICS = [
    {"title": "Attention Is All You Need", "url": "https://arxiv.org/abs/1706.03762", "year": 2017},
    {"title": "Chain-of-Thought Prompting Elicits Reasoning in Large Language Models", "url": "https://arxiv.org/abs/2201.11903", "year": 2022},
    {"title": "Constitutional AI: Harmlessness from AI Feedback", "url": "https://arxiv.org/abs/2212.08073", "year": 2022},
    {"title": "The Curse of Recursion: Training on Generated Data Makes Models Forget", "url": "https://arxiv.org/abs/2305.17493", "year": 2023},
]


def test_parse_sheet_reads_titles_labels_and_whys():
    entries = build_golden_papers.parse_sheet(SHEET)
    assert [e["number"] for e in entries] == [1, 2, 6, 7, 8, 9, 10, 26, 27]
    assert entries[2]["raw_title"] == "Attention Is All You Need (the Transformer paper)"
    assert entries[2]["label"] == "yes."
    assert entries[2]["why"] == "The GOAT paper."
    assert entries[7]["label"] == "seems cool.implement"
    assert entries[7]["why"] == ""


def test_build_maps_labels_attaches_urls_and_warns():
    records, warnings = build_golden_papers.build(build_golden_papers.parse_sheet(SHEET), CLASSICS)
    by_title = {r["title"]: r for r in records}
    assert by_title["Recursive Self-Improvement in AI"] == {
        "title": "Recursive Self-Improvement in AI",
        "url": "https://arxiv.org/abs/2607.07663",
        "rating": "COOL",
        "why": "adopted as the lab's design rubric.",
    }
    assert by_title["Eureka-style meta-agent orchestration"]["rating"] == "MEH"
    assert by_title["Attention Is All You Need"]["url"] == "https://arxiv.org/abs/1706.03762"
    assert by_title["Attention Is All You Need"]["rating"] == "COOL"
    assert by_title["Chain-of-Thought Prompting Elicits Reasoning in Large Language Models"]["rating"] == "COOL"
    assert by_title["Constitutional AI: Harmlessness from AI Feedback"]["rating"] == "SKIP"
    assert by_title["STaR: Self-Taught Reasoner"]["rating"] == "COOL"
    assert by_title["STaR: Self-Taught Reasoner"]["url"] is None
    assert by_title["The Curse of Recursion: Training on Generated Data Makes Models Forget"]["rating"] == "COOL"
    assert "Something Unlabelled" not in by_title
    assert len(records) == 7
    assert any("entry 9" in w for w in warnings)
    assert any("entry 27" in w for w in warnings)


def test_main_writes_jsonl(tmp_path, monkeypatch):
    sheet = tmp_path / "sheet.md"
    sheet.write_text(SHEET, encoding="utf-8")
    classics = tmp_path / "classics.json"
    classics.write_text(json.dumps(CLASSICS), encoding="utf-8")
    out = tmp_path / "golden.jsonl"
    monkeypatch.setattr(build_golden_papers, "SHEET_PATH", sheet)
    monkeypatch.setattr(build_golden_papers, "CLASSICS_PATH", classics)
    monkeypatch.setattr(build_golden_papers, "OUT_PATH", out)

    build_golden_papers.main()

    lines = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 7
    assert {"title", "url", "rating", "why"} == set(lines[0])


def test_repo_sheet_builds_without_warnings():
    """The committed sheet must convert cleanly, so the image ships every label."""
    records, warnings = build_golden_papers.build(
        build_golden_papers.parse_sheet(build_golden_papers.SHEET_PATH.read_text(encoding="utf-8")),
        json.loads(build_golden_papers.CLASSICS_PATH.read_text(encoding="utf-8")),
    )
    assert warnings == []
    assert len(records) == 26
    assert sum(1 for r in records if r["rating"] == "SKIP") == 1
    committed = [json.loads(line) for line in build_golden_papers.OUT_PATH.read_text(encoding="utf-8").splitlines()]
    assert committed == records
