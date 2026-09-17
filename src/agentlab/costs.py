import os
from copy import deepcopy
from dataclasses import dataclass

# litellm fetches its price map from GitHub over HTTPS at import time unless
# this is set, which would violate the "no network calls" constraint on this
# module. The bundled local copy of the map has identical values for the
# keys this module resolves (verified against the remote copy). Must be set
# before `import litellm`, since litellm reads it during its own __init__.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm
from pydantic import BaseModel

CLAUDE_TOKENIZER_ADJUSTMENT = 1.30
"""Pre-run, same-text FORECAST factor for the Claude 4.7+ tokenizer
generation (Sonnet 5, Opus 5, Fable 5; NOT Haiku 4.5 or earlier), which
emits about 30% more tokens than earlier Claude generations for identical
input text. See docs/research/bedrock-model-pricing.md section 2.3.

Not applied anywhere in this module: resolve_price, run_cost, and
experiment_cost all use RAW litellm per-token prices against MEASURED
token counts (e.g. from an API response), which are already denominated in
the model's actual tokenizer and would be double-counted by this factor.
Callers doing pre-run cost forecasting from same-text estimates on a
Claude 4.7+ model should apply this factor themselves."""


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    cache_read_input_per_mtok: float
    cache_write_input_per_mtok: float
    source: str


@dataclass(frozen=True)
class ModelCallUsage:
    stage: str
    requested_model: str
    pricing_model: str
    input_tokens: int
    output_tokens: int
    cache_read_input_tokens: int
    cache_write_input_tokens: int
    latency_ms: int
    estimated_cost_usd: float
    pricing_source: str


def _resolve_litellm_key(model: str) -> str:
    """Map our canonical Bedrock model id to a key in litellm.model_cost.

    litellm keys its Bedrock price entries by the bare Bedrock model id
    (e.g. "amazon.nova-lite-v1:0"), not by a "bedrock/"-prefixed id, even
    though our own canonical ids carry that prefix (e.g.
    "bedrock/amazon.nova-lite-v1:0"). Try an exact match first, then retry
    with the "bedrock/" prefix stripped, so a canonical id resolves whether
    or not the caller included the prefix.
    """
    if model in litellm.model_cost:
        return model
    if model.startswith("bedrock/"):
        stripped = model.removeprefix("bedrock/")
        if stripped in litellm.model_cost:
            return stripped
    raise KeyError(model)


def resolve_price(model: str) -> ModelPrice:
    key = _resolve_litellm_key(model)
    entry = litellm.model_cost[key]
    input_per_mtok = entry["input_cost_per_token"] * 1e6
    output_per_mtok = entry["output_cost_per_token"] * 1e6
    cache_read = entry.get("cache_read_input_token_cost")
    cache_write = entry.get("cache_creation_input_token_cost")
    return ModelPrice(
        input_per_mtok=input_per_mtok,
        output_per_mtok=output_per_mtok,
        cache_read_input_per_mtok=(
            input_per_mtok if cache_read is None else cache_read * 1e6
        ),
        cache_write_input_per_mtok=(
            input_per_mtok if cache_write is None else cache_write * 1e6
        ),
        source=f"litellm:{key}",
    )


def run_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    cache_write_input_tokens: int = 0,
) -> float:
    price = resolve_price(model)
    return (
        price.input_per_mtok * input_tokens / 1e6
        + price.output_per_mtok * output_tokens / 1e6
        + price.cache_read_input_per_mtok * cache_read_input_tokens / 1e6
        + price.cache_write_input_per_mtok * cache_write_input_tokens / 1e6
    )


def experiment_cost(usages: list[tuple[str, int, int]]) -> float:
    return sum(
        run_cost(model, input_tokens, output_tokens)
        for model, input_tokens, output_tokens in usages
    )


def _token_detail(details, name: str) -> int:
    if details is None:
        return 0
    value = (
        details.get(name, 0) if isinstance(details, dict) else getattr(details, name, 0)
    )
    return int(value or 0)


def _optional_token_detail(details, name: str) -> int | None:
    if details is None:
        return None
    value = (
        details.get(name) if isinstance(details, dict) else getattr(details, name, None)
    )
    return None if value is None else int(value)


def completion_usage(
    response,
    requested_model: str,
    pricing_model: str,
    stage: str,
    latency_ms: int,
) -> ModelCallUsage:
    usage = response.usage
    total_input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
    details = getattr(usage, "prompt_tokens_details", None)
    cache_read = _token_detail(details, "cached_tokens")
    cache_write = _token_detail(details, "cache_creation_tokens") or _token_detail(
        details, "cache_write_tokens"
    )
    text_tokens = _optional_token_detail(details, "text_tokens")
    input_tokens = (
        text_tokens
        if text_tokens is not None
        else max(0, total_input_tokens - cache_read - cache_write)
    )
    price = resolve_price(pricing_model)
    return ModelCallUsage(
        stage=stage,
        requested_model=requested_model,
        pricing_model=pricing_model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_input_tokens=cache_read,
        cache_write_input_tokens=cache_write,
        latency_ms=latency_ms,
        estimated_cost_usd=run_cost(
            pricing_model,
            input_tokens,
            output_tokens,
            cache_read_input_tokens=cache_read,
            cache_write_input_tokens=cache_write,
        ),
        pricing_source=price.source,
    )


def cache_static_system_prompt(messages: list[dict]) -> list[dict]:
    """Add one Bedrock prompt-cache checkpoint after stable system text."""
    cached = deepcopy(messages)
    for message in cached:
        if message.get("role") == "system" and message.get("content"):
            message["cache_control"] = {"type": "ephemeral"}
            break
    return cached
