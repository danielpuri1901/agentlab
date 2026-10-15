"""Anthropic Messages API adapter, shaped like the Bedrock one (agentlab.bedrock).

From 2026-10-15 inference runs on Anthropic API credits and AWS credits pay for
infrastructure only. Models are named "anthropic/<model id>", for example
"anthropic/claude-opus-5-5". The official SDK is imported lazily, like boto3 in
agentlab.bedrock.
"""

import json
import os
import re
from types import SimpleNamespace

from agentlab.bedrock import FIX_ROUND

API_KEY_ENV = "ANTHROPIC_API_KEY"
CACHE = {"type": "ephemeral"}


def api_key() -> str:
    """The key from the environment. ECS hands over the whole secret, which may
    be the bare key or a JSON object holding one key."""
    raw = os.environ.get(API_KEY_ENV, "").strip()
    if raw.startswith("{"):
        values = [str(value).strip() for value in json.loads(raw).values()]
        if len(values) != 1:
            raise ValueError(f"{API_KEY_ENV} as JSON must hold exactly one value")
        raw = values[0]
    if not raw:
        raise ValueError(f"{API_KEY_ENV} is not set")
    return raw


def normalize_api_key_env() -> None:
    """Put the bare key back into the environment, so litellm, which reads the
    variable itself, also works when the secret is stored as JSON."""
    if os.environ.get(API_KEY_ENV, "").strip():
        os.environ[API_KEY_ENV] = api_key()


def _text_blocks(text: str, *, cache_stable_prefix: bool = False) -> list[dict]:
    """Split scene repair prompts so changing feedback stays after the cache."""
    if not cache_stable_prefix or FIX_ROUND not in text:
        return [{"type": "text", "text": text}]
    stable, changing = text.split(FIX_ROUND, 1)
    return [
        {"type": "text", "text": stable, "cache_control": CACHE},
        {"type": "text", "text": FIX_ROUND.lstrip("\n") + changing},
    ]


def _image_block(url: str) -> dict:
    match = re.match(r"data:(image/[a-zA-Z0-9.+-]+);base64,(.*)", url, re.DOTALL)
    if not match:
        raise ValueError("Anthropic image content requires a base64 data URL")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": match.group(1), "data": match.group(2)},
    }


def _content_blocks(content, *, cache_stable_prefix: bool = False) -> list[dict]:
    if isinstance(content, str):
        return _text_blocks(content, cache_stable_prefix=cache_stable_prefix)
    result = []
    for block in content or []:
        if block.get("type") == "text":
            result.append({"type": "text", "text": block.get("text", "")})
        elif block.get("type") == "image_url":
            result.append(_image_block(block["image_url"]["url"]))
        else:
            raise ValueError(f"unsupported content block: {block.get('type')}")
    return result


def build_messages_request(
    messages: list[dict],
    *,
    model: str,
    max_tokens: int,
    extra_fields: dict | None = None,
) -> dict:
    """Map chat messages to one Messages API request.

    The system prompt and the first user message get prompt-cache checkpoints,
    as on Bedrock. `extra_fields` goes into the request as is: the story path
    sends {"output_config": {"effort": ...}} and the deep read can send
    {"thinking": {"type": "between_tools"}}.
    """
    system: list[dict] = []
    api_messages = []
    first_user = True
    for message in messages:
        role = message["role"]
        if role == "system":
            system.extend(_content_blocks(message.get("content", "")))
            continue
        if role not in {"user", "assistant"}:
            raise ValueError(f"unsupported message role: {role}")
        is_first_user = role == "user" and first_user
        blocks = _content_blocks(message.get("content", ""), cache_stable_prefix=is_first_user)
        if is_first_user:
            first_user = False
            if blocks and not any("cache_control" in block for block in blocks):
                blocks[-1] = {**blocks[-1], "cache_control": CACHE}
        api_messages.append({"role": role, "content": blocks})
    request = {
        "model": model.removeprefix("anthropic/"),
        "max_tokens": max_tokens,
        "messages": api_messages,
    }
    if system:
        system[-1] = {**system[-1], "cache_control": CACHE}
        request["system"] = system
    if extra_fields:
        request.update(extra_fields)
    return request


def response_from_message(message):
    """The litellm-like shape the worker and agentlab.costs read."""
    if message.stop_reason == "refusal":
        details = getattr(message, "stop_details", None)
        raise ValueError(f"model refused the request: {getattr(details, 'category', None)}")
    text = "".join(block.text for block in message.content if block.type == "text")
    usage = message.usage
    read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    write = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    finish = "length" if message.stop_reason == "max_tokens" else "stop"
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
        usage=SimpleNamespace(
            prompt_tokens=int(usage.input_tokens or 0) + read + write,
            completion_tokens=int(usage.output_tokens or 0),
            prompt_tokens_details=SimpleNamespace(
                cached_tokens=read, cache_creation_tokens=write
            ),
        ),
    )


def messages_create(
    model: str,
    messages: list[dict],
    *,
    max_tokens: int,
    timeout: int,
    extra_fields: dict | None = None,
):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key(), timeout=timeout, max_retries=2)
    request = build_messages_request(
        messages, model=model, max_tokens=max_tokens, extra_fields=extra_fields
    )
    # Streamed: the story path allows 32000 output tokens, and a non-streamed
    # request that long runs into the SDK's time guard.
    with client.messages.stream(**request) as stream:
        return response_from_message(stream.get_final_message())
