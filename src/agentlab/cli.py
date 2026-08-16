"""AgentLab CLI: the compaction-quality variance pilot and the paired
baseline-vs-candidate experiment run.

Thin orchestration only: build the Inspect tasks, run them, hand the results
to `agentlab.results` for extraction and `agentlab.stats`/`agentlab.report`
for analysis and rendering.
"""

import asyncio
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import typer
from inspect_ai import eval_async
from inspect_ai.log import EvalLog

from agentlab.compaction_task import compaction_task
from agentlab.report import render_report
from agentlab.results import extract_results, mean_score, total_cost, total_tokens
from agentlab.stats import paired_analysis, required_tasks, verdict

# eval_async has no display kwarg (sync eval only); silence Inspect's
# progress UI process-wide instead.
os.environ.setdefault("INSPECT_DISPLAY", "none")

app = typer.Typer()

BASELINE_STYLE = "truncate"
CANDIDATE_STYLE = "structured"

HYPOTHESIS = (
    "Structured summarization retains more of the planted per-fact "
    "information across the compaction boundary than truncation does."
)


def check_log_status(log: EvalLog, arm_name: str) -> None:
    """Abort cleanly if an Inspect eval run for `arm_name` did not succeed.

    A live provider failure (throttling, auth, etc.) leaves `log.status` as
    "error" or "cancelled" with no scores recorded. Letting extraction run
    against that log crashes deep inside `extract_results` with a confusing
    `KeyError` instead of a clear message naming what failed and where the
    log lives.
    """
    if log.status != "success":
        typer.echo(
            f"error: eval run for arm '{arm_name}' did not succeed "
            f"(status={log.status}); see log at {log.location}",
            err=True,
        )
        raise typer.Exit(1)


async def _run_arm(
    style: str,
    model: str,
    seeds: list[int],
    repeats: int,
    log_dir: Path,
    summary_budget: int,
    n_facts: int,
    filler_turns: int,
    max_connections: int | None = None,
) -> EvalLog:
    task = compaction_task(
        style=style,
        model=model,
        seeds=seeds,
        summary_budget=summary_budget,
        n_facts=n_facts,
        filler_turns=filler_turns,
    )
    [log] = await eval_async(
        task, epochs=repeats, log_dir=str(log_dir), max_connections=max_connections
    )
    check_log_status(log, arm_name=style)
    return log


async def _run_arms_in_one_loop(
    model, seeds, repeats, log_dir, baseline_style, candidate_style,
    summary_budget, n_facts, filler_turns, max_connections,
):
    # Both arms must share one event loop: provider internals (e.g. the
    # aiobotocore credential-refresh lock) bind to the loop of the first
    # eval and crash a second eval run on a fresh loop.
    baseline_log = await _run_arm(
        baseline_style, model, seeds, repeats, log_dir, summary_budget, n_facts, filler_turns, max_connections
    )
    candidate_log = await _run_arm(
        candidate_style, model, seeds, repeats, log_dir, summary_budget, n_facts, filler_turns, max_connections
    )
    return baseline_log, candidate_log


def _run_paired(
    model: str,
    seeds: list[int],
    repeats: int,
    log_dir: Path,
    baseline_style: str,
    candidate_style: str,
    summary_budget: int,
    n_facts: int,
    filler_turns: int,
    max_connections: int | None = None,
):
    baseline_log, candidate_log = asyncio.run(
        _run_arms_in_one_loop(
            model, seeds, repeats, log_dir, baseline_style, candidate_style,
            summary_budget, n_facts, filler_turns, max_connections,
        )
    )
    baseline = extract_results(baseline_log.location)
    candidate = extract_results(candidate_log.location)
    result = paired_analysis(baseline.scores, candidate.scores)
    cost = total_cost(baseline.usages + candidate.usages)
    return baseline_log, candidate_log, baseline, candidate, result, cost


def _warn_unpriced(cost) -> None:
    if cost.unpriced_models:
        models = ", ".join(sorted(cost.unpriced_models))
        typer.echo(
            f"warning: no litellm price for: {models} (excluded from cost total)",
            err=True,
        )


@app.command()
def pilot(
    tasks: int = typer.Option(20, help="Distinct seeded compaction sessions to run."),
    repeats: int = typer.Option(
        5, help="Epochs per session, for repeat-noise measurement."
    ),
    model: str = typer.Option(..., help="Inspect model id used for both arms."),
    mde: float = typer.Option(
        0.1, help="Minimum detectable effect to size the full run for."
    ),
    results_dir: Path = typer.Option(
        Path("results"), help="Directory pilot.json is written under."
    ),
    n_facts: int = typer.Option(12, help="Planted facts per session."),
    filler_turns: int = typer.Option(40, help="Filler turns per session."),
    summary_budget: int = typer.Option(
        150, help="max_tokens for the summarizer's GenerateConfig (naive/structured only)."
    ),
    baseline_style: str = typer.Option(
        BASELINE_STYLE, help="Compaction style for the baseline arm."
    ),
    candidate_style: str = typer.Option(
        CANDIDATE_STYLE, help="Compaction style for the candidate arm."
    ),
    max_connections: int = typer.Option(
        None, help="Inspect max concurrent model connections (None = Inspect default)."
    ),
) -> None:
    """Run a small paired baseline/candidate pilot to measure sd_task_delta
    and cost before sizing the full `run`."""
    seeds = list(range(tasks))
    log_dir = results_dir / "pilot-logs"
    baseline_log, candidate_log, baseline, candidate, result, cost = _run_paired(
        model,
        seeds,
        repeats,
        log_dir,
        baseline_style,
        candidate_style,
        summary_budget,
        n_facts,
        filler_turns,
        max_connections,
    )
    # sd_task_delta == 0 means baseline and candidate tied on every task (no
    # measured variance, e.g. two arms scoring identically); required_tasks
    # divides mde by sd_task_delta, so this is a genuine edge case to guard
    # rather than a test-only concern.
    n_required = (
        required_tasks(sd_task_delta=result.sd_task_delta, mde=mde)
        if result.sd_task_delta > 0
        else None
    )

    results_dir.mkdir(parents=True, exist_ok=True)
    pilot_path = results_dir / "pilot.json"
    pilot_path.write_text(
        json.dumps(
            {
                "model": model,
                "baseline_style": baseline_style,
                "candidate_style": candidate_style,
                "seeds": seeds,
                "repeats": repeats,
                "n_facts": n_facts,
                "filler_turns": filler_turns,
                "summary_budget": summary_budget,
                "mean_delta": result.mean_delta,
                "ci_low": result.ci_low,
                "ci_high": result.ci_high,
                "n_tasks": result.n_tasks,
                "sd_task_delta": result.sd_task_delta,
                "mde": mde,
                "required_tasks": n_required,
                "baseline_recall": mean_score(baseline.scores),
                "candidate_recall": mean_score(candidate.scores),
                "total_cost": cost.total,
                "unpriced_models": sorted(cost.unpriced_models),
                "total_tokens": total_tokens(baseline.usages + candidate.usages),
                "log_paths": [baseline_log.location, candidate_log.location],
            },
            indent=2,
        )
    )
    typer.echo(f"pilot written to {pilot_path}")
    typer.echo(
        f"sd_task_delta={result.sd_task_delta:.4f} mean_delta={result.mean_delta:.4f} "
        f"required_tasks(mde={mde})={n_required} cost=${cost.total:.2f}"
    )
    _warn_unpriced(cost)


@app.command(name="run")
def run_experiment(
    tasks: int = typer.Option(20, help="Distinct seeded compaction sessions to run."),
    repeats: int = typer.Option(5, help="Epochs per session."),
    model: str = typer.Option(..., help="Inspect model id used for both arms."),
    results_dir: Path = typer.Option(
        Path("results"), help="Directory experiment-<ts>/ is written under."
    ),
    n_facts: int = typer.Option(12, help="Planted facts per session."),
    filler_turns: int = typer.Option(40, help="Filler turns per session."),
    summary_budget: int = typer.Option(
        150, help="max_tokens for the summarizer's GenerateConfig (naive/structured only)."
    ),
    baseline_style: str = typer.Option(
        BASELINE_STYLE, help="Compaction style for the baseline arm."
    ),
    candidate_style: str = typer.Option(
        CANDIDATE_STYLE, help="Compaction style for the candidate arm."
    ),
) -> None:
    """Run baseline vs candidate paired on identical seeds and render a
    verdict report to results/experiment-<timestamp>/report.md."""
    seeds = list(range(tasks))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = results_dir / f"experiment-{timestamp}"

    baseline_log, candidate_log, baseline, candidate, result, cost = _run_paired(
        model,
        seeds,
        repeats,
        experiment_dir / "logs",
        baseline_style,
        candidate_style,
        summary_budget,
        n_facts,
        filler_turns,
        max_connections,
    )
    verdict_str = verdict(result, protected=[])

    report_md = render_report(
        result=result,
        verdict_str=verdict_str,
        total_cost=cost.total,
        log_paths=[baseline_log.location, candidate_log.location],
        hypothesis=HYPOTHESIS,
        model=model,
        baseline_style=baseline_style,
        candidate_style=candidate_style,
        seeds=seeds,
        repeats=repeats,
        baseline_recall=mean_score(baseline.scores),
        candidate_recall=mean_score(candidate.scores),
        total_tokens=total_tokens(baseline.usages + candidate.usages),
        unpriced_models=sorted(cost.unpriced_models),
    )
    experiment_dir.mkdir(parents=True, exist_ok=True)
    report_path = experiment_dir / "report.md"
    report_path.write_text(report_md)

    typer.echo(f"verdict: {verdict_str}")
    typer.echo(f"report written to {report_path}")
    _warn_unpriced(cost)


if __name__ == "__main__":
    app()
