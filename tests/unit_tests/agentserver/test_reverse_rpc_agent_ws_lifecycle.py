from __future__ import annotations

import asyncio
from typing import Any

import pytest

from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


class _BlockingEmptyWebSocket:
    remote_address = ("127.0.0.1", 19000)

    def __init__(self) -> None:
        self.ack_sent = asyncio.Event()
        self.allow_close = asyncio.Event()

    async def send(self, payload: str) -> None:
        del payload
        self.ack_sent.set()

    def __aiter__(self) -> "_BlockingEmptyWebSocket":
        return self

    async def __anext__(self) -> Any:
        await self.allow_close.wait()
        raise StopAsyncIteration


class _AgentManager:
    async def cancel_all_inflight_work(self, *, reason: str) -> None:
        del reason


class _ReverseRpcClient:
    def __init__(self) -> None:
        self.failures: list[BaseException] = []

    def fail_all(self, exc: BaseException) -> None:
        self.failures.append(exc)


def _server() -> AgentWebSocketServer:
    server = object.__new__(AgentWebSocketServer)
    server._current_ws = None
    server._current_send_lock = None
    server._acp_client_capabilities_by_ws = {}
    server._session_stream_tasks = {}
    server._agent_manager = _AgentManager()
    server._scheduler_service = None
    server._scheduler_agent = None
    server._ping_interval = 30.0
    server._ping_timeout = 300.0
    return server


@pytest.mark.asyncio
async def test_active_gateway_close_fails_reverse_rpc_pending(monkeypatch) -> None:
    client = _ReverseRpcClient()
    monkeypatch.setattr(
        agent_ws_server_module,
        "get_reverse_rpc_client",
        lambda: client,
    )

    async def cancel_team_tasks(*, reason: str) -> None:
        del reason

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.cancel_all_team_stream_tasks_across_managers",
        cancel_team_tasks,
    )
    server = _server()
    ws = _BlockingEmptyWebSocket()

    task = asyncio.create_task(server._connection_handler(ws))
    await ws.ack_sent.wait()
    ws.allow_close.set()
    await task

    assert server._current_ws is None
    assert server._current_send_lock is None
    assert len(client.failures) == 1


@pytest.mark.asyncio
async def test_old_gateway_close_does_not_clear_or_fail_new_connection(
    monkeypatch,
) -> None:
    client = _ReverseRpcClient()
    monkeypatch.setattr(
        agent_ws_server_module,
        "get_reverse_rpc_client",
        lambda: client,
    )

    async def cancel_team_tasks(*, reason: str) -> None:
        del reason

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.cancel_all_team_stream_tasks_across_managers",
        cancel_team_tasks,
    )
    server = _server()
    old_ws = _BlockingEmptyWebSocket()
    task = asyncio.create_task(server._connection_handler(old_ws))
    await old_ws.ack_sent.wait()

    new_ws = object()
    new_lock = asyncio.Lock()
    server._current_ws = new_ws
    server._current_send_lock = new_lock
    client.failures.clear()
    old_ws.allow_close.set()
    await task

    assert server._current_ws is new_ws
    assert server._current_send_lock is new_lock
    assert client.failures == []
