from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_code
from jiuwenswarm.server.runtime.agent_adapter.interface_code import (
    JiuwenSwarmCodeAdapter,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    _deep_agent_context_engine_config,
)


def test_deep_agent_context_engine_config_forwards_context_window_tokens():
    config = _deep_agent_context_engine_config(
        {"context_engine_config": {"context_window_tokens": "123456"}}
    )

    assert config.context_window_tokens == 123456


def test_deep_agent_context_engine_config_ignores_invalid_context_window_tokens():
    config = _deep_agent_context_engine_config(
        {"context_engine_config": {"context_window_tokens": "not-a-number"}}
    )

    assert config.context_window_tokens is None


@pytest.mark.asyncio
async def test_code_adapter_forwards_context_window_tokens(tmp_path, monkeypatch):
    config_base = {
        "react": {
            "context_engine_config": {
                "context_window_tokens": "123456",
            },
        },
    }
    monkeypatch.setattr(interface_code, "get_config", lambda: config_base)
    monkeypatch.setattr(interface_code, "get_agent_workspace_dir", lambda: tmp_path)

    created_instance = MagicMock(ensure_initialized=AsyncMock())
    created_instance.deep_config = SimpleNamespace(tool_owner_id=None)
    created_instance.configured_rails.return_value = []
    spec = MagicMock()
    spec.build.return_value = created_instance
    captured: dict = {}

    def build_spec(**kwargs):
        captured.update(kwargs)
        context = SimpleNamespace(
            tool_owner_id="code-window-test",
            artifacts=SimpleNamespace(tools=[]),
        )
        return spec, context

    adapter = JiuwenSwarmCodeAdapter()

    def create_sys_operation():
        adapter._sys_operation_card = MagicMock()
        return MagicMock()

    with (
        patch.object(adapter, "set_checkpoint", AsyncMock()),
        patch.object(adapter, "_skip_own_instance_build", return_value=False),
        patch.object(adapter, "_refresh_multimodal_configs"),
        patch.object(adapter, "_create_model", return_value=object()),
        patch.object(adapter, "_build_agent_rails", return_value=[]),
        patch.object(
            adapter, "_create_sys_operation", side_effect=create_sys_operation
        ),
        patch.object(
            adapter, "_build_configured_subagents", return_value=(None, False)
        ),
        patch.object(adapter, "_seed_runtime_cwd"),
        patch.object(adapter, "_register_mcp_servers_from_config", AsyncMock()),
        patch.object(adapter, "load_user_rails", AsyncMock()),
        patch.object(adapter, "_load_active_packages", AsyncMock()),
        patch.object(
            interface_code.code_agent_spec,
            "convert_code_config_to_deep_agent_spec",
            side_effect=build_spec,
        ),
    ):
        await adapter.create_instance()

    context_config = captured["context_engine_config"]
    assert context_config.context_window_tokens == 123456
