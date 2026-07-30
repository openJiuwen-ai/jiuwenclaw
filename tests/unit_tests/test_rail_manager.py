from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jiuwenswarm.agents.harness.common.plugins import rail_manager as rail_manager_module
from jiuwenswarm.agents.harness.common.plugins.rail_manager import (
    RailExtension,
    RailManager,
)

_RAIL_NAME = "sample_rail"


class _FakeAgent:
    def __init__(self) -> None:
        self.registered: list[Any] = []
        self.unregistered: list[Any] = []

    async def register_rail(self, rail: Any) -> None:
        self.registered.append(rail)

    async def unregister_rail(self, rail: Any) -> None:
        self.unregistered.append(rail)


@pytest.fixture
def rail_manager(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> RailManager:
    monkeypatch.setattr(
        rail_manager_module,
        "get_agent_workspace_dir",
        lambda: tmp_path,
    )
    RailManager._instance = None
    manager = RailManager()
    manager._extensions = {
        _RAIL_NAME: RailExtension(
            name=_RAIL_NAME,
            class_name="SampleRail",
            enabled=True,
        )
    }
    yield manager
    RailManager._instance = None


@pytest.mark.asyncio
async def test_hot_reload_registration_is_scoped_per_agent(
    rail_manager: RailManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[object] = []

    def _fresh(_name: str) -> object:
        rail = object()
        created.append(rail)
        return rail

    monkeypatch.setattr(rail_manager, "create_fresh_rail_instance", _fresh)
    template_agent = _FakeAgent()
    session_agent = _FakeAgent()

    await rail_manager.hot_reload_rail(
        _RAIL_NAME,
        True,
        agent_instance=template_agent,
    )
    await rail_manager.hot_reload_rail(
        _RAIL_NAME,
        True,
        agent_instance=session_agent,
    )
    await rail_manager.hot_reload_rail(
        _RAIL_NAME,
        True,
        agent_instance=session_agent,
    )

    assert len(created) == 2
    assert template_agent.registered == [created[0]]
    assert session_agent.registered == [created[1]]
    assert created[0] is not created[1]
    assert rail_manager.is_rail_registered(
        _RAIL_NAME,
        agent_instance=template_agent,
    )
    assert rail_manager.is_rail_registered(
        _RAIL_NAME,
        agent_instance=session_agent,
    )

    await rail_manager.hot_reload_rail(
        _RAIL_NAME,
        False,
        agent_instance=template_agent,
    )
    assert template_agent.unregistered == [created[0]]
    assert rail_manager.is_rail_registered(_RAIL_NAME)

    await rail_manager.hot_reload_rail(
        _RAIL_NAME,
        False,
        agent_instance=session_agent,
    )
    assert session_agent.unregistered == [created[1]]
    assert not rail_manager.is_rail_registered(_RAIL_NAME)


@pytest.mark.asyncio
async def test_concurrent_agents_do_not_share_hot_reload_target(
    rail_manager: RailManager,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rail_manager,
        "create_fresh_rail_instance",
        lambda _name: object(),
    )
    first = _FakeAgent()
    second = _FakeAgent()

    await asyncio.gather(
        rail_manager.hot_reload_rail(
            _RAIL_NAME,
            True,
            agent_instance=first,
        ),
        rail_manager.hot_reload_rail(
            _RAIL_NAME,
            True,
            agent_instance=second,
        ),
    )

    assert len(first.registered) == 1
    assert len(second.registered) == 1
    assert first.registered[0] is not second.registered[0]
