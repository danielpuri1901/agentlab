"""Shared eval-running machinery.

Used by both the local CLI (`agentlab.cli`, which runs both arms of a paired
experiment in one process) and the cloud worker commands (`agentlab.worker`,
where a worker owns exactly one arm). Keeping this in its own module - rather
than in `cli.py`, which `worker.py` cannot import without creating a cycle -
means both entrypoints exercise the identical Inspect eval path.
"""

import os
from pathlib import Path

import typer
from inspect_ai import eval_async
from inspect_ai.log import EvalLog

from agentlab.compaction_task import compaction_task

# eval_async has no display kwarg (sync eval only); silence Inspect's
# progress UI process-wide instead. Set here, not in cli.py, so it also
# applies when the cloud worker commands are the process entrypoint.
os.environ.setdefault("INSPECT_DISPLAY", "none")


def hypothesis_for(baseline_style: str, candidate_style: str) -> str:
    """Derive the report's hypothesis line from the actual arms under test."""
    return (
        f"The '{candidate_style}' compaction style retains more of the planted "
        f"per-fact information across the compaction boundary than the "
        f"'{baseline_style}' style does."
    )


class ArmRunError(typer.Exit):
    """Raised when an eval run for one arm does not succeed.

    Subclasses `typer.Exit` rather than a plain exception so every existing
    call site that expects `typer.Exit` - the local CLI, and this module's
    own tests - sees no behavior change: raising it still aborts a Typer
    command with the given exit code. The point of the subclass is
    `message`/`__str__`: `str(typer.Exit(1))` is just `"1"` (click's `Exit`
    never calls `super().__init__(message)`), which loses the actual
    diagnosis for callers that need more than the exit code - the cloud
    worker's ARM_FAILED write needs the arm name, status, and log location,
    not the number 1.
    """

    def __init__(self, message: str, code: int = 1) -> None:
        super().__init__(code=code)
        self.message = message

    def __str__(self) -> str:
        return self.message


def check_log_status(log: EvalLog, arm_name: str) -> None:
    """Abort cleanly if an Inspect eval run for `arm_name` did not succeed.

    A live provider failure (throttling, auth, etc.) leaves `log.status` as
    "error" or "cancelled" with no scores recorded. Letting extraction run
    against that log crashes deep inside `extract_results` with a confusing
    `KeyError` instead of a clear message naming what failed and where the
    log lives.
    """
    if log.status != "success":
        message = (
            f"error: eval run for arm '{arm_name}' did not succeed "
            f"(status={log.status}); see log at {log.location}"
        )
        typer.echo(message, err=True)
        raise ArmRunError(message)


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
