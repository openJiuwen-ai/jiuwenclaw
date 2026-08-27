from types import SimpleNamespace

from openjiuwen.core.foundation.llm import ModelClientConfig, ProviderType

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


def test_constructed_wrapper_model_is_usable_without_top_level_credentials() -> None:
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.IntelliRouter
        ),
        _client=object(),
    )

    assert JiuWenSwarmDeepAdapter._model_looks_usable(model) is True


def test_model_without_a_constructed_client_is_not_usable() -> None:
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.IntelliRouter
        ),
        _client=None,
    )

    assert JiuWenSwarmDeepAdapter._model_looks_usable(model) is False


def test_documentation_placeholder_endpoint_is_not_usable() -> None:
    model = SimpleNamespace(
        model_client_config=ModelClientConfig(
            client_provider=ProviderType.OpenAI,
            api_base="https://example.com/compatible-mode/v1",
            api_key="test-key",
        ),
        _client=object(),
    )

    assert JiuWenSwarmDeepAdapter._model_looks_usable(model) is False
