"""
Bedrock credit smoke test: verify which model families draw from promotional credits.

Sends one fixed ~1500-word (~2000-token) prompt to each model family
(Nova Lite, Haiku 4.5, Sonnet 5) with max 200 output tokens. Prints model id,
tokens used, and expected cost via run_cost from costs.py.

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
import sys
from pathlib import Path

# Add src to path so we can import agentlab
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agentlab.costs import run_cost

# Model configurations
MODELS = [
    ("bedrock/amazon.nova-lite-v1:0", "Nova Lite"),
    ("bedrock/anthropic.claude-haiku-4-5-20251001-v1:0", "Haiku 4.5"),
    ("bedrock/anthropic.claude-sonnet-5", "Sonnet 5"),
]

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
            # Prompt is approximately 1500 words, ~1950 tokens
            input_tokens = 1950

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
                f"3. Run: export AWS_REGION=us-east-1\n\n"
                f"Original error: {error_msg}"
            )
        raise


async def main():
    """Run the credit smoke test."""
    print("Bedrock Credit Smoke Test")
    print("=" * 60)
    print()

    total_cost = 0.0

    for model_id, friendly_name in get_model_configs():
        print(f"Testing {friendly_name} ({model_id})...")

        try:
            (input_tokens, output_tokens, _) = await (
                invoke_model_via_inspect_ai(model_id, PROMPT, MAX_OUTPUT_TOKENS)
            )

            expected_cost = calculate_expected_cost(
                model_id, input_tokens, output_tokens
            )
            total_cost += expected_cost

            print(f"  Input tokens:  {input_tokens}")
            print(f"  Output tokens: {output_tokens}")
            print(f"  Expected cost: ${expected_cost:.4f}")
            print()
        except RuntimeError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        except (ValueError, KeyError, AttributeError) as e:
            print(f"UNEXPECTED ERROR: {e}")
            sys.exit(1)

    print("=" * 60)
    print(f"Total expected cost: ${total_cost:.4f}")
    if total_cost < 1.0:
        print("Status: PASS (cost < $1.00)")
    else:
        print(f"WARNING: Expected cost ${total_cost:.4f} exceeds $1.00 target")


if __name__ == "__main__":
    asyncio.run(main())
