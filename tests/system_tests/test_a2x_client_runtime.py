from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.foundation.llm.schema.config import ModelClientConfig

import pytest

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


def _make_config(role: str, *, dataset: str = "", endpoint: str = "") -> dict:
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
                "model_client_config": {
                    "api_key": "system-test-key",
                    "api_base": "http://fake-a2x.local/v1",
                }
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


def _make_fake_model() -> MagicMock:
    """Create a fake Model with a valid ModelClientConfig for testing."""
    fake_mcc = ModelClientConfig(
        client_provider="OpenAI",
        api_key="system-test-key",
        api_base="http://fake-a2x.local/v1",
    )
    fake_model = MagicMock()
    fake_model.model_client_config = fake_mcc
    fake_model.model_config = MagicMock()
    return fake_model


def _mock_create_model(self, config: dict) -> MagicMock:
    """Mock _create_model that also sets self._model like the real method does."""
    fake_model = _make_fake_model()
    self._model = fake_model
    return fake_model


async def _create_adapter_and_run_chat(config_base: dict) -> AsyncMock:
    created_agent = SimpleNamespace(card=SimpleNamespace(id="jiuwenswarm", name="main_agent"), ensure_initialized=AsyncMock())
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
        patch.object(
            interface_module.Runner,
            "run_agent",
            AsyncMock(return_value={"output": "PONG"}),
        ) as run_agent_mock,
    ):
        adapter = JiuWenSwarmDeepAdapter()
        await adapter.create_instance()
        response = await adapter.process_message_impl(request, inputs)

    assert response.ok is True
    return run_agent_mock


@pytest.mark.asyncio
async def test_a2x_teammate_registers_blank_agent_during_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _FakeAsyncA2XRegistryClient.instances.clear()
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FakeAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenswarm.agents.harness.team.a2x.client", fake_module)

    run_agent_mock = await _create_adapter_and_run_chat(
        _make_config(
            "teammate",
            dataset="system_test_dataset",
            endpoint="http://agent.example/ws",
        )
    )

    run_agent_mock.assert_called_once()
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

    run_agent_mock = await _create_adapter_and_run_chat(_make_config("teamleader"))

    run_agent_mock.assert_called_once()
    assert len(_FakeAsyncA2XRegistryClient.instances) == 1
    assert _FakeAsyncA2XRegistryClient.instances[0].blank_registrations == []


@pytest.mark.asyncio
async def test_a2x_init_failure_does_not_block_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_module = ModuleType("jiuwenswarm.agents.harness.team.a2x.client")
    fake_module.AsyncA2XRegistryClient = _FailingAsyncA2XRegistryClient
    monkeypatch.setitem(sys.modules, "jiuwenswarm.agents.harness.team.a2x.client", fake_module)

    run_agent_mock = await _create_adapter_and_run_chat(_make_config("teammate"))

    run_agent_mock.assert_called_once()
