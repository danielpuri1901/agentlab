"""Render the paired-experiment verdict report from a jinja2 template.

Kept pure and testable: no file I/O, no Inspect log reading, just template
fill-in over already-computed values.
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from agentlab.stats import PairedResult

TEMPLATE_DIR = Path(__file__).parent / "templates"

_env = Environment(
    loader=FileSystemLoader(TEMPLATE_DIR),
    autoescape=False,  # Markdown output, not HTML; no escaping wanted
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_report(
    result: PairedResult,
    verdict_str: str,
    total_cost: float,
    log_paths: list[str],
    *,
    hypothesis: str | None = None,
    model: str | None = None,
    baseline_style: str | None = None,
    candidate_style: str | None = None,
    seeds: list[int] | None = None,
    repeats: int | None = None,
    baseline_recall: float | None = None,
    candidate_recall: float | None = None,
    total_tokens: int | None = None,
) -> str:
    """Render `templates/report.md.j2` into a Markdown report string.

    Only `result`, `verdict_str`, `total_cost`, and `log_paths` are required;
    the remaining keyword-only fields are the experiment's config and per-arm
    numbers, rendered when supplied and omitted from the report otherwise.
    """
    template = _env.get_template("report.md.j2")
    return template.render(
        result=result,
        verdict_str=verdict_str,
        total_cost=total_cost,
        log_paths=log_paths,
        hypothesis=hypothesis,
        model=model,
        baseline_style=baseline_style,
        candidate_style=candidate_style,
        seeds=seeds,
        repeats=repeats,
        baseline_recall=baseline_recall,
        candidate_recall=candidate_recall,
        total_tokens=total_tokens,
    )
