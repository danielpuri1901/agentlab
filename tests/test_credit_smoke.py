"""Offline tests for the credit smoke test script.

Tests model list resolution, cost calculation, and script structure without
requiring AWS credentials or Bedrock access.
"""

import importlib.util
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from agentlab.costs import resolve_price

smoke_script_path = Path(__file__).parent.parent / "scripts" / "credit_smoke.py"
spec = importlib.util.spec_from_file_location("credit_smoke", smoke_script_path)
credit_smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(credit_smoke)


class TestCreditSmokeOffline:
    """Test offline components of the credit smoke test script."""

    def test_model_configs_resolve_through_costs(self):
        """Verify each model in MODELS resolves through resolve_price."""
        for model_id, friendly_name in credit_smoke.get_model_configs():
            # Should not raise KeyError
            price = resolve_price(model_id)
            assert price.input_per_mtok > 0, f"Missing input price for {model_id}"
            assert price.output_per_mtok > 0, f"Missing output price for {model_id}"

    def test_model_configs_match_live_verified_ids(self):
        """MODELS must use the exact ids confirmed against a live AWS account
        via `aws bedrock list-inference-profiles` (region eu-west-1): Nova
        Lite has no global inference profile, so it uses the eu regional
        profile, while both Claude tiers use the global profile."""
        assert credit_smoke.get_model_configs() == [
            ("bedrock/eu.amazon.nova-lite-v1:0", "Nova Lite"),
            ("bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0", "Haiku 4.5"),
            ("bedrock/global.anthropic.claude-sonnet-4-6", "Sonnet 4.6"),
        ]

    def test_expected_cost_calculation(self):
        """Verify cost calculation for typical token usage."""
        # Nova Lite: ~2000 input tokens, ~50 output tokens (rough estimate)
        nova_cost = credit_smoke.calculate_expected_cost(
            "bedrock/eu.amazon.nova-lite-v1:0", input_tokens=2000, output_tokens=50
        )
        assert 0 < nova_cost < 0.1, f"Nova Lite cost out of range: ${nova_cost}"

        # Haiku 4.5: ~2000 input tokens, ~50 output tokens
        haiku_cost = credit_smoke.calculate_expected_cost(
            "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            input_tokens=2000,
            output_tokens=50,
        )
        assert 0 < haiku_cost < 0.2, f"Haiku 4.5 cost out of range: ${haiku_cost}"

        # Sonnet 4.6: ~2000 input tokens, ~50 output tokens
        sonnet_cost = credit_smoke.calculate_expected_cost(
            "bedrock/global.anthropic.claude-sonnet-4-6", input_tokens=2000, output_tokens=50
        )
        assert 0 < sonnet_cost < 0.2, f"Sonnet 4.6 cost out of range: ${sonnet_cost}"

        # Verify Haiku is cheaper than Sonnet (by price rates)
        assert haiku_cost < sonnet_cost, "Expected Haiku to be cheaper than Sonnet"

    def test_nova_lite_gets_exactly_one_call(self):
        """Nova Lite's credit coverage is near-certain, so it stays at one call."""
        nova_id = next(
            model_id
            for model_id, friendly_name in credit_smoke.get_model_configs()
            if friendly_name == "Nova Lite"
        )
        cost_per_call = credit_smoke.calculate_expected_cost(
            nova_id, credit_smoke.ESTIMATED_INPUT_TOKENS, credit_smoke.MAX_OUTPUT_TOKENS
        )
        repeats = credit_smoke.repeats_for_family(nova_id, cost_per_call)
        assert repeats == credit_smoke.NOVA_LITE_CALLS == 1

    def test_claude_family_repeats_reach_target_spend(self):
        """Each Claude family's derived repeat count must land its expected
        total at or just above TARGET_DOLLARS_PER_CLAUDE_FAMILY, and in a
        sane 10-50 call band: enough repeats for a real, attributable
        Bedrock charge without needing a large dollar total (the
        credit-coverage question is which billing entity a charge lands
        under, not how big the charge is)."""
        for model_id, friendly_name in credit_smoke.get_model_configs():
            if friendly_name == "Nova Lite":
                continue
            cost_per_call = credit_smoke.calculate_expected_cost(
                model_id, credit_smoke.ESTIMATED_INPUT_TOKENS, credit_smoke.MAX_OUTPUT_TOKENS
            )
            repeats = credit_smoke.repeats_for_family(model_id, cost_per_call)
            expected_total = repeats * cost_per_call
            target = credit_smoke.TARGET_DOLLARS_PER_CLAUDE_FAMILY
            assert target <= expected_total < target + cost_per_call, (
                f"{friendly_name} expected total ${expected_total:.4f} not "
                f"within one call's cost of target ${target}"
            )
            assert 10 <= repeats <= 50, (
                f"{friendly_name} repeats={repeats} outside the sane 10-50 call band"
            )
            assert repeats <= credit_smoke.MAX_REPEATS_PER_FAMILY

    def test_grand_expected_total_in_sub_dollar_band(self):
        """Total expected spend across all families must land in the
        deliberately small sub-$2 band this design targets: enough calls per
        family to be a believable signal, not the $5-6 the earlier
        over-solved design produced, and not the sub-$0.01 the original bug
        produced."""
        grand_total = 0.0
        for model_id, friendly_name in credit_smoke.get_model_configs():
            cost_per_call = credit_smoke.calculate_expected_cost(
                model_id, credit_smoke.ESTIMATED_INPUT_TOKENS, credit_smoke.MAX_OUTPUT_TOKENS
            )
            repeats = credit_smoke.repeats_for_family(model_id, cost_per_call)
            grand_total += repeats * cost_per_call

        assert 2 * credit_smoke.TARGET_DOLLARS_PER_CLAUDE_FAMILY <= grand_total < 2.0, (
            f"grand expected total ${grand_total:.2f} outside the sub-$2 target band"
        )

    def test_prompt_length(self):
        """Verify the prompt lands around 8,000 tokens per the Finding 4
        amendment: bigger per-call requests need fewer repeats to produce a
        real, attributable charge."""
        prompt = credit_smoke.PROMPT
        # Rough tokenization: ~1.3 tokens per word on average for English
        word_count = len(prompt.split())
        rough_token_count = word_count * 1.3

        # Enforce spec: 7000-9000 tokens (accounting for tokenizer variance)
        assert 7000 <= rough_token_count <= 9000, (
            f"Prompt word count {word_count} (~{rough_token_count:.0f} tokens) "
            f"outside spec range [7000, 9000]"
        )

    def test_estimated_input_tokens_matches_prompt(self):
        """ESTIMATED_INPUT_TOKENS is derived from PROMPT, not hardcoded, so
        it can't silently drift out of sync with the actual prompt size."""
        assert credit_smoke.ESTIMATED_INPUT_TOKENS == round(
            len(credit_smoke.PROMPT.split()) * 1.3
        )

    def test_max_output_tokens(self):
        """Verify max output tokens constant is set."""
        assert credit_smoke.MAX_OUTPUT_TOKENS == 500


def test_extract_usage_against_real_modeloutput():
    from inspect_ai.model import ModelOutput, ModelUsage

    out = ModelOutput.from_content(model="test", content="hello world response")
    out.usage = ModelUsage(input_tokens=8000, output_tokens=450, total_tokens=8450)
    assert credit_smoke.extract_usage(out) == (8000, 450, "hello world response")


def test_extract_usage_falls_back_when_usage_missing():
    from inspect_ai.model import ModelOutput

    out = ModelOutput.from_content(model="test", content="three word reply")
    out.usage = None
    tokens_in, tokens_out, text = credit_smoke.extract_usage(out)
    assert tokens_in == credit_smoke.ESTIMATED_INPUT_TOKENS
    assert tokens_out == 3
    assert text == "three word reply"
