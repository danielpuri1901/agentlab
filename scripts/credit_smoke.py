"""
Bedrock credit smoke test: verify which model families draw from promotional credits.

Sends one fixed ~1500-word (~2000-token) prompt to each model family (Nova
Lite, Haiku 4.5, Sonnet 5) with max 200 output tokens per call. Nova Lite is
called once - its first-party credit coverage is near-certain, so one call is
enough to confirm it. Each Claude family is called a repeat count derived
from run_cost (see repeats_for_family) so its expected total spend lands
near TARGET_DOLLARS_PER_CLAUDE_FAMILY: legible in Cost Explorer instead of
vanishing into UI rounding. Prints the spend plan (per-family call count and
expected cost) before sending anything, then per-family actual token usage
and cost via run_cost from costs.py, then a grand total.

This script is structured for offline testing: model configurations, prompts, and
cost calculation are all deterministic and do not require AWS credentials. The live
invocation uses inspect_ai's model API, which fails gracefully with a clear message
if credentials are missing.

Rationale for inspect_ai over boto3: inspect_ai is already a project dependency
and its Bedrock provider requires no additional dependencies. This keeps the
smoke test script minimal and avoids introducing boto3 as a new dependency just
for credential handling.
"""

import asyncio
import math
import sys
from pathlib import Path

# Add src to path so we can import agentlab
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agentlab.costs import run_cost

# Model configurations. Claude entries use the "global." inference-profile ids:
# newer Anthropic models on Bedrock (Sonnet 5, Haiku 4.5) typically require a
# geo inference-profile id for on-demand invocation rather than the bare
# model id. The global profile carries no pricing premium (see
# docs/research/bedrock-model-pricing.md section 5.2) and both ids are
# confirmed present in litellm's local price map. Before running against a
# real account, verify these ids are actually provisioned there (see
# docs/runbooks/credit-smoke-test.md prerequisites).
#
# Bare-id fallback, if the global inference profile is unavailable in the
# account: "bedrock/anthropic.claude-haiku-4-5-20251001-v1:0" and
# "bedrock/anthropic.claude-sonnet-5".
MODELS = [
    ("bedrock/amazon.nova-lite-v1:0", "Nova Lite"),
    ("bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0", "Haiku 4.5"),
    ("bedrock/global.anthropic.claude-sonnet-5", "Sonnet 5"),
]

NOVA_LITE_CALLS = 1
"""Nova Lite is first-party Bedrock billing (not AWS Marketplace), so its
credit coverage is near-certain; one call is enough to confirm it draws from
credits at all, unlike the Claude families this script is actually testing."""

TARGET_DOLLARS_PER_CLAUDE_FAMILY = 2.5
"""Expected spend per Claude family. Chosen in the $2-3 range so the
resulting charge is legible against Cost Explorer's UI rounding while still
being a smoke test, not a real experiment run."""

MAX_REPEATS_PER_FAMILY = 1000
"""Safety cap on the derived repeat count, well above what real Bedrock
prices require to reach TARGET_DOLLARS_PER_CLAUDE_FAMILY (roughly 424-848
calls at current Sonnet 5 / Haiku 4.5 rates). Guards against a pricing bug
(e.g. a near-zero resolved price) turning a smoke test into an unbounded
number of live API calls."""

ESTIMATED_INPUT_TOKENS = 1950
"""Rough token estimate for PROMPT (~1500 words at ~1.3 tokens/word), used
both as the pre-run cost estimate that sizes REPEATS_PER_FAMILY and as a
fallback if a live response has no usable usage field."""

# Fixed prompt: ~1500 words of Lorem ipsum text (approximately 2000 tokens)
_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis "
    "nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. "
    "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu "
    "fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum. "
)

PROMPT = "Summarize this comprehensive technical document:\n\n" + (
    _LOREM * 22
    + "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium "
    "doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore "
    "veritatis et quasi architecto beatae vitae dicta sunt explicabo."
)

# Maximum output tokens
MAX_OUTPUT_TOKENS = 200


def get_model_configs():
    """Return the list of (model_id, friendly_name) tuples.

    Testable offline: no dependencies on AWS or credentials.
    """
    return MODELS


def calculate_expected_cost(model_id, input_tokens, output_tokens):
    """Calculate expected cost for a model and token usage.

    Testable offline: uses costs.run_cost which has no AWS dependencies.
    """
    return run_cost(model_id, input_tokens, output_tokens)


def repeats_for_family(model_id, cost_per_call):
    """How many times to call `model_id` in the smoke test.

    Nova Lite always gets exactly NOVA_LITE_CALLS - its credit coverage is
    near-certain, so one call is enough. Each Claude family gets a repeat
    count derived from `cost_per_call` (itself computed via run_cost) so its
    total expected spend lands at or just above
    TARGET_DOLLARS_PER_CLAUDE_FAMILY, capped at MAX_REPEATS_PER_FAMILY.

    Testable offline: pure arithmetic over a pre-computed cost estimate, no
    AWS dependencies.
    """
    if "nova" in model_id:
        return NOVA_LITE_CALLS
    if cost_per_call <= 0:
        raise ValueError(
            f"non-positive per-call cost estimate for {model_id}: {cost_per_call}"
        )
    return min(
        math.ceil(TARGET_DOLLARS_PER_CLAUDE_FAMILY / cost_per_call),
        MAX_REPEATS_PER_FAMILY,
    )


async def invoke_model_via_inspect_ai(model_id, prompt, max_tokens):
    """Invoke a model via inspect_ai's model API.

    Fails gracefully with a clear message if AWS credentials are missing.

    Returns: (input_tokens, output_tokens, response_text)
    """
    try:
        from inspect_ai.model import GenerateConfig, get_model
    except ImportError:
        raise RuntimeError(
            "inspect_ai not installed. Install with: uv add inspect_ai"
        )

    try:
        model = get_model(model_id)
        config = GenerateConfig(max_tokens=max_tokens)
        response = await model.generate(prompt, config=config)

        # Extract token counts from response
        # inspect_ai's response object has usage info
        if hasattr(response, "usage"):
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        else:
            # Fallback: estimate from response text
            output_tokens = len(response.text.split())
            input_tokens = ESTIMATED_INPUT_TOKENS

        return input_tokens, output_tokens, response.text
    except Exception as e:
        error_msg = str(e)
        if "credential" in error_msg.lower() or "auth" in error_msg.lower():
            raise RuntimeError(
                f"AWS credentials not configured or Bedrock model access not "
                f"enabled.\n\nTo fix:\n"
                f"1. Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in your "
                f"environment\n"
                f"2. Ensure Bedrock model access is provisioned in the AWS "
                f"console for {model_id}\n"
                f"3. AWS_REGION comes from your ambient AWS config; this "
                f"project's standard is eu-west-1 with the global inference "
                f"profile for Claude (see docs/runbooks/credit-smoke-test.md)\n\n"
                f"Original error: {error_msg}"
            )
        raise


async def main():
    """Run the credit smoke test."""
    print("Bedrock Credit Smoke Test")
    print("=" * 60)
    print()

    # Build the spend plan and print it before sending a single request, so
    # the operator sees exactly how many live calls are about to happen and
    # roughly what they'll cost.
    plan = []  # (model_id, friendly_name, repeats, cost_per_call)
    grand_expected = 0.0
    print("Plan (expected spend before sending any request):")
    for model_id, friendly_name in get_model_configs():
        cost_per_call = calculate_expected_cost(
            model_id, ESTIMATED_INPUT_TOKENS, MAX_OUTPUT_TOKENS
        )
        repeats = repeats_for_family(model_id, cost_per_call)
        expected_total = repeats * cost_per_call
        grand_expected += expected_total
        plan.append((model_id, friendly_name, repeats))
        print(
            f"  {friendly_name} ({model_id}): {repeats} call(s) "
            f"x ~${cost_per_call:.4f} ~= ${expected_total:.2f}"
        )
    print(f"Grand expected total: ${grand_expected:.2f}")
    print()

    grand_actual = 0.0

    for model_id, friendly_name, repeats in plan:
        print(f"Testing {friendly_name} ({model_id}) x{repeats}...")

        family_cost = 0.0
        family_input_tokens = 0
        family_output_tokens = 0
        try:
            for call_num in range(1, repeats + 1):
                (input_tokens, output_tokens, _) = await (
                    invoke_model_via_inspect_ai(model_id, PROMPT, MAX_OUTPUT_TOKENS)
                )
                family_cost += calculate_expected_cost(
                    model_id, input_tokens, output_tokens
                )
                family_input_tokens += input_tokens
                family_output_tokens += output_tokens
        except RuntimeError as e:
            print(f"ERROR (call {call_num}/{repeats}): {e}")
            sys.exit(1)
        except (ValueError, KeyError, AttributeError) as e:
            print(f"UNEXPECTED ERROR (call {call_num}/{repeats}): {e}")
            sys.exit(1)

        grand_actual += family_cost
        print(f"  Total input tokens:  {family_input_tokens}")
        print(f"  Total output tokens: {family_output_tokens}")
        print(f"  Actual cost:         ${family_cost:.4f}")
        print()

    print("=" * 60)
    print(f"Grand actual total: ${grand_actual:.4f}")
    print()
    print(
        "Wait 24-48h for AWS Cost Explorer to settle, then read exact amounts "
        "via Cost Explorer's CSV export or the Cost Explorer API rather than "
        "the console UI alone - UI rounding can hide small per-family charges."
    )


if __name__ == "__main__":
    asyncio.run(main())
