"""The Anthropic API adapter, offline: requests are built and responses mapped
without the SDK or the network."""

from types import SimpleNamespace

import pytest

from agentlab import anthropic_api
from agentlab.bedrock import FIX_ROUND


def test_request_caches_the_system_prompt_and_the_stable_part_of_a_fix_round():
    request = anthropic_api.build_messages_request(
        [
            {"role": "system", "content": "rules"},
            {"role": "user", "content": "storyboard" + FIX_ROUND + " error: x"},
            {"role": "assistant", "content": "code"},
            {"role": "user", "content": "again"},
        ],
        model="anthropic/claude-opus-5-5",
        max_tokens=32000,
        extra_fields={"output_config": {"effort": "high"}},
    )

    assert request["model"] == "claude-opus-5-5"
    assert request["output_config"] == {"effort": "high"}
    assert request["system"] == [
        {"type": "text", "text": "rules", "cache_control": {"type": "ephemeral"}}
    ]
    first = request["messages"][0]["content"]
    assert first[0] == {"type": "text", "text": "storyboard", "cache_control": {"type": "ephemeral"}}
    assert "cache_control" not in first[1] and first[1]["text"].startswith("This is a fix round.")
    assert request["messages"][2]["content"] == [{"type": "text", "text": "again"}]


def test_request_maps_data_url_images():
    request = anthropic_api.build_messages_request(
        [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUJD"}},
            {"type": "text", "text": "look"},
        ]}],
        model="anthropic/claude-opus-5-5",
        max_tokens=100,
    )

    image, text = request["messages"][0]["content"]
    assert image == {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"}}
    assert text["cache_control"] == {"type": "ephemeral"}


def _message(stop_reason="end_turn", **usage):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="thinking"), SimpleNamespace(type="text", text="hi")],
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=5,
            cache_read_input_tokens=usage.get("read", 0),
            cache_creation_input_tokens=usage.get("write", 0),
        ),
    )


def test_response_keeps_text_only_and_counts_cached_input_as_input():
    response = anthropic_api.response_from_message(_message(read=100, write=20))

    assert response.choices[0].message.content == "hi"
    assert response.choices[0].finish_reason == "stop"
    assert response.usage.prompt_tokens == 130
    assert response.usage.prompt_tokens_details.cached_tokens == 100
    assert response.usage.prompt_tokens_details.cache_creation_tokens == 20


def test_a_cut_off_answer_reads_as_length_and_a_refusal_raises():
    assert anthropic_api.response_from_message(_message("max_tokens")).choices[0].finish_reason == "length"
    with pytest.raises(ValueError, match="refused"):
        anthropic_api.response_from_message(_message("refusal"))


def test_the_key_may_be_stored_bare_or_as_one_entry_json(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", " sk-test ")
    assert anthropic_api.api_key() == "sk-test"
    monkeypatch.setenv("ANTHROPIC_API_KEY", '{"aws-inference": "sk-json"}')
    anthropic_api.normalize_api_key_env()
    assert anthropic_api.api_key() == "sk-json"
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(ValueError, match="not set"):
        anthropic_api.api_key()
