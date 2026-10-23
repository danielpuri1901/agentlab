import json
from types import SimpleNamespace

from agentlab import scene_review
from agentlab.scene_review import Verdict, review


def _boards(n):
    return [
        SimpleNamespace(beats=[SimpleNamespace(narration=f"Chapter {k} beat.", on_screen_text=["label"])])
        for k in range(1, n + 1)
    ]


def test_review_sends_sheets_and_text_without_code_and_parses_fix():
    seen = {}

    def complete(model, messages, **kwargs):
        seen["model"], seen["messages"], seen["kwargs"] = model, messages, kwargs
        return json.dumps({"verdict": "fix", "chapter": 2, "problem": "text overlaps the axis"})

    verdict = review([b"png1", b"png2"], _boards(2), complete, "anthropic/claude-haiku-4-5")
    assert verdict == Verdict(verdict="fix", chapter=2, problem="text overlaps the axis")
    content = seen["messages"][1]["content"]
    images = [c for c in content if c.get("type") == "image_url"]
    assert len(images) == 2 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    texts = " ".join(c["text"] for c in content if c.get("type") == "text")
    assert "Chapter 2 beat." in texts and "def beat_" not in texts
    assert seen["kwargs"]["extra_fields"]["output_config"]["format"]["schema"] == scene_review.VERDICT_SCHEMA
    assert seen["kwargs"]["max_tokens"] == 300 and seen["kwargs"]["timeout"] == 60


def test_review_rejects_invalid_chapter_as_ship():
    for chapter in (0, 3, None):
        verdict = review([b"a", b"b"], _boards(2), lambda *a, chapter=chapter, **k: json.dumps({"verdict": "fix", "chapter": chapter, "problem": "x"}), "m")
        assert verdict.verdict == "ship"


def test_review_failure_counts_as_ship():
    def boom(*args, **kwargs):
        raise TimeoutError("slow")

    verdict = review([b"a"], _boards(1), boom, "m")
    assert verdict.verdict == "ship" and "TimeoutError" in verdict.problem
    verdict = review([b"a"], _boards(1), lambda *a, **k: "not json", "m")
    assert verdict.verdict == "ship"
    verdict = review([b"a", b"b"], _boards(1), boom, "m")
    assert verdict.verdict == "ship" and "ValueError" in verdict.problem


def test_review_schema_forbids_extra_keys():
    assert scene_review.VERDICT_SCHEMA["additionalProperties"] is False
    assert set(scene_review.VERDICT_SCHEMA["required"]) == {"verdict", "chapter", "problem"}
