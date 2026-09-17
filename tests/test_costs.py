from types import SimpleNamespace

import litellm
import pytest

from agentlab.costs import (
    CLAUDE_TOKENIZER_ADJUSTMENT,
    ModelCallUsage,
    cache_static_system_prompt,
    completion_usage,
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


def test_cache_aware_cost_uses_each_measured_token_class():
    model = "bedrock/global.anthropic.claude-sonnet-4-6"
    price = resolve_price(model)

    cost = run_cost(
        model,
        input_tokens=100,
        output_tokens=20,
        cache_read_input_tokens=300,
        cache_write_input_tokens=400,
    )

    assert cost == pytest.approx(
        price.input_per_mtok * 100 / 1e6
        + price.output_per_mtok * 20 / 1e6
        + price.cache_read_input_per_mtok * 300 / 1e6
        + price.cache_write_input_per_mtok * 400 / 1e6
    )


def test_completion_usage_separates_uncached_and_cached_input_tokens():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=1_700,
            completion_tokens=20,
            prompt_tokens_details=SimpleNamespace(
                text_tokens=1_000,
                cached_tokens=500,
                cache_creation_tokens=200,
            ),
        )
    )

    usage = completion_usage(
        response,
        requested_model="bedrock/arn:profile",
        pricing_model="bedrock/global.anthropic.claude-sonnet-4-6",
        stage="scene",
        latency_ms=1234,
    )

    assert usage == ModelCallUsage(
        stage="scene",
        requested_model="bedrock/arn:profile",
        pricing_model="bedrock/global.anthropic.claude-sonnet-4-6",
        input_tokens=1_000,
        output_tokens=20,
        cache_read_input_tokens=500,
        cache_write_input_tokens=200,
        latency_ms=1234,
        estimated_cost_usd=pytest.approx(
            run_cost(
                "bedrock/global.anthropic.claude-sonnet-4-6",
                input_tokens=1_000,
                output_tokens=20,
                cache_read_input_tokens=500,
                cache_write_input_tokens=200,
            )
        ),
        pricing_source="litellm:global.anthropic.claude-sonnet-4-6",
    )


def test_completion_usage_handles_missing_cache_details():
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            prompt_tokens_details=None,
        )
    )

    usage = completion_usage(
        response,
        requested_model=NOVA,
        pricing_model=NOVA,
        stage="pick",
        latency_ms=4,
    )

    assert usage.cache_read_input_tokens == 0
    assert usage.cache_write_input_tokens == 0


def test_cache_static_system_prompt_adds_one_checkpoint_without_mutation():
    messages = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "dynamic paper"},
    ]

    cached = cache_static_system_prompt(messages)

    assert cached[0]["cache_control"] == {"type": "ephemeral"}
    assert cached[1] == messages[1]
    assert "cache_control" not in messages[0]


def test_cache_static_system_prompt_leaves_messages_without_system_unchanged():
    messages = [{"role": "user", "content": "dynamic paper"}]
    assert cache_static_system_prompt(messages) == messages
