import litellm
from pydantic import BaseModel

CLAUDE_TOKENIZER_ADJUSTMENT = 1.30
"""Claude 4.7+ models use a newer tokenizer that emits about 30% more tokens
for the same text than earlier Claude generations, so litellm's per-token
price (tokenizer-agnostic) understates real cost for those models.
See docs/research/bedrock-model-pricing.md sections 2.3 and 3."""


class ModelPrice(BaseModel):
    input_per_mtok: float
    output_per_mtok: float
    source: str


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
    if "claude" in model.lower():
        input_per_mtok *= CLAUDE_TOKENIZER_ADJUSTMENT
        output_per_mtok *= CLAUDE_TOKENIZER_ADJUSTMENT
    return ModelPrice(
        input_per_mtok=input_per_mtok,
        output_per_mtok=output_per_mtok,
        source=f"litellm:{key}",
    )


def run_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    price = resolve_price(model)
    return (
        price.input_per_mtok * input_tokens / 1e6
        + price.output_per_mtok * output_tokens / 1e6
    )


def experiment_cost(usages: list[tuple[str, int, int]]) -> float:
    return sum(
        run_cost(model, input_tokens, output_tokens)
        for model, input_tokens, output_tokens in usages
    )
