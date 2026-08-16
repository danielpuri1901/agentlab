import litellm
import pytest

from agentlab.costs import (
    CLAUDE_TOKENIZER_ADJUSTMENT,
    experiment_cost,
    resolve_price,
    run_cost,
)

NOVA = "bedrock/amazon.nova-lite-v1:0"  # litellm key is "amazon.nova-lite-v1:0" (no bedrock/ prefix)
CLAUDE = "anthropic.claude-sonnet-5"  # verified key in litellm.model_cost


def _raw_litellm_input_per_mtok(key: str) -> float:
    return litellm.model_cost[key]["input_cost_per_token"] * 1e6


def test_nova_lite_resolves_with_positive_prices():
    p = resolve_price(NOVA)
    assert p.input_per_mtok > 0 and p.output_per_mtok > 0


def test_claude_resolves_to_raw_litellm_price_unadjusted():
    raw_in = resolve_price(CLAUDE).input_per_mtok
    assert raw_in == pytest.approx(_raw_litellm_input_per_mtok(CLAUDE))


def test_claude_tokenizer_adjustment_constant_is_exported_but_unapplied():
    assert CLAUDE_TOKENIZER_ADJUSTMENT == 1.30


def test_unknown_model_raises():
    with pytest.raises(KeyError):
        resolve_price("bedrock/does-not-exist")


def test_run_and_experiment_cost_arithmetic():
    p = resolve_price(NOVA)
    expected = p.input_per_mtok + p.output_per_mtok * 0.1
    assert run_cost(NOVA, 1_000_000, 100_000) == pytest.approx(expected)
    assert experiment_cost([(NOVA, 1_000_000, 100_000)] * 3) == pytest.approx(
        expected * 3
    )
