from agentlab.stats import paired_analysis, required_tasks, verdict


def _mk(vals):  # helper: same tasks both arms
    return {f"t{i}": [v] for i, v in enumerate(vals)}


def test_clear_improvement_promotes():
    base = _mk([0.4] * 30)
    cand = _mk([0.7] * 29 + [0.69])  # tiny jitter so sd > 0
    r = paired_analysis(base, cand)
    assert r.ci_low > 0
    assert verdict(r, protected=[]) == "PROMOTE"


def test_straddling_zero_is_inconclusive():
    base = _mk([0.5, 0.6, 0.4, 0.55, 0.45, 0.5, 0.6, 0.4])
    cand = _mk([0.52, 0.58, 0.43, 0.53, 0.44, 0.53, 0.58, 0.42])
    r = paired_analysis(base, cand)
    assert r.ci_low < 0 < r.ci_high
    assert verdict(r, protected=[]) == "INCONCLUSIVE"


def test_protected_regression_holds_a_promote():
    primary = paired_analysis(_mk([0.4] * 30), _mk([0.7] * 29 + [0.69]))
    cost = paired_analysis(_mk([1.0] * 30), _mk([2.0] * 29 + [1.99]))  # cost doubled
    assert verdict(primary, protected=[(cost, 0.5)]) == "HOLD"  # allowed +0.5, saw +1.0


def test_required_tasks_shrinks_with_bigger_effect():
    assert required_tasks(sd_task_delta=0.2, mde=0.05) > required_tasks(sd_task_delta=0.2, mde=0.15)
