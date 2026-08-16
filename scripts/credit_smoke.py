#!/usr/bin/env python3
"""
Bedrock credit smoke test: verify which model families draw from promotional credits.

Sends one fixed ~2000-token prompt to each model family (Nova Lite, Haiku 4.5,
Sonnet 5) with max 200 output tokens. Prints model id, tokens used, and expected
cost via run_cost from costs.py.

This script is structured for offline testing: model configurations, prompts, and
cost calculation are all deterministic and do not require AWS credentials. The live
invocation uses inspect_ai's model API, which fails gracefully with a clear message
if credentials are missing.

Rationale for inspect_ai over boto3: inspect_ai is already a project dependency
and its Bedrock provider requires no additional dependencies. This keeps the
smoke test script minimal and avoids introducing boto3 as a new dependency just
for credential handling.
"""

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

# Fixed prompt: Lorem ipsum text (~1500+ words = ~2000 tokens after tokenization)
PROMPT = (
    "Summarize this long document:\n\n"
    "The quick brown fox jumps over the lazy dog. Lorem ipsum dolor sit amet, "
    "consectetur adipiscing elit, sed do eiusmod tempor incididunt ut labore et "
    "dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation "
    "ullamco laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure "
    "dolor in reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
    "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in culpa qui "
    "officia deserunt mollit anim id est laborum. Sed ut perspiciatis unde omnis "
    "iste natus error sit voluptatem accusantium doloremque laudantium, totam rem "
    "aperiam, eaque ipsa quae ab illo inventore veritatis et quasi architecto beatae "
    "vitae dicta sunt explicabo. Nemo enim ipsam voluptatem quia voluptas sit "
    "aspernatur aut odit aut fugit, sed quia consequuntur magni dolores eos qui "
    "ratione voluptatem sequi nesciunt. Neque porro quisquam est, qui dolorem ipsum "
    "quia dolor sit amet, consectetur, adipisci velit, sed quia non numquam eius "
    "modi tempora incidunt ut labore et dolore magnam aliquam quaerat voluptatem. "
    "Ut enim ad minima veniam, quis nostrum exercitationem ullam corporis suscipit "
    "laboriosam, nisi ut aliquid ex ea commodi consequatur. Quis autem vel eum iure "
    "reprehenderit qui in ea voluptate velit esse quam nihil molestiae consequatur, "
    "vel illum qui dolorem eum fugiat quo voluptas nulla pariatur. At vero eos et "
    "accusamus et iusto odio dignissimos ducimus qui blanditiis praesentium "
    "voluptatum deleniti atque corrupti quos dolores et quas molestias excepturi "
    "sint occaecati cupiditate non provident, similique sunt in culpa qui officia "
    "deserunt mollitia animi, id est laborum et dolorum fuga. Et harum quidem rerum "
    "facilis est et expedita distinctio. Nam libero tempore, cum soluta nobis est "
    "eligendi optio cumque nihil impedit quo minus id quod maxime placeat facere "
    "possimus, omnis voluptas assumenda est, omnis dolor repellendus. Temporibus "
    "autem quibusdam et aut officiis debitis aut rerum necessitatibus saepe eveniet "
    "ut et voluptates repudiandae sint et molestiae non recusandae itaque earum "
    "rerum hic tenetur a sapiente delectus, ut aut reiciendis voluptatibus maiores "
    "alias consequatur aut perferendis doloribus asperiores repellat. Sed ut "
    "perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque "
    "laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et "
    "quasi architecto beatae vitae dicta sunt explicabo. Nemo enim ipsam voluptatem "
    "quia voluptas sit aspernatur aut odit aut fugit, sed quia consequuntur magni "
    "dolores eos qui ratione voluptatem sequi nesciunt. Neque porro quisquam est, "
    "qui dolorem ipsum quia dolor sit amet, consectetur, adipisci velit, sed quia "
    "non numquam eius modi tempora incidunt ut labore et dolore magnam aliquam "
    "quaerat voluptatem. Ut enim ad minima veniam, quis nostrum exercitationem "
    "ullam corporis suscipit laboriosam, nisi ut aliquid ex ea commodi consequatur. "
    "Quis autem vel eum iure reprehenderit qui in ea voluptate velit esse quam nihil "
    "molestiae consequatur, vel illum qui dolorem eum fugiat quo voluptas nulla "
    "pariatur. At vero eos et accusamus et iusto odio dignissimos ducimus qui "
    "blanditiis praesentium voluptatum deleniti atque corrupti quos dolores et quas "
    "molestias excepturi sint occaecati cupiditate non provident, similique sunt in "
    "culpa qui officia deserunt mollitia animi, id est laborum et dolorum fuga. Et "
    "harum quidem rerum facilis est et expedita distinctio. Nam libero tempore, cum "
    "soluta nobis est eligendi optio cumque nihil impedit quo minus id quod maxime "
    "placeat facere possimus, omnis voluptas assumenda est, omnis dolor repellendus. "
    "Temporibus autem quibusdam et aut officiis debitis aut rerum necessitatibus "
    "saepe eveniet ut et voluptates repudiandae sint et molestiae non recusandae "
    "itaque earum rerum hic tenetur a sapiente delectus, ut aut reiciendis "
    "voluptatibus maiores alias consequatur aut perferendis doloribus asperiores "
    "repellat. Sed ut perspiciatis unde omnis iste natus error sit voluptatem "
    "accusantium doloremque laudantium, totam rem aperiam, eaque ipsa quae ab illo "
    "inventore veritatis et quasi architecto beatae vitae dicta sunt explicabo. Nemo "
    "enim ipsam voluptatem quia voluptas sit aspernatur aut odit aut fugit, sed quia "
    "consequuntur magni dolores eos qui ratione voluptatem sequi nesciunt. Neque "
    "porro quisquam est, qui dolorem ipsum quia dolor sit amet, consectetur, adipisci "
    "velit. Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, "
    "quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea commodo "
    "consequat. Duis aute irure dolor in reprehenderit in voluptate velit esse "
    "cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat cupidatat non "
    "proident, sunt in culpa qui officia deserunt mollit anim id est laborum. Sed ut "
    "perspiciatis unde omnis iste natus error sit voluptatem accusantium doloremque "
    "laudantium, totam rem aperiam, eaque ipsa quae ab illo inventore veritatis et "
    "quasi architecto beatae vitae dicta sunt explicabo."
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


def invoke_model_via_inspect_ai(model_id, prompt, max_tokens):
    """Invoke a model via inspect_ai's model API.

    Fails gracefully with a clear message if AWS credentials are missing.

    Returns: (input_tokens, output_tokens, response_text)
    """
    try:
        from inspect_ai.model import get_model
    except ImportError:
        raise RuntimeError(
            "inspect_ai not installed. Install with: uv add inspect_ai"
        )

    try:
        model = get_model(model_id)
        response = model.generate(prompt, max_tokens=max_tokens)

        # Extract token counts from response
        # inspect_ai's response object has usage info
        if hasattr(response, "usage"):
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
        else:
            # Fallback: estimate from response text
            output_tokens = len(response.text.split())
            # Rough estimate: prompt is ~2000 tokens
            input_tokens = 2000

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


def main():
    """Run the credit smoke test."""
    print("Bedrock Credit Smoke Test")
    print("=" * 60)
    print()

    total_cost = 0.0

    for model_id, friendly_name in get_model_configs():
        print(f"Testing {friendly_name} ({model_id})...")

        try:
            (input_tokens, output_tokens, response_text) = (
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
        except Exception as e:
            print(f"UNEXPECTED ERROR: {e}")
            sys.exit(1)

    print("=" * 60)
    print(f"Total expected cost: ${total_cost:.4f}")
    if total_cost < 1.0:
        print("Status: PASS (cost < $1.00)")
    else:
        print(f"WARNING: Expected cost ${total_cost:.4f} exceeds $1.00 target")


if __name__ == "__main__":
    main()
