"""
Bedrock credit smoke test: verify which model families draw from promotional credits.

Sends one fixed ~6200-word (~8000-token) prompt to each model family (Nova
Lite, Haiku 4.5, Sonnet 5) with max 500 output tokens per call. The
credit-coverage question this test answers is qualitative - which billing
entity (AWS vs AWS Marketplace) a charge appears under - and Cost Explorer's
CSV/API views show sub-dollar amounts at full precision, so calls are sized
to answer that question cheaply rather than to hit a specific dollar target.
Nova Lite is called once - its first-party credit coverage is near-certain,
so one call is enough to confirm it. Each Claude family is called a repeat
count derived from run_cost (see repeats_for_family) so its expected total
spend lands near TARGET_DOLLARS_PER_CLAUDE_FAMILY, enough calls to be a
believable signal without needing a large total. Prints the spend plan
(per-family call count and expected cost) before sending anything, then
per-family actual token usage and cost via run_cost from costs.py, then a
grand total.

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

# Model configurations, confirmed against a live AWS account via
# `aws bedrock list-inference-profiles` (region eu-west-1; see
# docs/runbooks/credit-smoke-test.md prerequisites for how to re-verify).
# Claude entries use the "global." inference-profile ids: newer Anthropic
# models on Bedrock (Sonnet 5, Haiku 4.5) require a geo inference-profile id
# for on-demand invocation rather than the bare model id, and global carries
# no pricing premium (see docs/research/bedrock-model-pricing.md section
# 2.8). Nova Lite has no global inference profile, so it uses the "eu."
# regional profile instead. All three ids are confirmed present in litellm's
# local price map (verified via resolve_price - see
# tests/test_credit_smoke.py).
#
# Bare-id fallback, if a profile above is ever unavailable in the account:
# "bedrock/amazon.nova-lite-v1:0", "bedrock/anthropic.claude-haiku-4-5-20251001-v1:0",
# and "bedrock/anthropic.claude-sonnet-5".
MODELS = [
    ("bedrock/eu.amazon.nova-lite-v1:0", "Nova Lite"),
    ("bedrock/global.anthropic.claude-haiku-4-5-20251001-v1:0", "Haiku 4.5"),
    ("bedrock/global.anthropic.claude-sonnet-5", "Sonnet 5"),
]

NOVA_LITE_CALLS = 1
"""Nova Lite is first-party Bedrock billing (not AWS Marketplace), so its
credit coverage is near-certain; one call is enough to confirm it draws from
credits at all, unlike the Claude families this script is actually testing."""

TARGET_DOLLARS_PER_CLAUDE_FAMILY = 0.30
"""Expected spend per Claude family. The credit-coverage question is
qualitative (which billing entity a charge lands under), and Cost Explorer's
CSV/API views show sub-dollar amounts at full precision, so this only needs
to be big enough for a handful of real Bedrock calls to produce a readable
signal - not a specific dollar target. At current rates this derives to
roughly 15-30 calls per Claude family (see repeats_for_family)."""

MAX_REPEATS_PER_FAMILY = 100
"""Runaway guard on the derived repeat count, not a target: comfortably
above the ~15-30 calls real Bedrock prices require to reach
TARGET_DOLLARS_PER_CLAUDE_FAMILY, so it only bites if a pricing bug (e.g. a
near-zero resolved price) would otherwise turn a smoke test into an
unbounded number of live API calls."""

# One copy of the document body is ~1550 words (~2000 tokens); PROMPT below
# repeats it 4x under a single instruction to land near 8,000 input tokens
# per call, big enough that a handful of repeats per Claude family produces
# a real, attributable charge.
_LOREM = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
    "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis "
    "nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat. "
    "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu "
    "fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
    "culpa qui officia deserunt mollit anim id est laborum. "
)

_DOCUMENT_BODY = (
    _LOREM * 22
    + "Sed ut perspiciatis unde omnis iste natus error sit voluptatem accusantium "
    "doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore "
    "veritatis et quasi architecto beatae vitae dicta sunt explicabo."
)

PROMPT = "Summarize this comprehensive technical document:\n\n" + (_DOCUMENT_BODY * 4)

ESTIMATED_INPUT_TOKENS = round(len(PROMPT.split()) * 1.3)
"""Rough token estimate for PROMPT (~1.3 tokens/word), derived from PROMPT
itself rather than hardcoded so it can't drift out of sync. Used both as the
pre-run cost estimate that sizes repeats_for_family and as a fallback if a
live response has no usable usage field."""

# Maximum output tokens
MAX_OUTPUT_TOKENS = 500


def extract_usage(response):
    """Extract (input_tokens, output_tokens, text) from an inspect_ai ModelOutput.

    ModelOutput.usage is Optional and hasattr() is always True on the pydantic
    model, so this checks for None explicitly. The generated text lives in
    .completion (there is no .text attribute). Testable offline against a
    constructed ModelOutput.
    """
    if response.usage is not None:
        return (
            response.usage.input_tokens,
            response.usage.output_tokens,
            response.completion,
        )
    return (
        ESTIMATED_INPUT_TOKENS,
        len(response.completion.split()),
        response.completion,
    )


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

        return extract_usage(response)
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
        "the console UI alone - UI rounding can hide sub-dollar charges. "
        "Group by billing entity (AWS vs AWS Marketplace) and by service: "
        "that grouping, not the dollar amount, is the actual answer to the "
        "credit-coverage question this test exists to answer."
    )


if __name__ == "__main__":
    asyncio.run(main())
