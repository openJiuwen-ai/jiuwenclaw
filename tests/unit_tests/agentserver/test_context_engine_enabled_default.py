"""Tests for context engine enabled default in JiuWenSwarmDeepAdapter."""

from __future__ import annotations

import pytest

from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


class _FakeAbilityManager:
    @staticmethod
    def list():
        return []


class _FakeInstance:
    def __init__(self) -> None:
        self.registered: list[object] = []
        self.unregistered: list[object] = []
        self.ability_manager = _FakeAbilityManager()

    async def register_rail(self, rail: object) -> None:
        self.registered.append(rail)

    async def unregister_rail(self, rail: object) -> None:
        self.unregistered.append(rail)


async def _noop(*_args, **_kwargs) -> None:
    return None


def _make_adapter(config_cache: dict) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = _FakeInstance()
    adapter._config_cache = config_cache
    adapter._context_assemble_rail = None
    adapter._context_assemble_mode = None
    adapter._context_processor_rail = None
    return adapter


def _stub_rail_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    adapter: JiuWenSwarmDeepAdapter,
    processor_rail: object,
) -> None:
    monkeypatch.setattr(adapter, "_build_task_planning_rail", lambda: None)
    monkeypatch.setattr(adapter, "_build_structured_ask_user_rail", lambda: None)
    monkeypatch.setattr(adapter, "_handle_memory_rail_by_config", _noop)
    monkeypatch.setattr(adapter, "_handle_external_memory_rail_by_config", _noop)
    monkeypatch.setattr(
        interface_deep_module,
        "_build_context_assemble_rail",
        lambda: object(),
    )
    monkeypatch.setattr(
        interface_deep_module,
        "_build_context_processor_rail",
        lambda _config: processor_rail,
    )


@pytest.mark.asyncio
async def test_context_processor_rail_enabled_when_key_missing(monkeypatch):
    processor_rail = object()
    adapter = _make_adapter({})
    _stub_rail_dependencies(monkeypatch, adapter, processor_rail)

    await adapter._update_agent_rails()

    assert adapter._context_processor_rail is processor_rail
    assert processor_rail in adapter._instance.registered


@pytest.mark.asyncio
async def test_context_processor_rail_respects_explicit_disabled(monkeypatch):
    processor_rail = object()
    adapter = _make_adapter({"context_engine_config": {"enabled": False}})
    _stub_rail_dependencies(monkeypatch, adapter, processor_rail)

    await adapter._update_agent_rails()

    assert adapter._context_processor_rail is None
    assert processor_rail not in adapter._instance.registered
