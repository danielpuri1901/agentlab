"""Small Bedrock Converse adapter with native prompt-cache checkpoints."""

import base64
import re
from types import SimpleNamespace
from urllib.parse import unquote_to_bytes

_FIX_ROUND = "\n\nThis is a fix round."


def _model_id(model: str) -> str:
    return model.removeprefix("bedrock/")


def _text_blocks(text: str) -> list[dict]:
    """Split scene repair prompts so changing feedback stays after the cache."""
    if _FIX_ROUND not in text:
        return [{"text": text}, {"cachePoint": {"type": "default"}}]
    stable, changing = text.split(_FIX_ROUND, 1)
    return [
        {"text": stable},
        {"cachePoint": {"type": "default"}},
        {"text": _FIX_ROUND.lstrip("\n") + changing},
    ]


def _image_bytes(url: str) -> tuple[str, bytes]:
    match = re.match(r"data:image/([a-zA-Z0-9.+-]+);base64,(.*)", url, re.DOTALL)
    if match:
        return match.group(1).replace("jpeg", "jpg"), base64.b64decode(match.group(2))
    if url.startswith("data:"):
        header, payload = url.split(",", 1)
        media_type = header[5:].split(";", 1)[0]
        return media_type.rsplit("/", 1)[-1], unquote_to_bytes(payload)
    raise ValueError("Bedrock image content requires a data URL")


def _content_blocks(content) -> list[dict]:
    if isinstance(content, str):
        return _text_blocks(content)
    result = []
    for block in content or []:
        if block.get("type") == "text":
            result.append({"text": block.get("text", "")})
        elif block.get("type") == "image_url":
            url = block["image_url"]["url"]
            fmt, data = _image_bytes(url)
            result.append({"image": {"format": fmt, "source": {"bytes": data}}})
        else:
            raise ValueError(f"unsupported content block: {block.get('type')}")
    return result


def build_converse_request(messages: list[dict], *, model: str, max_tokens: int) -> dict:
    system = []
    converse_messages = []
    first_user = True
    for message in messages:
        role = message["role"]
        blocks = _content_blocks(message.get("content", ""))
        if role == "system":
            system.extend(blocks)
        else:
            if role not in {"user", "assistant"}:
                raise ValueError(f"unsupported message role: {role}")
            if role == "user" and first_user:
                first_user = False
                if blocks and not any("cachePoint" in block for block in blocks):
                    blocks.append({"cachePoint": {"type": "default"}})
            converse_messages.append({"role": role, "content": blocks})
    if system and system[-1].get("cachePoint") is None:
        system.append({"cachePoint": {"type": "default"}})
    return {
        "modelId": _model_id(model),
        "system": system,
        "messages": converse_messages,
        "inferenceConfig": {"maxTokens": max_tokens},
    }


def response_from_converse(response: dict):
    output = response.get("output", {}).get("message", {}).get("content", [])
    text = "".join(block.get("text", "") for block in output)
    usage = response.get("usage", {})
    details = SimpleNamespace(
        cached_tokens=int(usage.get("cacheReadInputTokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cacheWriteInputTokens", 0) or 0),
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=int(usage.get("inputTokens", 0) or 0),
            completion_tokens=int(usage.get("outputTokens", 0) or 0),
            prompt_tokens_details=details,
        ),
    )


def converse(model: str, messages: list[dict], *, max_tokens: int, timeout: int):
    import boto3
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name="eu-west-1",
        config=Config(connect_timeout=10, read_timeout=timeout, retries={"max_attempts": 2}),
    )
    response = client.converse(
        **build_converse_request(messages, model=model, max_tokens=max_tokens)
    )
    return response_from_converse(response)
