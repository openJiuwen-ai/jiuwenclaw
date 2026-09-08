# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Behavior characterization for the AgentServer ``session.fork`` boundary."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_unary
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer


def _request(*, target_session_id: str | None = "fork-target") -> AgentRequest:
    params = {
        "source_session_id": "fork-source",
        "title": "Forked session",
    }
    if target_session_id is not None:
        params["target_session_id"] = target_session_id
    return AgentRequest(
        request_id="fork-request",
        channel_id="tui",
        session_id="fork-source",
        req_method=ReqMethod.SESSION_FORK,
        params=params,
        metadata={"trace_id": "fork-characterization"},
    )


def _fork_result(target_session_id: str = "fork-target") -> dict[str, Any]:
    return {
        "session_id": target_session_id,
        "source_session_id": "fork-source",
        "title": "Forked session",
    }


def _server(runtime: Any, manager: Any) -> AgentWebSocketServer:
    server = object.__new__(AgentWebSocketServer)
    server._runtime = runtime
    server._agent_manager = manager
    return server


def _parse_sent_response(ws: Any) -> Any:
    ws.send.assert_awaited_once()
    return parse_agent_server_wire_unary(json.loads(ws.send.await_args.args[0]))


@pytest.mark.asyncio
async def test_session_fork_live_agent_preserves_context_state_and_wire_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    trace: list[str] = []
    card = object()
    deep_agent = SimpleNamespace(card=card)

    async def start_runtime() -> None:
        trace.append("runtime.start")

    async def ensure_instance() -> Any:
        trace.append("agent.ensure")
        return deep_agent

    def get_agent_nowait(channel_id: str) -> Any:
        assert channel_id == "tui"
        trace.append("agent.lookup")
        return SimpleNamespace(ensure_instance=ensure_instance)

    def fork_session(**kwargs: Any) -> dict[str, Any]:
        assert kwargs == {
            "source_session_id": "fork-source",
            "target_session_id": "fork-target",
            "title": "Forked session",
            "channel_id": "tui",
        }
        trace.append("fork.filesystem")
        return _fork_result()

    async def copy_session_context(
        selected_agent: Any,
        source_session_id: str,
        target_session_id: str,
    ) -> bool:
        assert selected_agent is deep_agent
        assert source_session_id == "fork-source"
        assert target_session_id == "fork-target"
        trace.append("fork.context")
        return True

    async def copy_session_state(**kwargs: Any) -> bool:
        assert kwargs == {
            "source_session_id": "fork-source",
            "target_session_id": "fork-target",
            "card": card,
            "deep_agent": deep_agent,
        }
        trace.append("fork.state")
        return True

    manager = SimpleNamespace(get_agent_nowait=get_agent_nowait)
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(side_effect=start_runtime),
        create_or_resume_session=AsyncMock(),
    )
    ws = SimpleNamespace(
        send=AsyncMock(side_effect=lambda _wire: trace.append("response.send"))
    )
    monkeypatch.setattr(session_ops_service, "fork_session", fork_session)
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_context",
        copy_session_context,
    )
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        copy_session_state,
    )

    await _server(runtime, manager)._handle_session_fork(
        ws,
        _request(),
        asyncio.Lock(),
    )

    assert trace == [
        "runtime.start",
        "fork.filesystem",
        "agent.lookup",
        "agent.ensure",
        "fork.context",
        "fork.state",
        "response.send",
    ]
    response = _parse_sent_response(ws)
    assert response.ok is True
    assert response.payload == _fork_result()
    assert response.metadata is None


@pytest.mark.asyncio
@pytest.mark.parametrize("best_effort_stage", ["context", "state"])
async def test_session_fork_context_and_state_false_remain_best_effort(
    monkeypatch: pytest.MonkeyPatch,
    best_effort_stage: str,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    trace: list[str] = []
    deep_agent = SimpleNamespace(card=object())
    agent = SimpleNamespace(ensure_instance=AsyncMock(return_value=deep_agent))
    manager = SimpleNamespace(get_agent_nowait=MagicMock(return_value=agent))
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(),
        create_or_resume_session=AsyncMock(),
    )
    ws = SimpleNamespace(send=AsyncMock())

    def fork_session(**_kwargs: Any) -> dict[str, Any]:
        trace.append("fork.filesystem")
        return _fork_result()

    async def copy_session_context(*_args: Any) -> bool:
        trace.append("fork.context")
        return best_effort_stage != "context"

    async def copy_session_state(**_kwargs: Any) -> bool:
        trace.append("fork.state")
        return best_effort_stage != "state"

    monkeypatch.setattr(session_ops_service, "fork_session", fork_session)
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_context",
        copy_session_context,
    )
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        copy_session_state,
    )

    await _server(runtime, manager)._handle_session_fork(
        ws,
        _request(),
        asyncio.Lock(),
    )

    assert trace == ["fork.filesystem", "fork.context", "fork.state"]
    response = _parse_sent_response(ws)
    assert response.ok is True
    assert response.payload == _fork_result()


@pytest.mark.asyncio
async def test_session_fork_auto_target_preserves_allocation_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    trace: list[str] = []

    async def start_runtime() -> None:
        trace.append("runtime.start")

    async def allocate_target(**kwargs: Any) -> str:
        assert kwargs == {"channel_id": "tui", "session_id": None}
        trace.append("runtime.allocate")
        return "allocated-target"

    def fork_session(**kwargs: Any) -> dict[str, Any]:
        assert kwargs["target_session_id"] == "allocated-target"
        trace.append("fork.filesystem")
        return _fork_result("allocated-target")

    async def copy_session_state(**kwargs: Any) -> bool:
        assert kwargs["target_session_id"] == "allocated-target"
        trace.append("fork.state")
        return True

    manager = SimpleNamespace(get_agent_nowait=MagicMock(return_value=None))
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(side_effect=start_runtime),
        create_or_resume_session=AsyncMock(side_effect=allocate_target),
    )
    ws = SimpleNamespace(
        send=AsyncMock(side_effect=lambda _wire: trace.append("response.send"))
    )
    monkeypatch.setattr(session_ops_service, "fork_session", fork_session)
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        copy_session_state,
    )

    await _server(runtime, manager)._handle_session_fork(
        ws,
        _request(target_session_id=None),
        asyncio.Lock(),
    )

    assert trace == [
        "runtime.start",
        "runtime.allocate",
        "fork.filesystem",
        "fork.state",
        "response.send",
    ]
    response = _parse_sent_response(ws)
    assert response.ok is True
    assert response.payload == _fork_result("allocated-target")


@pytest.mark.asyncio
async def test_session_fork_allocation_failure_skips_all_downstream_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    manager = SimpleNamespace(get_agent_nowait=MagicMock(return_value=None))
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(),
        create_or_resume_session=AsyncMock(
            side_effect=RuntimeError("target allocation failed")
        ),
    )
    ws = SimpleNamespace(send=AsyncMock())
    fork_session = MagicMock()
    copy_session_context = AsyncMock()
    copy_session_state = AsyncMock()
    monkeypatch.setattr(session_ops_service, "fork_session", fork_session)
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_context",
        copy_session_context,
    )
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        copy_session_state,
    )

    await _server(runtime, manager)._handle_session_fork(
        ws,
        _request(target_session_id=None),
        asyncio.Lock(),
    )

    fork_session.assert_not_called()
    manager.get_agent_nowait.assert_not_called()
    copy_session_context.assert_not_awaited()
    copy_session_state.assert_not_awaited()
    response = _parse_sent_response(ws)
    assert response.ok is False
    assert response.payload == {"error": "target allocation failed"}
    assert response.metadata is None


@pytest.mark.asyncio
async def test_session_fork_generic_exception_keeps_legacy_error_wire(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    manager = SimpleNamespace(get_agent_nowait=MagicMock(return_value=None))
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(),
        create_or_resume_session=AsyncMock(),
    )
    ws = SimpleNamespace(send=AsyncMock())
    copy_session_state = AsyncMock()
    monkeypatch.setattr(
        session_ops_service,
        "fork_session",
        MagicMock(side_effect=RuntimeError("fork exploded")),
    )
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        copy_session_state,
    )

    await _server(runtime, manager)._handle_session_fork(
        ws,
        _request(),
        asyncio.Lock(),
    )

    manager.get_agent_nowait.assert_not_called()
    copy_session_state.assert_not_awaited()
    response = _parse_sent_response(ws)
    assert response.request_id == "fork-request"
    assert response.channel_id == "tui"
    assert response.ok is False
    assert response.payload == {"error": "fork exploded"}
    assert response.metadata is None


@pytest.mark.asyncio
async def test_session_fork_cancelled_state_copy_propagates_without_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.agents.harness.common import session_ops_service

    trace: list[str] = []
    manager = SimpleNamespace(get_agent_nowait=MagicMock(return_value=None))
    runtime = SimpleNamespace(
        agent_manager=manager,
        start=AsyncMock(),
        create_or_resume_session=AsyncMock(),
    )
    ws = SimpleNamespace(send=AsyncMock())

    def fork_session(**_kwargs: Any) -> dict[str, Any]:
        trace.append("fork.filesystem")
        return _fork_result()

    async def cancel_state_copy(**_kwargs: Any) -> None:
        trace.append("fork.state.cancel")
        raise asyncio.CancelledError

    monkeypatch.setattr(session_ops_service, "fork_session", fork_session)
    monkeypatch.setattr(
        session_ops_service,
        "copy_session_state",
        cancel_state_copy,
    )

    with pytest.raises(asyncio.CancelledError):
        await _server(runtime, manager)._handle_session_fork(
            ws,
            _request(),
            asyncio.Lock(),
        )

    assert trace == ["fork.filesystem", "fork.state.cancel"]
    ws.send.assert_not_awaited()
