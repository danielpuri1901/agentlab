# AgentLab v0.1: Local Statistical Verdict Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce one defensible, replayable, cost-accounted statistical verdict on a real experiment (compaction summarizer quality, measured by information retained across the compaction boundary), entirely locally.

**Architecture:** Inspect (inspect_ai) runs all trials and captures logs; AgentLab adds a seeded synthetic-session corpus, a compaction Inspect task, a paired-statistics module with power sizing and an INCONCLUSIVE-capable verdict, cost accounting from Inspect token usage, and a CLI that runs pilot and experiment and renders a report. No AWS resources are created in v0.1; model calls go to Bedrock (Nova Lite default) or the Anthropic API via Inspect's model providers.

**Tech Stack:** Python 3.12, uv, inspect_ai + inspect_evals, pydantic v2, scipy + statsmodels (paired CI and power), pandas (log analysis), typer (CLI), jinja2 (report), faker (corpus filler), litellm (model price map), pytest, ruff.

**Spec:** `docs/phase0-synthesis.md` (sections 2, 3, 5, 6 including the post-review revisions block). Research grounding: `docs/research/evaluation-methods.md`, `docs/research/agent-improvement-literature.md`, `docs/research/bedrock-model-pricing.md`.

## Global Constraints

- Never use the em dash character anywhere; use "-".
- In Markdown prose, each full sentence on its own line.
- No AWS resources created or modified in v0.1; the only AWS touchpoint is Bedrock model invocation through Inspect, and the credit smoke-test script (Task 7) which invokes models only.
- Default experiment model: Bedrock Amazon Nova Lite; every model id lives in config, never hard-coded in logic.
- All randomness seeded; a run's seed is part of its record; identical seed + config must reproduce identical corpora.
- Commits per task; commit messages without any co-author or agent-name lines (Daniel's standing rule overrides harness defaults).
- ruff and pytest must be clean at every commit.
- inspect_ai API usage: the executor MUST verify exact signatures against the installed version's docs (https://inspect.aisi.org.uk/) before implementing Tasks 3 and 6; the code shown there is the intended shape, not verified API.
- Library-first (Daniel's standing rule): owned code is limited to the verdict rules, the compaction solvers/prompts, and thin glue; anything with an established library equivalent (CLI, templating, statistical tests, power analysis, price data, log dataframes, filler text) uses the library.
- Prices come from litellm's maintained model price map; only the +30% Claude tokenizer adjustment is layered on top, documented in code with a pointer to `docs/research/bedrock-model-pricing.md`.

---

### Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`, `src/agentlab/__init__.py`, `tests/__init__.py`, `.gitignore`, `AGENTS.md`
- Test: `tests/test_scaffold.py`

**Interfaces:**
- Consumes: nothing.
- Produces: importable package `agentlab` with `agentlab.__version__ = "0.1.0"`; dev commands `uv run pytest`, `uv run ruff check .`.

- [ ] **Step 1: Initialize the project**

```bash
cd ~/Desktop/Projects/agentlab
uv init --package --name agentlab --python 3.12
uv add inspect-ai inspect-evals pydantic scipy statsmodels pandas typer jinja2 faker litellm
uv add --dev pytest ruff
```

- [ ] **Step 2: Write the failing test**

```python
# tests/test_scaffold.py
import agentlab

def test_version():
    assert agentlab.__version__ == "0.1.0"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: FAIL (no `__version__`).

- [ ] **Step 4: Implement**

```python
# src/agentlab/__init__.py
__version__ = "0.1.0"
```

Write `AGENTS.md` with three sections: what AgentLab is (two sentences pointing at `docs/phase0-synthesis.md`), how to run tests (`uv run pytest`), and where plans live (`docs/plans/`).
Add a standard Python `.gitignore` (`.venv/`, `__pycache__/`, `*.egg-info/`, `logs/`).

- [ ] **Step 5: Verify pass and commit**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS, no lint errors.

```bash
git add -A && git commit -m "feat: scaffold agentlab package with uv, pytest, ruff"
```

---

### Task 2: Seeded session corpus generator

**Files:**
- Create: `src/agentlab/corpus.py`
- Test: `tests/test_corpus.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `generate_session(seed: int, n_facts: int = 12, filler_turns: int = 40) -> Session` where `Session` is a pydantic model with fields `transcript: list[str]` (chat turns), `facts: list[Fact]`, and `Fact` has `key: str`, `value: str`, `probe_question: str`.
Facts are planted at deterministic positions inside otherwise-filler turns.
The `Session`/`Fact` pydantic surface is ours either way; the machinery behind it is reused where possible.

- [ ] **Step 0: Check inspect_evals for reusable needle-in-a-haystack machinery**

Inspect the inspect_evals repo (https://github.com/UKGovernmentBEIS/inspect_evals) for a niah / needle-in-a-haystack eval and read its dataset builder.
If it can plant facts at controlled positions with per-sample metadata, wrap it behind `generate_session` instead of implementing planting ourselves.
If its shape does not fit (no probe questions, no position control), implement the faker-based fallback below and record the finding in the commit message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_corpus.py
from agentlab.corpus import generate_session

def test_deterministic_by_seed():
    a, b = generate_session(seed=7), generate_session(seed=7)
    assert a.transcript == b.transcript and a.facts == b.facts

def test_different_seeds_differ():
    assert generate_session(seed=1).transcript != generate_session(seed=2).transcript

def test_facts_present_in_transcript():
    s = generate_session(seed=3)
    joined = "\n".join(s.transcript)
    assert len(s.facts) == 12
    for f in s.facts:
        assert f.value in joined

def test_probe_answerable():
    s = generate_session(seed=3)
    for f in s.facts:
        assert f.key in f.probe_question
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement**

Preferred path: thin wrapper over the inspect_evals niah dataset builder found in Step 0.
Fallback (only if Step 0 found no fit), with faker generating the filler so we hand-write no prose:

```python
# src/agentlab/corpus.py
import random
from faker import Faker
from pydantic import BaseModel

class Fact(BaseModel, frozen=True):
    key: str
    value: str
    probe_question: str

class Session(BaseModel):
    transcript: list[str]
    facts: list[Fact]

def generate_session(seed: int, n_facts: int = 12, filler_turns: int = 40) -> Session:
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    facts = []
    for i in range(n_facts):
        key = f"{fake.word()}-{i}-{rng.randint(100, 999)}"
        value = f"CODE-{rng.randint(10_000, 99_999)}"
        facts.append(Fact(
            key=key,
            value=value,
            probe_question=f"What was the exact code recorded for {key}?",
        ))
    roles = ["user", "assistant"]
    turns = [f"{roles[i % 2]}: {fake.sentence(nb_words=12)}" for i in range(filler_turns)]
    positions = sorted(rng.sample(range(filler_turns), n_facts))
    for pos, fact in zip(positions, facts):
        turns[pos] = f"assistant: note for the record, {fact.key} resolved with {fact.value}."
    return Session(transcript=turns, facts=facts)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_corpus.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: seeded synthetic session corpus with planted facts and probes"
```

---

### Task 3: Compaction Inspect task (baseline vs candidate solvers, recall scorer)

**Files:**
- Create: `src/agentlab/compaction_task.py`
- Test: `tests/test_compaction_task.py`

**Interfaces:**
- Consumes: `generate_session` from Task 2.
- Produces: `compaction_dataset(seeds: list[int]) -> list[Sample]` (one Inspect `Sample` per session, `metadata` carrying facts and the compaction boundary at 60% of turns); `compact_transcript(turns: list[str], style: str) -> str` prompt builder where `style` is `"truncate"` (baseline: keep only post-boundary turns) or `"structured"` (candidate: model-written structured summary of pre-boundary turns); `recall_scorer()` returning fraction of probes answered with the exact planted value; `compaction_task(style: str, model: str)` returning an Inspect `Task`.
- MANDATORY first action: read the installed inspect_ai docs for `Task`, `Sample`, `solver`, `scorer`, and `eval()` signatures and adjust the shape below to the real API before writing the test.

- [ ] **Step 1: Verify inspect_ai API against docs, then write the failing tests**

Pure-python parts are testable without any model call:

```python
# tests/test_compaction_task.py
from agentlab.compaction_task import compaction_dataset, compact_transcript

def test_dataset_one_sample_per_seed():
    samples = compaction_dataset(seeds=[1, 2, 3])
    assert len(samples) == 3
    assert all("facts" in s.metadata and "boundary" in s.metadata for s in samples)

def test_truncate_drops_preboundary_and_structured_keeps_all_input():
    turns = [f"turn {i}" for i in range(10)]
    truncated = compact_transcript(turns, style="truncate")
    assert "turn 0" not in truncated and "turn 9" in truncated
    structured_prompt = compact_transcript(turns, style="structured")
    assert "turn 0" in structured_prompt  # pre-boundary content goes INTO the summarizer prompt
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compaction_task.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement**

Implement `compaction_dataset`, `compact_transcript`, `recall_scorer` (string-contains match on the exact `CODE-xxxxx` value, deterministic, no judge), and `compaction_task` wiring: solver compacts at the boundary per `style`, then answers each probe question with only post-compaction context.
The structured style asks the model to produce a summary that preserves exact identifiers and codes; the truncate style is the no-summary baseline.
Score = answered-correctly count / n_facts, recorded per sample.

- [ ] **Step 4: Run tests, then a 2-sample live smoke**

Run: `uv run pytest tests/test_compaction_task.py -v`
Expected: PASS.
Then one manual smoke with real credentials (executor pauses here if no credentials are configured and surfaces it):
`uv run inspect eval` on `compaction_task(style="truncate", model="bedrock/amazon.nova-lite-v1:0")` limited to 2 samples.
Expected: run completes, log written under `logs/`, per-sample recall score present.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: compaction task with truncate/structured solvers and recall scorer"
```

---

### Task 4: Paired statistics module with power sizing and verdict

**Files:**
- Create: `src/agentlab/stats.py`
- Test: `tests/test_stats.py`

**Interfaces:**
- Consumes: nothing (pure functions over floats).
- Produces:
`paired_analysis(baseline: dict[str, list[float]], candidate: dict[str, list[float]]) -> PairedResult` where dict keys are task ids, values are per-repeat scores; `PairedResult` has `mean_delta: float`, `ci_low: float`, `ci_high: float`, `n_tasks: int`, `sd_task_delta: float`.
`required_tasks(sd_task_delta: float, mde: float, alpha: float = 0.05, power: float = 0.8) -> int`.
`verdict(primary: PairedResult, protected: list[tuple[PairedResult, float]]) -> str` returning `"PROMOTE" | "REJECT" | "INCONCLUSIVE" | "HOLD"`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_stats.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_stats.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement**

The statistical machinery comes from scipy and statsmodels; only the verdict rules are ours.
scipy must be >= 1.10 for `ttest_rel(...).confidence_interval()`.

```python
# src/agentlab/stats.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_stats.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: paired stats with power sizing and INCONCLUSIVE-capable verdict"
```

---

### Task 5: Cost accounting from Inspect logs

**Files:**
- Create: `src/agentlab/costs.py`
- Test: `tests/test_costs.py`

**Interfaces:**
- Consumes: litellm's maintained `model_cost` price map; Inspect eval log token-usage fields (executor verifies exact field names in the installed version's `EvalLog` model before implementing).
- Produces: `resolve_price(model: str) -> ModelPrice` (`ModelPrice` has `input_per_mtok: float`, `output_per_mtok: float`, `source: str`), looked up from litellm's map, with `CLAUDE_TOKENIZER_ADJUSTMENT = 1.30` applied to any model id containing "claude" (documented, pointer to `docs/research/bedrock-model-pricing.md` sections 2.3 and 3); `run_cost(model: str, input_tokens: int, output_tokens: int) -> float`; `experiment_cost(usages: list[tuple[str, int, int]]) -> float`.
- The exact litellm map keys for our Bedrock model ids MUST be verified against `litellm.model_cost` at implementation time; do not guess key formats.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_costs.py
import pytest
from agentlab.costs import CLAUDE_TOKENIZER_ADJUSTMENT, resolve_price, run_cost, experiment_cost

NOVA = "bedrock/amazon.nova-lite-v1:0"  # executor: confirm this key exists in litellm.model_cost

def test_nova_lite_resolves_with_positive_prices():
    p = resolve_price(NOVA)
    assert p.input_per_mtok > 0 and p.output_per_mtok > 0

def test_claude_gets_tokenizer_adjustment():
    claude_key = "<claude key verified from litellm.model_cost>"  # executor fills real key
    raw_in = resolve_price(claude_key).input_per_mtok
    assert raw_in == pytest.approx(_raw_litellm_input_per_mtok(claude_key) * CLAUDE_TOKENIZER_ADJUSTMENT)

def test_unknown_model_raises():
    with pytest.raises(KeyError):
        resolve_price("bedrock/does-not-exist")

def test_run_and_experiment_cost_arithmetic():
    p = resolve_price(NOVA)
    expected = p.input_per_mtok + p.output_per_mtok * 0.1
    assert run_cost(NOVA, 1_000_000, 100_000) == pytest.approx(expected)
    assert experiment_cost([(NOVA, 1_000_000, 100_000)] * 3) == pytest.approx(expected * 3)
```

The helper `_raw_litellm_input_per_mtok` reads `litellm.model_cost[key]["input_cost_per_token"] * 1e6` directly, so the adjustment test compares our layer against the raw map (field name verified at implementation).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_costs.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement**

Implement `ModelPrice` (pydantic) and `resolve_price` as a thin lookup over `litellm.model_cost` (per-token fields scaled to per-Mtok), applying `CLAUDE_TOKENIZER_ADJUSTMENT` when "claude" is in the model id, raising `KeyError` for unknown ids; `run_cost` and `experiment_cost` are pure arithmetic on top.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_costs.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: model price table and cost accounting"
```

---

### Task 6: Pilot and experiment CLI with report

**Files:**
- Create: `src/agentlab/cli.py`, `src/agentlab/report.py`, `src/agentlab/templates/report.md.j2`
- Modify: `pyproject.toml` (add `[project.scripts] agentlab = "agentlab.cli:app"`)
- Test: `tests/test_report.py`

**Interfaces:**
- Consumes: Tasks 2-5 (`compaction_task`, `paired_analysis`, `required_tasks`, `verdict`, `experiment_cost`).
- Produces: a Typer app (`agentlab pilot --tasks 20 --repeats 5 --model <id>` and `agentlab run --tasks N --repeats K --model <id>`) with the same behavior as before: pilot runs ONE config and writes `results/pilot.json` with variance inputs and measured cost; run executes baseline and candidate paired on identical seeds and writes `results/experiment-<timestamp>/report.md` with hypothesis, config, per-arm recall, `PairedResult` numbers, verdict, total cost, tokens, and Inspect log paths for replay.
- Score and token extraction from logs uses Inspect's analysis dataframes plus pandas, NOT hand-parsing (executor verifies the current dataframe API, e.g. `samples_df`, in the installed version's docs).
- `render_report(result: PairedResult, verdict_str: str, total_cost: float, log_paths: list[str]) -> str` in `report.py` renders `src/agentlab/templates/report.md.j2` via jinja2 and stays pure and testable.

- [ ] **Step 1: Write the failing test for the pure part**

```python
# tests/test_report.py
from agentlab.report import render_report
from agentlab.stats import PairedResult

def test_report_contains_verdict_and_cost():
    r = PairedResult(0.12, 0.03, 0.21, 20, 0.19)
    md = render_report(result=r, verdict_str="PROMOTE", total_cost=1.42,
                       log_paths=["logs/a.eval", "logs/b.eval"])
    assert "PROMOTE" in md and "$1.42" in md and "logs/a.eval" in md
    assert "0.03" in md and "0.21" in md  # CI bounds visible
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_report.py -v`
Expected: FAIL with import error.

- [ ] **Step 3: Implement**

`render_report` fills `templates/report.md.j2` with jinja2 (one sentence per line in the template).
`cli.py` is a Typer app with `pilot` and `run` commands; `run` executes both arms via Inspect's `eval()` on identical seed lists, extracts per-sample scores and token usage through Inspect's analysis dataframes with pandas (API verified against installed docs), feeds `paired_analysis` -> `verdict` -> `render_report`, and prints the verdict plus report path.

- [ ] **Step 4: Run tests, then the live pilot**

Run: `uv run pytest -v && uv run ruff check .`
Expected: PASS, clean.
Then the real variance pilot (executor pauses and surfaces if credentials missing):
`uv run agentlab pilot --tasks 20 --repeats 5 --model bedrock/amazon.nova-lite-v1:0`
Expected: completes for well under $5; `results/pilot.json` written; the measured `sd_task_delta` and cost are pasted into `docs/phase0-synthesis.md` section 3 by the executor, replacing the assumed-parameter caution with measured values.

- [ ] **Step 5: Commit**

```bash
git add -A && git commit -m "feat: agentlab CLI with variance pilot, paired run, and verdict report"
```

---

### Task 7: Bedrock credit smoke-test script and runbook

**Files:**
- Create: `scripts/credit_smoke.py`, `docs/runbooks/credit-smoke-test.md`

**Interfaces:**
- Consumes: `run_cost` from Task 5.
- Produces: a script that sends one fixed ~2,000-token request to each configured model family (Nova Lite, Haiku 4.5, Sonnet 5) via Inspect or boto3, prints the expected cost per family from `costs.py`, and a runbook documenting the manual half: wait 24-48h, open Cost Explorer grouped by service and by "AWS Marketplace", and record per family whether the charge drew from promotional credits.

- [ ] **Step 1: Write the runbook first**

`docs/runbooks/credit-smoke-test.md` states: purpose (verify which model families' Bedrock usage draws from the $1,000 promotional credits, since Marketplace charges are excluded per `docs/research/bedrock-model-pricing.md` section 2.9), the exact script invocation, expected spend (< $1 total), the Cost Explorer steps, and a results table to fill in (family | billed via | credit-covered yes/no).

- [ ] **Step 2: Implement the script**

`scripts/credit_smoke.py`: for each family send one fixed prompt ("Summarize: " + 1,500 words of lorem text, max 200 output tokens), print model id, tokens used, and expected cost via `run_cost`.
No test file; this is an operational script, verified by running it.

- [ ] **Step 3: Run it once with real credentials**

Run: `uv run python scripts/credit_smoke.py`
Expected: three successful responses, total expected cost printed under $1.
Executor pauses and surfaces to Daniel if AWS credentials or Bedrock model access are not configured; enabling model access in the AWS console is a manual step only Daniel can do.

- [ ] **Step 4: Commit**

```bash
git add -A && git commit -m "feat: bedrock credit smoke test script and runbook"
```

---

## Execution order and gates

Tasks 1, 2, 4, 5 have no external dependencies and can run immediately (4 and 5 in parallel with 2 by different subagents if desired).
Task 3 step 4, Task 6 step 4, and Task 7 step 3 need real model credentials: the executor pauses at the FIRST credentialed step and surfaces exactly what Daniel must configure (AWS account, Bedrock model access for Nova/Claude families in eu-west-1 or the global endpoint, credentials in the environment).
The experiment proper (full `agentlab run` at pilot-sized n) happens after the pilot's measured variance chooses n via `required_tasks`, and its verdict plus measured costs become the inputs that the v0.2 AWS-fabric plan is written from.
