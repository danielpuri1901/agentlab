from types import SimpleNamespace

from agentlab import worker
from agentlab.bedrock import build_converse_request, response_from_converse


def test_build_converse_request_places_cache_points_after_stable_prefixes():
    request = build_converse_request(
        [
            {"role": "system", "content": "stable rules"},
            {
                "role": "user",
                "content": "stable storyboard\n\nThis is a fix round.\nold code\nfeedback",
            },
        ],
        model="bedrock/arn:aws:bedrock:eu-west-1:123:application-inference-profile/x",
        max_tokens=12000,
    )

    assert request["modelId"].endswith("application-inference-profile/x")
    assert request["system"] == [
        {"text": "stable rules"},
        {"cachePoint": {"type": "default"}},
    ]
    assert request["messages"][0]["content"] == [
        {"text": "stable storyboard"},
        {"cachePoint": {"type": "default"}},
        {"text": "This is a fix round.\nold code\nfeedback"},
    ]
    assert request["inferenceConfig"] == {"maxTokens": 12000}


def test_response_from_converse_preserves_cache_usage():
    response = response_from_converse(
        {
            "output": {"message": {"content": [{"text": "answer"}]}},
            "usage": {
                "inputTokens": 120,
                "outputTokens": 8,
                "cacheReadInputTokens": 90,
                "cacheWriteInputTokens": 20,
            },
        }
    )

    assert response.choices[0].message.content == "answer"
    assert response.usage.prompt_tokens == 120
    assert response.usage.completion_tokens == 8
    assert response.usage.prompt_tokens_details.cached_tokens == 90
    assert response.usage.prompt_tokens_details.cache_creation_tokens == 20


def test_worker_uses_bedrock_converse_for_application_profile(monkeypatch):
    calls = []

    def fake_converse(model, messages, *, max_tokens, timeout):
        calls.append((model, messages, max_tokens, timeout))
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=2,
                prompt_tokens_details=SimpleNamespace(
                    cached_tokens=5, cache_creation_tokens=3
                ),
            ),
        )

    monkeypatch.setattr(worker, "bedrock_converse", fake_converse)
    usage = []

    worker._run_completion(
        "bedrock/arn:aws:bedrock:eu-west-1:123:application-inference-profile/x",
        [{"role": "system", "content": "rules"}],
        max_tokens=100,
        timeout=7,
        stage="video",
        usage_sink=usage,
        pricing_model="anthropic.claude-sonnet-4-6",
    )

    assert len(calls) == 1
    assert calls[0][2:] == (100, 7)
    assert usage[0].cache_read_input_tokens == 5
    assert usage[0].cache_write_input_tokens == 3
