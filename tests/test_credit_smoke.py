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
            "bedrock/anthropic.claude-haiku-4-5-20251001-v1:0",
            input_tokens=2000,
            output_tokens=50,
        )
        assert 0 < haiku_cost < 0.2, f"Haiku 4.5 cost out of range: ${haiku_cost}"

        # Sonnet 5: ~2000 input tokens, ~50 output tokens
        sonnet_cost = credit_smoke.calculate_expected_cost(
            "bedrock/anthropic.claude-sonnet-5", input_tokens=2000, output_tokens=50
        )
        assert 0 < sonnet_cost < 0.2, f"Sonnet 5 cost out of range: ${sonnet_cost}"

        # Verify Haiku is cheaper than Sonnet (by price rates)
        assert haiku_cost < sonnet_cost, "Expected Haiku to be cheaper than Sonnet"

    def test_total_expected_cost_under_one_dollar(self):
        """Verify total cost across all models stays under $1 for the smoke test."""
        # With typical token estimates: 2000 input, 50-100 output per model
        total_cost = 0.0
        for model_id, _ in credit_smoke.get_model_configs():
            # Estimate: 2000 input, 100 output tokens per model
            cost = credit_smoke.calculate_expected_cost(
                model_id, input_tokens=2000, output_tokens=100
            )
            total_cost += cost

        # The smoke test's max output is 200 tokens, so worst case:
        # 3 models * 2000 input + 3 models * 200 output
        worst_case_total = 0.0
        for model_id, _ in credit_smoke.get_model_configs():
            cost = credit_smoke.calculate_expected_cost(
                model_id, input_tokens=2000, output_tokens=200
            )
            worst_case_total += cost

        assert worst_case_total < 1.0, (
            f"Worst-case total cost ${worst_case_total:.4f} exceeds $1.00 target"
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
