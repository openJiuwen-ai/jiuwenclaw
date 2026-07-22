from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig

import pytest

from jiuwenswarm.common.e2a.constants import E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY
from jiuwenswarm.integrations.ai4research_subscription.constants import (
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import CodexProviderError
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.agent_adapter.interface import build_user_prompt
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod

pytestmark = [pytest.mark.integration, pytest.mark.system]


class _FakeAsyncA2XRegistryClient:
    instances: list["_FakeAsyncA2XRegistryClient"] = []

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float,
        api_key: str | None,
        ownership_file,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self.api_key = api_key
        self.ownership_file = ownership_file
        self.blank_registrations: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    async def register_blank_agent(
        self,
        dataset: str,
        endpoint: str,
        service_id: str | None = None,
        persistent: bool = True,
    ):
        self.blank_registrations.append(
            {
                "dataset": dataset,
                "endpoint": endpoint,
                "service_id": service_id,
                "persistent": persistent,
            }
        )
        return SimpleNamespace(service_id="blank-service-id")

    async def aclose(self) -> None:
        return None


class _FailingAsyncA2XRegistryClient:
    def __init__(self, **_: object) -> None:
        raise RuntimeError("a2x unavailable")


def _make_config(
    role: str,
    *,
    dataset: str = "",
    endpoint: str = "",
    model_name: str = "",
    provider: str = "OpenAI",
) -> dict:
    model_client_config = {
        "client_provider": provider,
        "api_key": "system-test-key",
        "api_base": "http://fake-a2x.local/v1",
    }
    if model_name:
        model_client_config["model_name"] = model_name
    return {
        "preferred_language": "zh",
        "team": {
            "runtime": {
                "mode": "distributed",
                "role": role,
            }
        },
        "react": {
            "agent_name": "main_agent",
            "workspace_dir": "/tmp/a2x-system-test-workspace",
            "enable_task_loop": True,
            "max_iterations": 3,
            "a2x_registry": {
                "base_url": "http://fake-a2x.local",
                "timeout": 5.0,
                "api_key": "",
                "ownership_file": False,
                "role": role,
                "dataset": dataset,
                "endpoint": endpoint,
            },
        },
        "permissions": {"enabled": True},
        "models": {
            "default": {
                "model_client_config": model_client_config,
            }
        },
    }


def _make_request(session_id: str = "web_a2x_system_test") -> tuple[AgentRequest, dict]:
    query = "只回复 PONG"
    channel = "web"
    language = "zh"
    request = AgentRequest(
        request_id="a2x-system-test-request",
        channel_id=channel,
        session_id=session_id,
        req_method=ReqMethod.CHAT_SEND,
        params={"query": query, "mode": "agent.plan", "files": {}},
        is_stream=False,
        metadata={"source": "a2x_system_test"},
    )
    inputs = {
        "conversation_id": session_id,
        "query": build_user_prompt(query, files={}, channel=channel, language=language),
        "channel": channel,
        "language": language,
    }
    return request, inputs


def _make_fake_model(model_name: str, provider: str) -> MagicMock:
    """Create a fake Model with a valid ModelClientConfig for testing."""
    fake_mcc = ModelClientConfig(
        client_provider="OpenAI",
        api_key="system-test-key",
        api_base="http://fake-a2x.local/v1",
    )
    fake_mcc.client_provider = provider
    if provider == CODEX_PROVIDER_NAME:
        fake_mcc.api_key = ""
        fake_mcc.api_base = ""
    fake_model = MagicMock()
    fake_model.model_client_config = fake_mcc
    fake_model.model_config = SimpleNamespace(model_name=model_name)
    return fake_model


def _mock_create_model(self, config: dict) -> MagicMock:
    """Mirror the production model identity/cache registration."""
    default_model_config = config.get("models", {}).get("default", {})
    react_config = config.get("react", {})
    model_client_config = (
        default_model_config.get("model_client_config")
        or react_config.get("model_client_config")
        or {}
    )
    model_name = (
        model_client_config.get("model_name")
        or react_config.get("model_name")
        or "gpt-4"
    )
    provider = model_client_config.get("client_provider") or "OpenAI"
    fake_model = _make_fake_model(model_name, provider)
    self._model_cache[model_name] = fake_model
    self._model_canonical_key_by_object_id[id(fake_model)] = model_name
    self._default_model_name = model_name
    self._model = fake_model
    self._model_client_config = fake_model.model_client_config
    self._model_request_config = fake_model.model_config
    return fake_model


async def _create_adapter_and_run_chat(
    config_base: dict,
    *,
    expected_error_code: str | None = None,
) -> SimpleNamespace:
    """Create adapter, run one chat turn via interaction attach/send_input path.

    Returns the fake DeepAgent so callers can assert on ``send_input``.
    """

    class _FakeInteractionStream:
        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            yield SimpleNamespace(type="llm_output", payload={"content": "PONG"})

        async def close(self, *, abort_active_round: bool = False) -> None:
            return None

    created_agent = SimpleNamespace(
        card=SimpleNamespace(id="jiuwenswarm", name="main_agent"),
        react_agent=SimpleNamespace(
            set_llm=MagicMock(),
            config=SimpleNamespace(),
        ),
        ensure_initialized=AsyncMock(),
        start=AsyncMock(),
        attach_output=AsyncMock(return_value=_FakeInteractionStream()),
        send_input=AsyncMock(),
        goal_manager=None,
    )
    request, inputs = _make_request()

    with (
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "set_checkpoint", AsyncMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_refresh_multimodal_configs", return_value=None),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_create_model", _mock_create_model),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_get_tool_cards", AsyncMock(return_value=[])),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_build_agent_rails", return_value=[]),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_create_sys_operation", return_value=MagicMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_build_configured_subagents", return_value=(None, False)),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "_update_runtime_config", AsyncMock()),
        patch.object(interface_module.JiuWenSwarmDeepAdapter, "load_user_rails", AsyncMock()),
        patch.object(interface_module, "get_config", return_value=config_base),
        patch.object(interface_module, "init_permission_engine", return_value=None),
        patch.object(interface_module, "create_deep_agent", return_value=created_agent),
        patch.dict("os.environ", {"API_KEY": "system-test-key"}),
    ):
        adapter = JiuWenSwarmDeepAdapter()
        await adapter.create_instance()
        if expected_error_code is None:
            response = await adapter.process_message_impl(request, inputs)
        else:
            with pytest.raises(CodexProviderError) as captured:
                await adapter.process_message_impl(request, inputs)

    if expected_error_code is None:
        assert response.ok is True
        assert response.payload.get("content") == "PONG"
        created_agent.send_input.assert_awaited()
    else:
        assert captured.value.code == expected_error_code
        assert E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY not in request.metadata
        created_agent.send_input.assert_not_awaited()
    return created_agent


@pytest.mark.asyncio
async def test_a2x_teammate_registers_blank_agent_during_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenswarm.agents.harness.team.a2x.client", fake_module)

    await _create_adapter_and_run_chat(
        _make_config(
            "teammate",
            dataset="system_test_dataset",
            endpoint="http://agent.example/ws",
        )
    )

    assert len(_FakeAsyncA2XRegistryClient.instances) == 1
    assert _FakeAsyncA2XRegistryClient.instances[0].blank_registrations == [
        {
            "dataset": "system_test_dataset",
            "endpoint": "http://agent.example/ws",
            "service_id": None,
            "persistent": True,
        }
    ]


@pytest.mark.asyncio
async def test_a2x_teamleader_skips_blank_agent_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenswarm.agents.harness.team.a2x.client", fake_module)

    await _create_adapter_and_run_chat(_make_config("teamleader"))

    assert len(_FakeAsyncA2XRegistryClient.instances) == 1
    assert _FakeAsyncA2XRegistryClient.instances[0].blank_registrations == []


@pytest.mark.asyncio
async def test_a2x_init_failure_does_not_block_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FailingAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenswarm.agents.harness.team.a2x.client", fake_module)

    await _create_adapter_and_run_chat(_make_config("teammate"))


@pytest.mark.asyncio
async def test_a2x_codex_plan_rejected_before_interaction_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(
        sys.modules,
        "jiuwenswarm.agents.harness.team.a2x.client",
        fake_module,
    )

    await _create_adapter_and_run_chat(
        _make_config(
            "teamleader",
            model_name=CODEX_MODEL_ALIAS,
            provider=CODEX_PROVIDER_NAME,
        ),
        expected_error_code="unsupported_consumer",
    )
