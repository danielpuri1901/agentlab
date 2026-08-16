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

    def test_expected_cost_calculation(self):
        """Verify cost calculation for typical token usage."""
        # Nova Lite: ~2000 input tokens, ~50 output tokens (rough estimate)
        nova_cost = credit_smoke.calculate_expected_cost(
            "bedrock/amazon.nova-lite-v1:0", input_tokens=2000, output_tokens=50
        )
        assert 0 < nova_cost < 0.1, f"Nova Lite cost out of range: ${nova_cost}"

        # Haiku 4.5: ~2000 input tokens, ~50 output tokens
        haiku_cost = credit_smoke.calculate_expected_cost(
            "bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0",
            input_tokens=2000,
            output_tokens=50,
        )
        assert 0 < haiku_cost < 0.2, f"Haiku 4.5 cost out of range: ${haiku_cost}"

        # Sonnet 5: ~2000 input tokens, ~50 output tokens
        sonnet_cost = credit_smoke.calculate_expected_cost(
            "bedrock/global.anthropic.claude-sonnet-5", input_tokens=2000, output_tokens=50
        )
        assert 0 < sonnet_cost < 0.2, f"Sonnet 5 cost out of range: ${sonnet_cost}"

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
        total at or just above TARGET_DOLLARS_PER_CLAUDE_FAMILY, so the
        resulting Bedrock charge is legible in Cost Explorer instead of
        vanishing into UI rounding (the bug this replaces)."""
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
            assert repeats <= credit_smoke.MAX_REPEATS_PER_FAMILY

    def test_grand_expected_total_is_legible(self):
        """Total expected spend across all families must land in a legible
        multi-dollar range, not the sub-$0.01 total the original design produced."""
        grand_total = 0.0
        for model_id, friendly_name in credit_smoke.get_model_configs():
            cost_per_call = credit_smoke.calculate_expected_cost(
                model_id, credit_smoke.ESTIMATED_INPUT_TOKENS, credit_smoke.MAX_OUTPUT_TOKENS
            )
            repeats = credit_smoke.repeats_for_family(model_id, cost_per_call)
            grand_total += repeats * cost_per_call

        assert grand_total >= 2 * credit_smoke.TARGET_DOLLARS_PER_CLAUDE_FAMILY, (
            f"grand expected total ${grand_total:.2f} is not legible"
        )

    def test_prompt_length(self):
        """Verify the prompt is ~2000 tokens as specified in the brief.

        The brief specifies: one fixed ~2,000-token request, which translates to
        approximately 1500 words given English tokenization (~1.3 tokens per word).
        """
        prompt = credit_smoke.PROMPT
        # Rough tokenization: ~1.3 tokens per word on average for English
        word_count = len(prompt.split())
        rough_token_count = word_count * 1.3

        # Enforce spec: 1600-2600 tokens (accounting for tokenizer variance)
        assert 1600 <= rough_token_count <= 2600, (
            f"Prompt word count {word_count} (~{rough_token_count:.0f} tokens) "
            f"outside spec range [1600, 2600]"
        )

    def test_max_output_tokens(self):
        """Verify max output tokens constant is set."""
        assert credit_smoke.MAX_OUTPUT_TOKENS == 200
