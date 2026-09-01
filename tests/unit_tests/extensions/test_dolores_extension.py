"""Regression tests for the dev-stable/Dolores compatibility layer."""

from dataclasses import dataclass
from inspect import signature
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


def test_dolores_runtime_config_uses_dev_stable_merged_baseline(monkeypatch) -> None:
    from jiuwenswarm.extensions.dolores import extension
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter import (
        interface_deep as dolores_interface,
    )

    expected = {"react": {"max_iterations": 100}}
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: expected,
    )
    monkeypatch.setattr(
        dolores_interface,
        "get_config",
        lambda: {"react": {}},
    )

    extension._patch_dolores_runtime_config_baseline()

    assert dolores_interface.get_config() is expected
    assert (
        dolores_interface.get_config._dolores_uses_dev_stable_config_baseline
        is True
    )


def test_root_agent_loops_use_isolated_callback_namespaces() -> None:
    from jiuwenswarm.extensions.dolores.extension import (
        _patch_agent_loop_callback_namespace_isolation,
    )
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.agent_loop import (
        AgentLoop,
    )

    original_init = AgentLoop.__init__
    try:
        _patch_agent_loop_callback_namespace_isolation()
        card = SimpleNamespace(id="jiuwenswarm", name="test")
        common = {
            "card": card,
            "context_engine": object(),
            "system_prompt_builder": object(),
        }

        first = AgentLoop(**common)
        second = AgentLoop(**common)
        explicit = AgentLoop(**common, runtime_id="explicit.subagent.runtime")

        assert first._runtime_id.startswith("jiuwenswarm.instance.")
        assert second._runtime_id.startswith("jiuwenswarm.instance.")
        assert first._runtime_id != second._runtime_id
        assert first._agent_callback_manager.event_namespace == first._runtime_id
        assert second._agent_callback_manager.event_namespace == second._runtime_id
        assert explicit._runtime_id == "explicit.subagent.runtime"
    finally:
        AgentLoop.__init__ = original_init


@pytest.mark.asyncio
async def test_dev_stable_adapter_forwards_authoritative_config_base(monkeypatch) -> None:
    from jiuwenswarm.extensions.dolores import extension
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.interface_deep import (
        DoloresAdapter,
    )

    assert "config_base" in signature(DoloresAdapter.create_instance).parameters
    create_instance = AsyncMock(return_value=None)
    monkeypatch.setattr(DoloresAdapter, "create_instance", create_instance)
    adapter = extension._dolores_create_adapter()
    adapter._instance = SimpleNamespace(
        _deep_config=SimpleNamespace(max_iterations=100, completion_timeout=1800)
    )
    config_base = {"models": {"default": {"client_provider": "warmup"}}}

    await adapter.create_instance(
        {"agent_name": "test"},
        mode="agent",
        sub_mode="plan",
        config_base=config_base,
    )

    create_instance.assert_awaited_once_with(
        {"agent_name": "test"},
        mode="agent",
        sub_mode="plan",
        config_base=config_base,
    )


@pytest.mark.asyncio
async def test_prepare_session_applies_dev_stable_runtime_context() -> None:
    from jiuwenswarm.extensions.dolores import extension

    adapter = extension._dolores_create_adapter()
    update_runtime_config = AsyncMock()
    child = SimpleNamespace(
        _RuntimeConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        _update_runtime_config=update_runtime_config,
    )
    adapter._get_or_create_session_adapter = AsyncMock(return_value=child)

    await adapter.prepare_session(
        session_id="web_session",
        channel_id="web",
        mode="code.normal",
        project_dir="E:/project",
    )

    update_runtime_config.assert_awaited_once()
    runtime_config = update_runtime_config.await_args.args[0]
    assert runtime_config.session_id == "web_session"
    assert runtime_config.channel_id == "web"
    assert runtime_config.mode == "code.normal"
    assert runtime_config.project_dir == "E:/project"
    assert runtime_config.cwd == "E:/project"
    assert runtime_config.workspace == "E:/project"


@pytest.mark.asyncio
async def test_session_child_inherits_authoritative_config_base(monkeypatch) -> None:
    from jiuwenswarm.extensions.dolores.server.runtime.agent_adapter.interface_deep import (
        DoloresAdapter,
    )

    root = DoloresAdapter()
    child = SimpleNamespace(
        create_instance=AsyncMock(),
        start_interaction=AsyncMock(),
    )
    monkeypatch.setattr(root, "_new_session_scoped_adapter", lambda _sid: child)
    config_base = {"react": {"max_iterations": 100}}
    root._config_base_cache = config_base
    root._session_instance_config = {"agent_name": "test"}

    result = await root._get_or_create_session_adapter("web_session")

    assert result is child
    child.create_instance.assert_awaited_once_with(
        {"agent_name": "test"},
        mode="agent",
        sub_mode=None,
        config_base=config_base,
    )
    child.start_interaction.assert_awaited_once_with(session_id="web_session")


def test_compatible_dataclass_copy_warns_once_for_source_only_fields() -> None:
    from jiuwenswarm.extensions.dolores import extension

    @dataclass
    class _StockResponse:
        content: str
        future_field: str

    @dataclass
    class _DoloresResponse:
        content: str

    extension._SCHEMA_FIELD_MISMATCHES_WARNED.clear()
    source = _StockResponse(content="ok", future_field="new")
    with patch.object(extension.logger, "warning") as warning:
        first = extension._copy_compatible_dataclass(source, _DoloresResponse)
        second = extension._copy_compatible_dataclass(source, _DoloresResponse)

    assert first == _DoloresResponse(content="ok")
    assert second == _DoloresResponse(content="ok")
    warning.assert_called_once()
    assert "response schema drift" in warning.call_args.args[0]
    assert warning.call_args.args[-1] == "future_field"
