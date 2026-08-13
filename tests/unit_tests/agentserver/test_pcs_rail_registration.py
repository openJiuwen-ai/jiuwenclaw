"""Agent mode registration uses the single Core PCSContextRail."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_deep_adapter_imports_core_pcs_context_rail_only() -> None:
    module = (
        Path(__file__).parents[3]
        / "jiuwenswarm"
        / "server"
        / "runtime"
        / "agent_adapter"
        / "interface_deep.py"
    )
    tree = ast.parse(_source(str(module)))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    imported_names = {
        alias.name
        for node in imports
        for alias in (node.names if isinstance(node, ast.Import) else node.names)
    }

    assert "PCSContextRail" in imported_names
    assert "ProactiveContextRail" not in imported_names
    assert "PCSContextRail" in imported_names


def test_jiuwenswarm_rail_export_points_to_core_rail() -> None:
    from openjiuwen.harness.rails.proactive_context import PCSContextRail

    import jiuwenswarm.agents.harness.common.rails as rails

    assert rails.PCSContextRail is PCSContextRail


@pytest.mark.asyncio
async def test_agent_fast_and_plan_share_one_rail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._is_code_agent = False  # pylint: disable=protected-access
    adapter._instance = _FakeAgent()  # pylint: disable=protected-access
    adapter._proactive_context_rail = None  # pylint: disable=protected-access
    adapter._proactive_context_rail_lock = asyncio.Lock()  # pylint: disable=protected-access

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.PCSContextRail",
        _FakeRail,
        raising=False,
    )
    monkeypatch.setattr(
        adapter,
        "_proactive_context_rail_enabled",
        lambda mode: mode in {"agent.fast", "agent.plan"},
    )

    await adapter._sync_proactive_context_rail("agent.fast")  # pylint: disable=protected-access
    first = adapter._proactive_context_rail  # pylint: disable=protected-access
    await adapter._sync_proactive_context_rail("agent.plan")  # pylint: disable=protected-access

    assert first is adapter._proactive_context_rail  # pylint: disable=protected-access
    assert adapter._instance.registered == [first]  # pylint: disable=protected-access


def _adapter(agent: _FakeAgent) -> JiuWenSwarmDeepAdapter:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._is_code_agent = False  # pylint: disable=protected-access
    adapter._instance = agent  # pylint: disable=protected-access
    adapter._proactive_context_rail = None  # pylint: disable=protected-access
    adapter._proactive_context_rail_lock = asyncio.Lock()  # pylint: disable=protected-access
    return adapter


@pytest.mark.asyncio
async def test_pcs_rail_constructor_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _FakeAgent()
    adapter = _adapter(agent)

    def _failed_constructor(_home: str | Path) -> object:
        raise RuntimeError("construction failed")

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.PCSContextRail",
        _failed_constructor,
    )

    await adapter._sync_proactive_context_rail("agent.fast")  # pylint: disable=protected-access

    assert adapter._proactive_context_rail is None  # pylint: disable=protected-access
    assert await agent.normal_request() == "ok"


@pytest.mark.asyncio
async def test_pcs_rail_registration_and_cleanup_failure_is_fail_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _FakeAgent(fail_register=True, fail_unregister=True)
    adapter = _adapter(agent)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.PCSContextRail",
        _FakeRail,
    )

    await adapter._sync_proactive_context_rail("agent.fast")  # pylint: disable=protected-access

    assert len(agent.register_attempts) == 1
    assert agent.unregister_attempts == agent.register_attempts
    assert adapter._proactive_context_rail is None  # pylint: disable=protected-access
    assert await agent.normal_request() == "ok"


@pytest.mark.asyncio
async def test_pcs_rail_unregister_failure_is_fail_open() -> None:
    rail = _FakeRail(Path("pcs"))
    agent = _FakeAgent(fail_unregister=True)
    agent.registered.append(rail)
    adapter = _adapter(agent)
    adapter._proactive_context_rail = rail  # pylint: disable=protected-access

    await adapter._sync_proactive_context_rail("code.normal")  # pylint: disable=protected-access

    assert agent.unregister_attempts == [rail]
    assert adapter._proactive_context_rail is None  # pylint: disable=protected-access
    assert await agent.normal_request() == "ok"


@pytest.mark.asyncio
async def test_pcs_rail_cancellation_still_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _FakeAgent(cancel_register=True)
    adapter = _adapter(agent)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.PCSContextRail",
        _FakeRail,
    )

    with pytest.raises(asyncio.CancelledError):
        await adapter._sync_proactive_context_rail("agent.fast")  # pylint: disable=protected-access


class _FakeAgent:
    def __init__(
        self,
        *,
        fail_register: bool = False,
        fail_unregister: bool = False,
        cancel_register: bool = False,
    ) -> None:
        self.registered: list[object] = []
        self.register_attempts: list[object] = []
        self.unregister_attempts: list[object] = []
        self.fail_register = fail_register
        self.fail_unregister = fail_unregister
        self.cancel_register = cancel_register

    async def register_rail(self, rail: object) -> None:
        self.register_attempts.append(rail)
        self.registered.append(rail)
        if self.cancel_register:
            raise asyncio.CancelledError
        if self.fail_register:
            raise RuntimeError("register failed")

    async def unregister_rail(self, rail: object) -> None:
        self.unregister_attempts.append(rail)
        if self.fail_unregister:
            raise RuntimeError("unregister failed")
        if rail in self.registered:
            self.registered.remove(rail)

    async def normal_request(self) -> str:
        return "ok"


class _FakeRail:
    def __init__(self, home: str | Path) -> None:
        self.home = Path(home)

    def set_language(self, language: str) -> None:
        del language
