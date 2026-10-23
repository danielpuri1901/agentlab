"""Improve the scene-code system prompt with GEPA, scored on first attempts.

Runs as `worker optimize-scene-code` in the video image, which has manim,
TeX and the Anthropic key. On 2026-10-23 only 3 of 218 stored stories had
rendered cleanly on the first attempt: 91 needed a layout round, 38 were
rejected by the guard, and about 47 hit a manim traceback. Every extra
attempt is a paid fix round and minutes of render time.

One rollout is one first attempt in production's own terms: the candidate
system prompt with production's user prompt (build_scene_code_prompt), then
check_scene_code, one render, the timing check and the layout audit, all
imported from scene_code.py and story_video.py so the score cannot drift from
what the video job does. A stored story supplies the storyboard and the beat
durations its narration had, so no storyboard or Polly call is made.

Score per rollout, by what production would have to do next:
    1.0   ships as is: rendered, valid timing, clean layout
    0.6   rendered with layout warnings: production spends a layout round
    0.25  passed the guard, then the render or its timing failed
    0.0   the guard rejected the file, or the coder call failed
The feedback is the text production feeds its fix round, so GEPA's
reflection model reads exactly why a first attempt failed.

Results go to checkpoints/gepa-scene-code/<run>/ in the results bucket (the
explain role may write checkpoints/*): the seed and best prompts, a summary
with validation scores, and GEPA's run directory. The live prompt never
changes here; adopting a result is a reviewed PR against SCENE_CODE_SYSTEM.
"""

from __future__ import annotations

import json
import logging
import random
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from agentlab.scene_code import (
    SCENE_CODE_SYSTEM,
    build_scene_code_prompt,
    check_scene_code,
    extract_python_block,
)
from agentlab.story_video import (
    RENDER_TIMEOUT,
    _failure_detail,
    _render,
    _stderr_tail,
    _timing_error,
    _TimingInvalid,
    _write_attempt,
)
from agentlab.storyboard import Storyboard

logger = logging.getLogger(__name__)

COMPONENT = "scene_code_system"
STORIES_PREFIX = "stories/"
RESULTS_PREFIX = "checkpoints/gepa-scene-code/"
SHIPS = 1.0
LAYOUT_ROUND = 0.6
RENDER_FAILED = 0.25
REJECTED = 0.0
SOURCE_EXCERPT_CHARS = 4000
FEEDBACK_CHARS = 2500
# A call that would start past this multiple of the cap raises, so a long
# validation pass cannot run far past the budget the stopper checks.
HARD_CAP_FACTOR = 1.25
_STAMP_RE = re.compile(r"(\d{8}T\d{6}Z)")


@dataclass(frozen=True)
class Example:
    key: str
    storyboard: dict
    durations: list[float]


@dataclass(frozen=True)
class Rollout:
    score: float
    feedback: str
    source: str


class BudgetExceeded(RuntimeError):
    pass


def story_example(key: str, record: dict) -> Example | None:
    """A stored story as an eval example, or None when it lacks a valid
    storyboard or one positive narration length per beat."""
    board = record.get("storyboard")
    timing_beats = (record.get("timing") or {}).get("beats") or []
    if not isinstance(board, dict):
        return None
    try:
        storyboard = Storyboard.model_validate(board)
        durations = [float(beat["narration"]) for beat in timing_beats]
    except (KeyError, TypeError, ValueError):
        return None
    if len(durations) != len(storyboard.beats) or any(d <= 0 for d in durations):
        return None
    return Example(key=key, storyboard=board, durations=durations)


def _key_stamp(key: str) -> str:
    match = _STAMP_RE.search(key)
    return match.group(1) if match else ""


def load_examples(s3_client, bucket: str, count: int, seed: int) -> list[Example]:
    """`count` stored stories drawn from the newest 2 * count valid ones and
    shuffled with `seed`. Only top-level stories/<key>.json records count, so
    nothing this module writes can become an example."""
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=STORIES_PREFIX):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key.endswith(".json") and "/" not in key[len(STORIES_PREFIX) :]:
                keys.append(key)
    keys.sort(key=_key_stamp, reverse=True)
    pool: list[Example] = []
    for key in keys:
        if len(pool) >= 2 * count:
            break
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read()
        try:
            record = json.loads(body)
        except json.JSONDecodeError:
            continue
        example = story_example(key, record)
        if example is not None:
            pool.append(example)
    random.Random(seed).shuffle(pool)
    return pool[:count]


def first_attempt(
    example: Example,
    system_prompt: str,
    complete: Callable[..., str],
    model: str,
    work_dir: Path,
    render: Callable = _render,
) -> Rollout:
    """One first attempt scored like production would treat it."""
    storyboard = Storyboard.model_validate(example.storyboard)
    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": build_scene_code_prompt(
                storyboard, example.durations, None, None
            ),
        },
    ]
    try:
        source = extract_python_block(complete(model, messages))
    except BudgetExceeded:
        raise
    except Exception as exc:  # noqa: BLE001 - a failed call scores like a rejected file
        return Rollout(
            REJECTED, f"The scene coder call failed: {_failure_detail(exc)}", ""
        )
    findings = check_scene_code(source, len(storyboard.beats))
    if findings:
        return Rollout(
            REJECTED, "The guard rejected the file:\n- " + "\n- ".join(findings), source
        )
    scene_file = _write_attempt(work_dir, 1, source)
    spec = {
        "durations": example.durations,
        "subtitles": [beat.narration for beat in storyboard.beats],
    }
    try:
        _, timing = render(scene_file, spec, RENDER_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        return Rollout(
            RENDER_FAILED,
            "Manim failed with this traceback:\n" + _stderr_tail(exc),
            source,
        )
    except subprocess.TimeoutExpired:
        return Rollout(
            RENDER_FAILED,
            f"The render did not finish within {RENDER_TIMEOUT} s. The scene is too "
            "heavy: use fewer or coarser mobjects, lighter updaters, and shorter run_times.",
            source,
        )
    except _TimingInvalid as exc:
        return Rollout(RENDER_FAILED, f"The render timing is invalid: {exc}.", source)
    error = _timing_error(timing, example.durations)
    if error:
        return Rollout(RENDER_FAILED, f"The render timing is invalid: {error}.", source)
    warnings = timing.get("layout_warnings") or []
    if warnings:
        return Rollout(
            LAYOUT_ROUND,
            "The video rendered, but the layout has problems at the end of these "
            "beats:\n- " + "\n- ".join(warnings),
            source,
        )
    return Rollout(
        SHIPS,
        "The first attempt rendered with valid timing and a clean layout.",
        source,
    )


class SceneCodeAdapter:
    """GEPA adapter. evaluate runs first attempts in parallel threads (renders
    are subprocesses, as in the chapter lanes); make_reflective_dataset hands
    the reflection model each storyboard, an excerpt of the file written for
    it, and production's feedback text."""

    # None tells GEPA to use its own reflective proposer with reflection_lm.
    propose_new_texts = None

    def __init__(
        self,
        complete: Callable[..., str],
        model: str,
        work_root: Path,
        workers: int,
        attempt: Callable[..., Rollout] = first_attempt,
    ):
        self.complete, self.model, self.work_root = complete, model, Path(work_root)
        self.workers, self.attempt = workers, attempt

    def _run(self, example: Example, prompt: str) -> Rollout:
        work_dir = Path(tempfile.mkdtemp(prefix="rollout-", dir=self.work_root))
        try:
            return self.attempt(example, prompt, self.complete, self.model, work_dir)
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    def evaluate(self, batch, candidate, capture_traces=False):
        from gepa import EvaluationBatch

        prompt = candidate[COMPONENT]
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            rollouts = list(pool.map(lambda example: self._run(example, prompt), batch))
        trajectories = None
        if capture_traces:
            trajectories = [
                {
                    "title": example.storyboard.get("title", example.key),
                    "beats": len(example.storyboard.get("beats", [])),
                    "score": rollout.score,
                    "feedback": rollout.feedback,
                    "source": rollout.source,
                }
                for example, rollout in zip(batch, rollouts, strict=True)
            ]
        return EvaluationBatch(
            outputs=[rollout.source for rollout in rollouts],
            scores=[rollout.score for rollout in rollouts],
            trajectories=trajectories,
        )

    def make_reflective_dataset(self, candidate, eval_batch, components_to_update):
        rows = [
            {
                "Inputs": f"Storyboard '{t['title']}' with {t['beats']} beats",
                "Generated Outputs": t["source"][:SOURCE_EXCERPT_CHARS],
                "Feedback": f"Score {t['score']}. {t['feedback'][:FEEDBACK_CHARS]}",
            }
            for t in eval_batch.trajectories or []
        ]
        return {name: rows for name in components_to_update}


class Budget:
    """Estimated spend from the usage sink the completion appends to. As a
    GEPA stopper it ends the run between iterations once the cap is reached;
    guard() wraps a completion so a call past HARD_CAP_FACTOR * cap raises."""

    def __init__(self, cap_usd: float, usage: list):
        self.cap_usd, self.usage = cap_usd, usage

    def spent(self) -> float:
        return sum(item.estimated_cost_usd for item in list(self.usage))

    def __call__(self, gepa_state) -> bool:
        return self.spent() >= self.cap_usd

    def guard(self, complete: Callable[..., str]) -> Callable[..., str]:
        def guarded(model, messages, **kwargs):
            if self.spent() >= self.cap_usd * HARD_CAP_FACTOR:
                raise BudgetExceeded(
                    f"spent ${self.spent():.2f} of a ${self.cap_usd:.2f} cap"
                )
            return complete(model, messages, **kwargs)

        return guarded


def _instance_scores(subscores, index: int) -> list[float]:
    scores = subscores[index]
    return list(scores.values()) if isinstance(scores, dict) else list(scores)


def run(
    s3_client,
    bucket: str,
    complete: Callable[..., str],
    usage: list,
    *,
    reflect_complete: Callable[..., str] | None = None,
    scene_model: str,
    reflection_model: str,
    baseline_model: str | None,
    train_count: int,
    val_count: int,
    max_metric_calls: int,
    budget_usd: float,
    workers: int,
    seed: int,
    work_root: Path,
) -> dict:
    """Run GEPA once and upload its results. Returns the summary.
    reflect_complete, when given, serves the reflection model's calls, so
    they can run at a different effort than the scene calls."""
    import gepa

    examples = load_examples(s3_client, bucket, train_count + val_count, seed)
    if len(examples) < train_count + val_count:
        raise ValueError(
            f"only {len(examples)} usable stories, {train_count + val_count} needed"
        )
    train, val = examples[:train_count], examples[train_count:]
    budget = Budget(budget_usd, usage)
    guarded = budget.guard(complete)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work_root = Path(work_root)
    run_dir = work_root / f"gepa-{run_id}"
    run_dir.mkdir(parents=True, exist_ok=True)

    guarded_reflect = budget.guard(reflect_complete or complete)

    def reflect(prompt) -> str:
        messages = (
            prompt
            if isinstance(prompt, list)
            else [{"role": "user", "content": prompt}]
        )
        return guarded_reflect(reflection_model, messages)

    summary: dict = {
        "run_id": run_id,
        "scene_model": scene_model,
        "reflection_model": reflection_model,
        "baseline_model": baseline_model,
        "train": [example.key for example in train],
        "val": [example.key for example in val],
        "budget_usd": budget_usd,
        "max_metric_calls": max_metric_calls,
    }
    seed_candidate = {COMPONENT: SCENE_CODE_SYSTEM}
    if baseline_model and baseline_model != scene_model:
        # Today's prompt on today's model, on the same held-out storyboards:
        # separates what a model switch buys from what GEPA buys.
        baseline = SceneCodeAdapter(
            guarded, baseline_model, work_root, workers
        ).evaluate(val, seed_candidate)
        summary["baseline_val_score"] = sum(baseline.scores) / len(baseline.scores)
        summary["baseline_clean_first_tries"] = sum(
            1 for s in baseline.scores if s >= SHIPS
        )
    best_prompt = SCENE_CODE_SYSTEM
    try:
        result = gepa.optimize(
            seed_candidate=seed_candidate,
            trainset=train,
            valset=val,
            adapter=SceneCodeAdapter(guarded, scene_model, work_root, workers),
            reflection_lm=reflect,
            reflection_minibatch_size=3,
            max_metric_calls=max_metric_calls,
            stop_callbacks=budget,
            run_dir=str(run_dir),
            seed=seed,
        )
    except BudgetExceeded as exc:
        summary["stopped"] = f"hard budget stop: {exc}"
        logger.warning("GEPA stopped by the hard budget cap: %s", exc)
    else:
        best = result.best_idx
        seed_scores = _instance_scores(result.val_subscores, 0)
        best_scores = _instance_scores(result.val_subscores, best)
        best_prompt = result.candidates[best][COMPONENT]
        summary.update(
            {
                "candidates": len(result.candidates),
                "best_index": best,
                "seed_val_score": result.val_aggregate_scores[0],
                "best_val_score": result.val_aggregate_scores[best],
                "seed_clean_first_tries": sum(1 for s in seed_scores if s >= SHIPS),
                "best_clean_first_tries": sum(1 for s in best_scores if s >= SHIPS),
                "val_count": len(val),
                "metric_calls": result.total_metric_calls,
            }
        )
    summary["estimated_cost_usd"] = round(budget.spent(), 2)
    prefix = f"{RESULTS_PREFIX}{run_id}/"
    files = {
        "summary.json": json.dumps(summary, indent=1),
        "seed_prompt.txt": SCENE_CODE_SYSTEM,
        "best_prompt.txt": best_prompt,
    }
    for name, text in files.items():
        s3_client.put_object(
            Bucket=bucket, Key=prefix + name, Body=text.encode("utf-8")
        )
    for path in run_dir.rglob("*"):
        if path.is_file():
            s3_client.upload_file(
                str(path), bucket, prefix + "run/" + str(path.relative_to(run_dir))
            )
    summary["results"] = f"s3://{bucket}/{prefix}"
    return summary


def telegram_summary(summary: dict) -> str:
    lines = [f"GEPA scene-code run {summary['run_id']}"]
    n = len(summary.get("val", []))
    if "baseline_val_score" in summary:
        lines.append(
            f"Today's prompt on {summary['baseline_model']}: score "
            f"{summary['baseline_val_score']:.2f}, clean first renders "
            f"{summary['baseline_clean_first_tries']}/{n}"
        )
    if "best_val_score" in summary:
        lines += [
            f"On {summary['scene_model']}:",
            (
                f"First-try score on {n} held-out storyboards: "
                f"{summary['seed_val_score']:.2f} -> {summary['best_val_score']:.2f}"
            ),
            (
                f"Clean first renders: {summary['seed_clean_first_tries']}/{n} -> "
                f"{summary['best_clean_first_tries']}/{n}"
            ),
            f"{summary['candidates']} candidates, {summary['metric_calls']} rollouts",
        ]
    else:
        lines.append(summary.get("stopped", "stopped before a result"))
    lines += [
        f"Estimated cost: ${summary['estimated_cost_usd']:.2f}",
        f"Prompts and run: {summary.get('results', '')}",
        "Nothing changed in production. Adopting the prompt is a PR.",
    ]
    return "\n".join(lines)
