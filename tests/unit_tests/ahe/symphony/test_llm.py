from types import SimpleNamespace

import pytest

from jiuwenswarm.symphony.llm import (
    LLMConfig,
    create_llm_client,
    extract_message_content,
    get_llm_token_usage_summary,
    reset_llm_token_usage,
    _record_usage_from_response,
)


class _FakeInvokeModel:
    def __init__(self):
        self.calls = []

    async def invoke(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content='{"ok": true}')


def test_extract_message_content_supports_openjiuwen_response_shape():
    response = SimpleNamespace(content=[{"text": "{\"ok\": true}"}])

    assert extract_message_content(response) == '{"ok": true}'


def test_llm_config_from_default_models(monkeypatch):
    model_config = {
        "models": {
            "defaults": [
                {
                    "model_client_config": {},
                    "model_config_obj": {},
                }
            ]
        }
    }
    monkeypatch.setattr("jiuwenswarm.common.config.get_config", lambda: model_config)
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_default_models",
        lambda config=None: [
            {
                "model_client_config": {
                    "api_key": "key",
                    "api_base": "https://example.test/v1/",
                    "model_name": "model-a",
                    "client_provider": "openai",
                    "custom_headers": {"X-Test": "1"},
                    "timeout": 12,
                    "verify_ssl": False,
                },
                "model_config_obj": {"temperature": 0.2, "top_p": 0.8, "max_tokens": 99},
            }
        ],
    )

    config = LLMConfig.from_default_model()

    assert config.model_client_config["api_key"] == "key"
    assert config.base_url == "https://example.test/v1"
    assert config.model == "model-a"
    assert config.model_client_config["client_provider"] == "openai"
    assert "timeout_seconds" not in LLMConfig.__dataclass_fields__
    assert "max_tokens" not in LLMConfig.__dataclass_fields__
    assert config.temperature == 0.0
    assert config.top_p == 1.0
    assert "batch_size" not in LLMConfig.__dataclass_fields__
    assert not hasattr(config, "timeout_seconds")
    assert not hasattr(config, "max_tokens")
    assert config.model_client_kwargs()["custom_headers"] == {"X-Test": "1"}
    assert config.model_client_kwargs()["timeout"] == 12
    assert config.model_client_kwargs()["verify_ssl"] is False
    assert config.model_request_kwargs()["temperature"] == 0.0
    assert config.model_request_kwargs()["top_p"] == 1.0
    assert config.model_request_kwargs()["max_tokens"] == 99


def test_llm_config_prefers_resolved_default_model(monkeypatch):
    model_config = {
        "models": {
            "defaults": [
                {"model_client_config": {}, "model_config_obj": {}},
                {"model_client_config": {}, "model_config_obj": {}},
            ]
        }
    }
    monkeypatch.setattr("jiuwenswarm.common.config.get_config", lambda: model_config)
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_default_models",
        lambda config=None: [
            {
                "is_default": False,
                "model_client_config": {
                    "api_key": "key-a",
                    "api_base": "https://a.example.test/v1",
                    "model_name": "model-a",
                    "client_provider": "openai",
                },
                "model_config_obj": {},
            },
            {
                "is_default": True,
                "model_client_config": {
                    "api_key": "key-b",
                    "api_base": "https://b.example.test/v1",
                    "model_name": "model-b",
                    "client_provider": "openai",
                },
                "model_config_obj": {},
            },
        ],
    )

    config = LLMConfig.from_default_model()

    assert config.model == "model-b"
    assert config.base_url == "https://b.example.test/v1"


def test_llm_config_does_not_fallback_to_environment_model(monkeypatch):
    monkeypatch.setattr("jiuwenswarm.common.config.get_config", lambda: {"models": {}})
    monkeypatch.setenv("API_KEY", "key")
    monkeypatch.setenv("API_BASE", "https://example.test/v1")
    monkeypatch.setenv("MODEL_NAME", "model-a")

    with pytest.raises(RuntimeError, match="config.yaml"):
        LLMConfig.from_default_model()


def test_create_llm_client_uses_jiuwenswarm_client():
    client = create_llm_client(
        LLMConfig(
            model="model-a",
            model_client_config={
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "client_provider": "openai",
            },
        )
    )

    assert type(client).__name__ == "JiuwenSwarmChatClient"


@pytest.mark.asyncio
async def test_complete_json_async_passes_request_overrides_to_invoke():
    client = create_llm_client(
        LLMConfig(
            model="model-a",
            model_client_config={
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "client_provider": "openai",
            },
        )
    )
    fake_model = _FakeInvokeModel()
    setattr(client, "_model", fake_model)

    result = await client.complete_json_async(
        system_prompt="system",
        user_content="user",
        request_overrides={
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )

    assert result == '{"ok": true}'
    assert "reasoning_effort" not in fake_model.calls[0]
    assert fake_model.calls[0]["extra_body"] == {
        "thinking": {"type": "disabled"}
    }


@pytest.mark.asyncio
async def test_complete_json_async_omits_request_overrides_by_default():
    client = create_llm_client(
        LLMConfig(
            model="model-a",
            model_client_config={
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "client_provider": "openai",
            },
        )
    )
    fake_model = _FakeInvokeModel()
    setattr(client, "_model", fake_model)

    await client.complete_json_async(system_prompt="system", user_content="user")

    assert "reasoning_effort" not in fake_model.calls[0]
    assert "extra_body" not in fake_model.calls[0]


@pytest.mark.asyncio
async def test_complete_json_many_async_passes_request_overrides_to_each_invoke():
    client = create_llm_client(
        LLMConfig(
            model="model-a",
            model_client_config={
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "client_provider": "openai",
            },
        )
    )
    fake_model = _FakeInvokeModel()
    setattr(client, "_model", fake_model)

    results = await client.complete_json_many_async(
        [
            {"system_prompt": "system-a", "user_content": "user-a"},
            {"system_prompt": "system-b", "user_content": "user-b"},
        ],
        request_overrides={
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )

    assert results == ['{"ok": true}', '{"ok": true}']
    assert all("reasoning_effort" not in call for call in fake_model.calls)
    assert [
        call["extra_body"]
        for call in fake_model.calls
    ] == [
        {"thinking": {"type": "disabled"}},
        {"thinking": {"type": "disabled"}},
    ]


@pytest.mark.asyncio
async def test_complete_json_many_async_omits_request_overrides_by_default():
    client = create_llm_client(
        LLMConfig(
            model="model-a",
            model_client_config={
                "api_key": "key",
                "api_base": "https://example.test/v1",
                "client_provider": "openai",
            },
        )
    )
    fake_model = _FakeInvokeModel()
    setattr(client, "_model", fake_model)

    await client.complete_json_many_async(
        [{"system_prompt": "system", "user_content": "user"}],
    )

    assert "reasoning_effort" not in fake_model.calls[0]
    assert "extra_body" not in fake_model.calls[0]


def test_record_usage_supports_openjiuwen_usage_metadata():
    reset_llm_token_usage()
    config = LLMConfig(
        model="model-a",
        model_client_config={
            "api_key": "key",
            "api_base": "https://example.test/v1",
            "client_provider": "openai",
        },
    )
    response = SimpleNamespace(
        usage_metadata=SimpleNamespace(
            input_tokens=12,
            output_tokens=5,
            total_tokens=17,
        )
    )

    try:
        _record_usage_from_response(
            config=config,
            response=response,
            operation="schema_extraction",
        )

        usage = get_llm_token_usage_summary()
        assert usage["total"]["prompt_tokens"] == 12
        assert usage["total"]["completion_tokens"] == 5
        assert usage["total"]["total_tokens"] == 17
        assert usage["records"][0]["source"] == "usage_metadata"
    finally:
        reset_llm_token_usage()
