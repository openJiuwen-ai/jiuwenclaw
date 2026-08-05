"""_resolve_rewind_agent must prefer the session-scoped DeepAgent used by chat."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def server_cls():
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    return AgentWebSocketServer


def _returning(value):
    """Build an async stand-in for the wrapper's ``ensure_instance``.

    The root DeepAgent is built on demand now, so the rewind path awaits it
    instead of reading a plain accessor.
    """

    async def _ensure_instance():
        return value

    return _ensure_instance


def test_resolve_rewind_agent_prefers_session_scoped_instance(server_cls):
    root_deep = MagicMock(name="root_deep")
    root_deep.react_agent = MagicMock(name="root_react")

    session_deep = MagicMock(name="session_deep")
    session_deep.react_agent = MagicMock(name="session_react")

    session_adapter = SimpleNamespace(_instance=session_deep)
    root_adapter = SimpleNamespace(
        _is_session_scoped_adapter=False,
        _instance=root_deep,
        _get_cached_session_adapter=lambda sid: session_adapter if sid == "sess-1" else None,
        apply_sandbox_runtime_patch=lambda *a, **k: None,
    )

    agent = SimpleNamespace(_adapter=root_adapter)
    agent.get_instance = lambda: root_deep
    agent.ensure_instance = _returning(root_deep)

    server = MagicMock()
    server._agent_manager = MagicMock()
    server._agent_manager.get_agent_nowait.return_value = agent
    server._resolve_adapter = server_cls._resolve_adapter

    pair = asyncio.run(server_cls._resolve_rewind_agent(server, "tui", session_id="sess-1"))
    assert pair is not None
    deep, react = pair
    assert deep is session_deep
    assert react is session_deep.react_agent


def test_resolve_rewind_agent_falls_back_to_root_when_no_session_adapter(server_cls):
    root_deep = MagicMock(name="root_deep")
    root_deep.react_agent = MagicMock(name="root_react")

    root_adapter = SimpleNamespace(
        _is_session_scoped_adapter=False,
        _instance=root_deep,
        _get_cached_session_adapter=lambda sid: None,
        apply_sandbox_runtime_patch=lambda *a, **k: None,
    )

    agent = SimpleNamespace(_adapter=root_adapter)
    agent.get_instance = lambda: root_deep
    agent.ensure_instance = _returning(root_deep)

    server = MagicMock()
    server._agent_manager = MagicMock()
    server._agent_manager.get_agent_nowait.return_value = agent
    server._resolve_adapter = server_cls._resolve_adapter

    pair = asyncio.run(server_cls._resolve_rewind_agent(server, "tui", session_id="missing"))
    assert pair is not None
    deep, _react = pair
    assert deep is root_deep
