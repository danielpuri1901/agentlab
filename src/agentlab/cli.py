"""AgentLab CLI: the compaction-quality variance pilot and the paired
baseline-vs-candidate experiment run.

Thin orchestration only: build the Inspect tasks, run them, hand the results
to `agentlab.results` for extraction and `agentlab.stats`/`agentlab.report`
for analysis and rendering.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import typer
from inspect_ai import eval
from inspect_ai.log import EvalLog

from agentlab.compaction_task import compaction_task
from agentlab.report import render_report
from agentlab.results import extract_results, mean_score, total_cost, total_tokens
from agentlab.stats import paired_analysis, required_tasks, verdict

app = typer.Typer()

BASELINE_STYLE = "truncate"
CANDIDATE_STYLE = "structured"

HYPOTHESIS = (
    "Structured summarization retains more of the planted per-fact "
    "information across the compaction boundary than truncation does."
)


def _run_arm(
    style: str, model: str, seeds: list[int], repeats: int, log_dir: Path
) -> EvalLog:
    task = compaction_task(style=style, model=model, seeds=seeds)
    [log] = eval(task, epochs=repeats, log_dir=str(log_dir), display="none")
    return log


def _run_paired(model: str, seeds: list[int], repeats: int, log_dir: Path):
    baseline_log = _run_arm(BASELINE_STYLE, model, seeds, repeats, log_dir)
    candidate_log = _run_arm(CANDIDATE_STYLE, model, seeds, repeats, log_dir)
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
) -> None:
    """Run a small paired baseline/candidate pilot to measure sd_task_delta
    and cost before sizing the full `run`."""
    seeds = list(range(tasks))
    log_dir = results_dir / "pilot-logs"
    baseline_log, candidate_log, baseline, candidate, result, cost = _run_paired(
        model, seeds, repeats, log_dir
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
                "baseline_style": BASELINE_STYLE,
                "candidate_style": CANDIDATE_STYLE,
                "seeds": seeds,
                "repeats": repeats,
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
) -> None:
    """Run baseline vs candidate paired on identical seeds and render a
    verdict report to results/experiment-<timestamp>/report.md."""
    seeds = list(range(tasks))
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    experiment_dir = results_dir / f"experiment-{timestamp}"

    baseline_log, candidate_log, baseline, candidate, result, cost = _run_paired(
        model, seeds, repeats, experiment_dir / "logs"
    )
    verdict_str = verdict(result, protected=[])

    report_md = render_report(
        result=result,
        verdict_str=verdict_str,
        total_cost=cost.total,
        log_paths=[baseline_log.location, candidate_log.location],
        hypothesis=HYPOTHESIS,
        model=model,
        baseline_style=BASELINE_STYLE,
        candidate_style=CANDIDATE_STYLE,
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
