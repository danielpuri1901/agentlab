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

    examples = opt.load_examples(
        FakeTable(), FakeS3(), "bucket", 2, seed=0, unit="video"
    )
    assert sorted(e.key for e in examples) == sorted(keys[:2])
    assert fetched[0] == keys[0]  # newest first, each key once
    assert scans[1]["ExclusiveStartKey"] == {"k": 1}


def test_chapter_examples_split_the_storyboard_like_the_lanes():
    parts = opt.chapter_examples(_example())
    assert [part.beats for part in parts] == [(0, 1), (2, 3), (4,), (5,)]
    assert parts[0].durations == [4.0, 4.0] and parts[0].note["previous_visual"] is None
    assert parts[1].note == {
        "index": 2,
        "count": 4,
        "first_beat": 3,
        "last_beat": 4,
        "previous_visual": "Visual 2.",
    }


def test_a_chapter_rollout_prompts_checks_and_renders_only_its_beats(
    tmp_path, monkeypatch
):
    chapter = opt.chapter_examples(_example())[1]
    seen = {}

    def check(source, beats):
        seen["beats"] = beats
        return []

    def complete(model, messages, **kwargs):
        seen["prompt"] = messages[1]["content"]
        return "```python\nprint(1)\n```"

    def render(scene_file, spec, timeout):
        seen["spec"] = spec
        return scene_file, {"beats": [], "layout_warnings": []}

    monkeypatch.setattr(opt, "check_scene_code", check)
    monkeypatch.setattr(opt, "_timing_error", lambda timing, durations: None)
    rollout = opt.first_attempt(
        chapter, "system", complete, "m", tmp_path, render=render
    )
    assert rollout.score == opt.SHIPS
    assert "<chapter>" in seen["prompt"] and seen["beats"] == 2
    assert seen["spec"]["subtitles"] == ["Narration 3.", "Narration 4."]
    # story_scene's base class reads spec["storyboard"]; production's lane spec has it.
    assert [b["narration"] for b in seen["spec"]["storyboard"]["beats"]] == [
        "Narration 3.",
        "Narration 4.",
    ]
    assert seen["spec"]["durations"] == [4.0, 4.0]


def test_a_token_limit_failure_says_the_prompt_does_not_count(tmp_path):
    def capped(model, messages, **kwargs):
        raise ValueError(
            "model output hit the token limit; return a shorter complete response"
        )

    rollout = opt.first_attempt(_example(), "system", capped, "m", tmp_path)
    assert rollout.score == opt.REJECTED
    assert "never this system prompt" in rollout.feedback


def test_split_by_story_keeps_every_storyboard_on_one_side():
    examples = [
        opt.Example(key=f"stories/{s}.json#c{c}", storyboard=BOARD, durations=[4.0])
        for s in "abcd"
        for c in (1, 2, 3)
    ]
    train, val = opt.split_by_story(examples, val_count=4)
    stories = lambda part: {e.key.split("#")[0] for e in part}
    assert len(val) == 6 and len(train) == 6
    assert not stories(train) & stories(val)


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
    assert (
        "off the bottom edge" in row["Feedback"]
        and "<storyboard_json>" in row["Inputs"]
    )
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


def test_compare_scores_both_prompts_on_the_same_unseen_examples(tmp_path, monkeypatch):
    pytest.importorskip("gepa")
    record = json.dumps({"storyboard": BOARD, "timing": TIMING}).encode()
    keys = [
        "stories/built-20261023T100000Z-a.json",
        "stories/built-20261022T100000Z-b.json",
    ]
    puts = {}

    class FakeTable:
        def scan(self, **kwargs):
            return {"Items": [{"story_key": k} for k in keys]}

    class FakeS3:
        def get_object(self, Bucket, Key):
            return {"Body": SimpleNamespace(read=lambda: record)}

        def put_object(self, Bucket, Key, Body):
            puts[Key] = Body

    seen = []

    def fake_run(self, example, prompt):
        seen.append((example.key, prompt == "CANDIDATE"))
        if prompt != "CANDIDATE":
            return opt.Rollout(0.75, "layout", "src")
        return opt.Rollout(0.5 if example.key.endswith("#c4") else 1.0, "clean", "src")

    monkeypatch.setattr(opt.SceneCodeAdapter, "_run", fake_run)
    summary = opt.compare(
        FakeTable(),
        FakeS3(),
        "bucket",
        lambda *a, **k: "",
        [],
        scene_model="m",
        candidate_prompt="CANDIDATE",
        val_count=4,
        budget_usd=5.0,
        workers=2,
        seed=0,
        work_root=tmp_path,
        exclude_stories=frozenset({keys[0]}),
    )
    assert all(not k.startswith(keys[0]) for k in summary["examples"])  # excluded story
    assert sorted(k for k, cand in seen if cand) == sorted(
        k for k, cand in seen if not cand
    )
    assert summary["today_score"] == 0.75 and summary["candidate_clean"] == 3
    assert (summary["candidate_better"], summary["candidate_worse"]) == (3, 1)
    assert any(k.endswith("candidate_prompt.txt") for k in puts)
    assert "worse on 1" in opt.compare_summary(summary)
