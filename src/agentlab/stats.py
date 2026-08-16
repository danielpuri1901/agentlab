import math
from dataclasses import dataclass

from scipy import stats as sps
from statsmodels.stats.power import TTestPower


@dataclass
class PairedResult:
    mean_delta: float
    ci_low: float
    ci_high: float
    n_tasks: int
    sd_task_delta: float


def paired_analysis(baseline, candidate, alpha: float = 0.05) -> PairedResult:
    tasks = sorted(set(baseline) & set(candidate))
    if len(tasks) < 2:
        raise ValueError("need at least 2 shared tasks")
    b = [sum(baseline[t]) / len(baseline[t]) for t in tasks]
    c = [sum(candidate[t]) / len(candidate[t]) for t in tasks]
    deltas = [x - y for x, y in zip(c, b)]
    n = len(deltas)
    mean = sum(deltas) / n
    sd = math.sqrt(sum((d - mean) ** 2 for d in deltas) / (n - 1))
    ci = sps.ttest_rel(c, b).confidence_interval(confidence_level=1 - alpha)
    return PairedResult(mean, ci.low, ci.high, n, sd)


def required_tasks(sd_task_delta: float, mde: float, alpha: float = 0.05, power: float = 0.8) -> int:
    n = TTestPower().solve_power(
        effect_size=mde / sd_task_delta, alpha=alpha, power=power, alternative="two-sided"
    )
    return math.ceil(n)


def verdict(primary: PairedResult, protected) -> str:
    if primary.ci_low > 0:
        for res, allowed_delta in protected:
            if res.ci_high > allowed_delta:  # protected metric regressed beyond allowance
                return "HOLD"
        return "PROMOTE"
    if primary.ci_high < 0:
        return "REJECT"
    return "INCONCLUSIVE"
