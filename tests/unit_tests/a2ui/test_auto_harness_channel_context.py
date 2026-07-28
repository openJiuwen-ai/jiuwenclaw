# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=protected-access

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.common.e2a.constants import E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
    _ModelTurnGate,
)


class _FakeAutoHarnessService:
    def __init__(self) -> None:
        self.model = None

    @staticmethod
    def is_activate_only_request(request, query) -> bool:
        return False

    @staticmethod
    def is_implement_only_request(request, query) -> bool:
        return False

    async def run(
        self,
        request,
        session_id,
        request_id,
        *,
        query="",
        model=None,
        auto_accept=False,
    ):
        self.model = model
        yield AgentResponseChunk(
            request_id=request_id,
            channel_id=request.channel_id,
            payload={"event_type": "chat.final", "content": "done"},
            is_complete=False,
        )


@pytest.mark.asyncio
async def test_auto_harness_syncs_tui_channel_before_service(monkeypatch):
    """AutoHarness must preserve the TUI channel before downstream model rails run."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = SimpleNamespace(
        react_agent=SimpleNamespace(
            set_llm=lambda _model: None,
            config=SimpleNamespace(),
        )
    )
    adapter._is_session_scoped_adapter = True
    adapter._parent_session_id = None
    auto_harness_service = _FakeAutoHarnessService()
    adapter._auto_harness_service = auto_harness_service
    adapter._stream_event_rail = None
    adapter._model_turn_lock = _ModelTurnGate()
    model_name = "a2ui-api-model"
    provider = "OpenAI"
    canonical_model_key = f"{model_name}#0"
    model = SimpleNamespace(
        model_config=SimpleNamespace(model_name=model_name),
        model_client_config=SimpleNamespace(client_provider=provider),
    )
    adapter._model_cache = {canonical_model_key: model}
    adapter._model_canonical_key_by_object_id = {
        id(model): canonical_model_key,
    }

    captured = {}

    async def capture_runtime_config(self, runtime_config):
        captured["channel_id"] = runtime_config.channel_id
        captured["mode"] = runtime_config.mode
        captured["session_id"] = runtime_config.session_id

    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_has_valid_model_config",
        lambda self, model: True,
    )
    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_resolve_model_for_request",
        lambda self, request: model,
    )
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_update_runtime_config", capture_runtime_config)

    request = AgentRequest(
        request_id="req-auto-harness-tui",
        channel_id="tui",
        session_id="tui_session_1",
        params={"mode": "auto_harness", "query": "run swarmflow"},
    )

    chunks = []
    async for chunk in adapter.process_message_stream_impl(
        request,
        {"query": "run swarmflow", "conversation_id": "tui_session_1", "channel": "tui"},
    ):
        chunks.append(chunk)

    assert captured == {
        "channel_id": "tui",
        "mode": "auto_harness",
        "session_id": "tui_session_1",
    }
    assert auto_harness_service.model is model
    assert request.metadata[E2A_INTERNAL_ACTUAL_MODEL_ROUTE_KEY] == {
        "canonical_model_key": canonical_model_key,
        "provider": provider,
        "source_request_id": request.request_id,
        "mode": request.params["mode"],
    }
    assert chunks[0].payload == {"event_type": "chat.final", "content": "done"}


def test_prompt_channel_resolver_keeps_tui_prefix_non_web():
    """Session-prefix fallback should recognize TUI as a non-Web channel."""
    assert JiuWenSwarmDeepAdapter._resolve_prompt_channel("tui_session_1") == "tui"


@pytest.mark.asyncio
async def test_runtime_config_syncs_channel_and_task_workspace(monkeypatch):
    """Runtime config must sync the channel and task paths into inner rails."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = object()
    adapter._is_session_scoped_adapter = True
    adapter._parent_session_id = None
    adapter._project_dir = None
    adapter._workspace_dir = "/tmp"
    adapter._runtime_prompt_rail = None
    adapter._circuit_breaker_rail = None

    captured = {}

    class _ResponseRail:
        @staticmethod
        def set_channel(channel):
            captured["channel"] = channel

    async def async_noop(*args, **kwargs):
        return None

    adapter._response_prompt_rail = _ResponseRail()

    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_seed_runtime_cwd",
        lambda self, cwd=None, workspace=None: captured.update(
            cwd=cwd,
            workspace=workspace,
        ),
    )
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_resolve_runtime_language", lambda self: "cn")
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_write_runtime_state", lambda self, **kwargs: None)
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_update_rails_for_mode", async_noop)
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_update_tools_for_mode", async_noop)
    monkeypatch.setattr(JiuWenSwarmDeepAdapter, "_update_session_tools", async_noop)
    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_refresh_acp_runtime_tools",
        lambda self, *args: None,
    )
    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_update_prompt_for_mode",
        lambda self, *args: None,
    )

    await adapter._update_runtime_config(
        JiuWenSwarmDeepAdapter._RuntimeConfig(
            session_id="sess_123",
            mode="agent.fast",
            channel_id="web",
            cwd="/task/project/backend",
            workspace="/task/project",
            project_dir="/different/project",
        )
    )

    assert captured == {
        "channel": "web",
        "cwd": "/task/project/backend",
        "workspace": "/task/project",
    }

    await adapter._update_runtime_config(
        JiuWenSwarmDeepAdapter._RuntimeConfig(
            session_id="sess_123",
            mode="agent.fast",
            channel_id="web",
            project_dir="/task/second-project",
        )
    )

    assert captured == {
        "channel": "web",
        "cwd": "/task/second-project",
        "workspace": "/task/second-project",
    }
