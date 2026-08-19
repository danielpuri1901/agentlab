"""Verdict chart: the one PNG attached to a finalize ping."""

import io

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # backend must be set before pyplot


def verdict_chart_png(
    baseline_label: str,
    candidate_label: str,
    baseline_mean: float,
    candidate_mean: float,
    mean_delta: float,
    ci_low: float,
    ci_high: float,
) -> bytes:
    fig, ax = plt.subplots(figsize=(5, 3.2), dpi=110)
    bars = ax.bar(
        [baseline_label, candidate_label],
        [baseline_mean, candidate_mean],
        color=["#8a8a8a", "#2f6fd6"],
    )
    ax.bar_label(bars, fmt="%.3f")
    ax.set_ylim(0, 1.05)
    ax.set_ylabel("mean score")
    ax.set_title(
        f"delta {mean_delta:+.3f} (95% CI {ci_low:+.3f} to {ci_high:+.3f})",
        fontsize=10,
    )
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()
