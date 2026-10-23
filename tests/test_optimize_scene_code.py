import json
import subprocess
from types import SimpleNamespace

import pytest

from agentlab import optimize_scene_code as opt

BEATS = [
    {
        "role": role,
        "narration": f"Narration {i}.",
        "visual": f"Visual {i}.",
        "on_screen_text": [],
    }
    for i, role in enumerate(
        ["title", "problem", "mechanism", "mechanism", "result", "question"], start=1
    )
]
BOARD = {
    "title": "A test lesson",
    "simple_definition": "A short definition.",
    "visual_focus": "One board that grows.",
    "mapping": [
        {"paper_term": "cache", "visual": "a box"},
        {"paper_term": "miss", "visual": "a red arrow"},
    ],
    "beats": BEATS,
}
TIMING = {"beats": [{"beat": i, "narration": 4.0} for i in range(1, 7)]}


def _example():
    return opt.Example(key="stories/a.json", storyboard=BOARD, durations=[4.0] * 6)


def _complete(source="print('scene')"):
    return lambda model, messages, **kwargs: f"```python\n{source}\n```"


def test_story_example_needs_one_positive_narration_length_per_beat():
    assert opt.story_example("k", {"storyboard": BOARD, "timing": TIMING}) is not None
    short = {"beats": TIMING["beats"][:5]}
    assert opt.story_example("k", {"storyboard": BOARD, "timing": short}) is None
    zero = {"beats": [{"narration": 0.0}] * 6}
    assert opt.story_example("k", {"storyboard": BOARD, "timing": zero}) is None
    assert (
        opt.story_example("k", {"storyboard": {"title": "x"}, "timing": TIMING}) is None
    )


def test_load_examples_takes_story_keys_from_the_ledger_not_a_bucket_listing():
    record = json.dumps({"storyboard": BOARD, "timing": TIMING}).encode()
    keys = [
        "stories/built-20261023T100000Z-a.json",
        "stories/built-20261022T100000Z-b.json",
        "stories/built-20261023T100000Z-a.json",
        "stories/gepa/20261023T100000Z/summary.json",
        "stories/built-20261021T100000Z-c.py",
    ]
    pages = [
        {"Items": [{"story_key": k} for k in keys[:2]], "LastEvaluatedKey": {"k": 1}},
        {"Items": [{"story_key": k} for k in keys[2:]] + [{"story_key": None}]},
    ]
    scans = []

    class FakeTable:
        def scan(self, **kwargs):
            scans.append(kwargs)
            return pages[len(scans) - 1]

    fetched = []

    class FakeS3:
        def get_object(self, Bucket, Key):
            fetched.append(Key)
            return {"Body": SimpleNamespace(read=lambda: record)}

    examples = opt.load_examples(FakeTable(), FakeS3(), "bucket", 2, seed=0)
    assert sorted(e.key for e in examples) == sorted(keys[:2])
    assert fetched[0] == keys[0]  # newest first, each key once
    assert scans[1]["ExclusiveStartKey"] == {"k": 1}


def test_guard_rejection_scores_zero_with_the_guard_text(tmp_path):
    rollout = opt.first_attempt(_example(), "system", _complete(), "m", tmp_path)
    assert rollout.score == opt.REJECTED
    assert rollout.feedback.startswith("The guard rejected the file:")


@pytest.mark.parametrize(
    ("render_result", "score", "starts"),
    [
        (
            subprocess.CalledProcessError(1, "manim", stderr="Traceback: boom"),
            opt.RENDER_FAILED,
            "Manim failed",
        ),
        (
            subprocess.TimeoutExpired("manim", 600),
            opt.RENDER_FAILED,
            "The render did not finish",
        ),
        (
            {"beats": [], "layout_warnings": ["beat 2 layout: 'x' runs off"] * 3},
            0.75,
            "The video rendered, but the layout",
        ),
        (
            {"beats": [], "layout_warnings": ["beat 2 layout: 'x' runs off"] * 6},
            opt.LAYOUT_FLOOR,
            "The video rendered, but the layout",
        ),
        ({"beats": [], "layout_warnings": []}, opt.SHIPS, "The first attempt rendered"),
    ],
)
def test_render_outcomes_score_by_what_production_does_next(
    tmp_path, monkeypatch, render_result, score, starts
):
    monkeypatch.setattr(opt, "check_scene_code", lambda source, beats: [])
    monkeypatch.setattr(opt, "_timing_error", lambda timing, durations: None)
    monkeypatch.setattr(opt, "_stderr_tail", lambda exc: str(exc.stderr))

    def render(scene_file, spec, timeout):
        assert spec["durations"] == [4.0] * 6
        assert spec["subtitles"][0] == "Narration 1."
        if isinstance(render_result, Exception):
            raise render_result
        return scene_file, render_result

    rollout = opt.first_attempt(
        _example(), "system", _complete(), "m", tmp_path, render=render
    )
    assert rollout.score == score
    assert rollout.feedback.startswith(starts)


def test_invalid_timing_scores_as_a_failed_render(tmp_path, monkeypatch):
    monkeypatch.setattr(opt, "check_scene_code", lambda source, beats: [])
    monkeypatch.setattr(
        opt, "_timing_error", lambda timing, durations: "beat 3 ends late"
    )
    render = lambda scene_file, spec, timeout: (scene_file, {"beats": []})
    rollout = opt.first_attempt(
        _example(), "system", _complete(), "m", tmp_path, render=render
    )
    assert rollout.score == opt.RENDER_FAILED
    assert "beat 3 ends late" in rollout.feedback


def test_a_failed_coder_call_scores_zero_but_a_budget_stop_propagates(tmp_path):
    def broken(model, messages, **kwargs):
        raise TimeoutError("slow")

    rollout = opt.first_attempt(_example(), "system", broken, "m", tmp_path)
    assert (
        rollout.score == opt.REJECTED and "scene coder call failed" in rollout.feedback
    )

    def over_budget(model, messages, **kwargs):
        raise opt.BudgetExceeded("cap")

    with pytest.raises(opt.BudgetExceeded):
        opt.first_attempt(_example(), "system", over_budget, "m", tmp_path)


def test_budget_stops_gepa_at_the_cap_and_raises_past_the_hard_cap():
    usage = []
    budget = opt.Budget(10.0, usage)
    guarded = budget.guard(lambda model, messages, **kw: "ok")
    usage.append(SimpleNamespace(estimated_cost_usd=9.0))
    assert budget(None) is False and guarded("m", []) == "ok"
    usage.append(SimpleNamespace(estimated_cost_usd=1.5))
    assert budget(None) is True and guarded("m", []) == "ok"
    usage.append(SimpleNamespace(estimated_cost_usd=2.0))
    with pytest.raises(opt.BudgetExceeded):
        guarded("m", [])


def test_reflective_dataset_carries_the_file_and_the_feedback(tmp_path):
    gepa = pytest.importorskip("gepa")
    calls = []

    def attempt(example, prompt, complete, model, work_dir):
        calls.append(prompt)
        return opt.Rollout(
            opt.LAYOUT_FLOOR, "beat 4 layout: off the bottom edge", "src" * 3000
        )

    adapter = opt.SceneCodeAdapter(
        _complete(), "m", tmp_path, workers=2, attempt=attempt
    )
    batch = adapter.evaluate(
        [_example(), _example()], {opt.COMPONENT: "P"}, capture_traces=True
    )
    assert isinstance(batch, gepa.EvaluationBatch)
    assert batch.scores == [opt.LAYOUT_FLOOR] * 2 and calls == ["P", "P"]
    rows = adapter.make_reflective_dataset({opt.COMPONENT: "P"}, batch, [opt.COMPONENT])
    row = rows[opt.COMPONENT][0]
    assert len(row["Generated Outputs"]) == opt.SOURCE_EXCERPT_CHARS
    assert "off the bottom edge" in row["Feedback"] and "6 beats" in row["Inputs"]
    assert list(tmp_path.iterdir()) == []  # every rollout folder is removed


def test_telegram_summary_names_the_baseline_and_the_result():
    text = opt.telegram_summary(
        {
            "run_id": "r",
            "val": ["a", "b"],
            "baseline_model": "anthropic/claude-opus-4-6",
            "baseline_val_score": 0.3,
            "baseline_clean_first_tries": 0,
            "scene_model": "anthropic/claude-opus-5-5",
            "seed_val_score": 0.4,
            "best_val_score": 0.7,
            "seed_clean_first_tries": 0,
            "best_clean_first_tries": 1,
            "candidates": 3,
            "metric_calls": 40,
            "estimated_cost_usd": 12.5,
            "results": "s3://b/p/",
        }
    )
    assert "opus-4-6: score 0.30" in text and "0.40 -> 0.70" in text and "1/2" in text
    assert "Nothing changed in production" in text
