# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.runtime import AgentRuntime, RuntimeStateError
from jiuwenswarm.runtime.plan import PlanStateResult


class FakeAgentManager:
    def __init__(self) -> None:
        self.cancel_calls: list[str] = []
        self.cleanup_calls = 0
        self.session_calls: list[tuple[str, str | None]] = []
        self.cancel_error: Exception | None = None
        self.agent = FakeAgent()
        self.wait_calls: list[str | None] = []
        self.agent_calls: list[dict[str, object]] = []
        self.cleanup_session_calls: list[tuple[str, str]] = []
        self.foreground_calls: list[str] = []

    async def cancel_all_inflight_work(self, reason: str) -> None:
        self.cancel_calls.append(reason)
        if self.cancel_error is not None:
            raise self.cancel_error

    async def cleanup(self) -> None:
        self.cleanup_calls += 1

    async def create_session(
        self,
        channel_id: str = "",
        session_id: str | None = None,
    ) -> str:
        self.session_calls.append((channel_id, session_id))
        return session_id or "process_cli_generated"

    async def wait_for_session_prewarm(self, session_id: str | None) -> None:
        self.wait_calls.append(session_id)

    async def get_agent(self, **kwargs: object) -> object:
        self.agent_calls.append(kwargs)
        return self.agent

    def get_agent_nowait(self, *args: object, **kwargs: object) -> None:
        return None

    async def cleanup_session_runtime(
        self,
        *,
        channel_id: str,
        session_id: str,
    ) -> bool:
        self.cleanup_session_calls.append((channel_id, session_id))
        return True

    async def begin_foreground_chat(self) -> None:
        self.foreground_calls.append("begin")

    async def end_foreground_chat(self) -> None:
        self.foreground_calls.append("end")


class FakeAgent:
    async def process_message(self, request: AgentRequest) -> AgentResponse:
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"event_type": "chat.final", "content": "done"},
        )

    async def process_message_stream(self, request: AgentRequest):
        yield AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"event_type": "chat.delta", "delta": "ok"},
        )
        yield AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"event_type": "chat.final", "content": "ok"},
            is_complete=True,
        )


class FakePlanController:
    def __init__(self) -> None:
        self.reset_calls: list[str] = []

    async def ensure_state(self, *args: object) -> PlanStateResult:
        return PlanStateResult()

    async def check_post_process_exit(
        self,
        *args: object,
    ) -> list[dict[str, object]]:
        return []

    def reset_session(self, session_id: str) -> None:
        self.reset_calls.append(session_id)


@pytest.mark.asyncio
async def test_start_and_close_are_idempotent() -> None:
    manager = FakeAgentManager()
    initialize_calls = 0

    async def initialize() -> None:
        nonlocal initialize_calls
        initialize_calls += 1

    runtime = AgentRuntime(agent_manager=manager, initializer=initialize)

    await runtime.start()
    await runtime.start()
    await runtime.close()
    await runtime.close()

    assert initialize_calls == 1
    assert manager.cancel_calls == ["[runtime close] "]
    assert manager.cleanup_calls == 1
    assert runtime.started is False
    assert runtime.closed is True


@pytest.mark.asyncio
async def test_create_or_resume_session_uses_runtime_manager() -> None:
    manager = FakeAgentManager()

    async def initialize() -> None:
        return None

    runtime = AgentRuntime(agent_manager=manager, initializer=initialize)
    await runtime.start()

    created = await runtime.create_or_resume_session(channel_id="process_cli")
    resumed = await runtime.create_or_resume_session(
        channel_id="process_cli",
        session_id="cli-session-1",
    )

    assert created == "process_cli_generated"
    assert resumed == "cli-session-1"
    assert manager.session_calls == [
        ("process_cli", None),
        ("process_cli", "cli-session-1"),
    ]


@pytest.mark.asyncio
async def test_session_operations_require_started_runtime() -> None:
    runtime = AgentRuntime(agent_manager=FakeAgentManager(), initializer=lambda: None)

    with pytest.raises(RuntimeStateError, match="not started"):
        await runtime.create_or_resume_session(channel_id="process_cli")


@pytest.mark.asyncio
async def test_prepare_chat_turn_uses_runtime_manager() -> None:
    manager = FakeAgentManager()

    async def initialize() -> None:
        return None

    runtime = AgentRuntime(agent_manager=manager, initializer=initialize)
    await runtime.start()
    request = AgentRequest(
        request_id="process-cli-request",
        channel_id="process_cli",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hello", "mode": "agent", "work_mode": "work"},
    )

    mode, sub_mode, agent = await runtime.prepare_chat_turn(
        request,
        "process_cli",
    )

    assert (mode, sub_mode, agent) == ("agent", None, manager.agent)
    assert manager.wait_calls == [None]
    assert manager.agent_calls == [
        {
            "channel_id": "process_cli",
            "mode": "agent",
            "project_dir": None,
            "sub_mode": None,
        }
    ]


@pytest.mark.asyncio
async def test_cancel_and_cleanup_session_are_runtime_operations() -> None:
    manager = FakeAgentManager()

    async def initialize() -> None:
        return None

    runtime = AgentRuntime(agent_manager=manager, initializer=initialize)
    await runtime.start()
    request = AgentRequest(
        request_id="cancel-request",
        channel_id="process_cli",
        session_id="process-cli-session",
        req_method=ReqMethod.CHAT_CANCEL,
        params={},
    )

    response = await runtime.cancel_request(request)
    cleaned = await runtime.cleanup_session(
        channel_id="process_cli",
        session_id="process-cli-session",
    )

    assert response.ok is True
    assert response.payload["event_type"] == "chat.interrupt_result"
    assert cleaned is True
    assert manager.cleanup_session_calls == [
        ("process_cli", "process-cli-session")
    ]


@pytest.mark.asyncio
async def test_stream_uses_selected_existing_agent_and_runtime_events() -> None:
    manager = FakeAgentManager()
    plan = FakePlanController()

    async def initialize() -> None:
        return None

    runtime = AgentRuntime(
        agent_manager=manager,
        initializer=initialize,
        plan_controller=plan,
    )

    async def no_hook(request: AgentRequest) -> None:
        return None

    runtime._trigger_before_chat_request_hook = no_hook
    request = AgentRequest(
        request_id="stream-request",
        channel_id="process_cli",
        session_id=None,
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hello", "mode": "agent", "work_mode": "work"},
    )

    events = [event async for event in runtime.stream(request)]

    assert [event.event_type for event in events] == ["chat.delta", "chat.final"]
    assert events[-1].is_complete is True
    assert manager.foreground_calls == ["begin", "end"]


@pytest.mark.asyncio
async def test_closed_runtime_cannot_restart() -> None:
    manager = FakeAgentManager()

    async def initialize() -> None:
        return None

    runtime = AgentRuntime(agent_manager=manager, initializer=initialize)
    await runtime.close()

    with pytest.raises(RuntimeStateError, match="already closed"):
        await runtime.start()


@pytest.mark.asyncio
async def test_close_still_cleans_up_when_cancel_fails() -> None:
    manager = FakeAgentManager()
    manager.cancel_error = RuntimeError("cancel failed")
    runtime = AgentRuntime(agent_manager=manager)

    with pytest.raises(RuntimeError, match="cancel failed"):
        await runtime.close()

    assert manager.cleanup_calls == 1
    assert runtime.closed is True


def test_agent_server_owns_the_same_runtime_manager() -> None:
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    server = AgentWebSocketServer()

    assert server.get_runtime().agent_manager is server.get_agent_manager()


@pytest.mark.asyncio
async def test_agent_server_cancel_delegates_to_runtime_public_api() -> None:
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    manager = object()
    expected = AgentResponse(
        request_id="cancel-request",
        channel_id="tui",
        payload={"event_type": "chat.interrupt_result", "success": True},
    )
    runtime = SimpleNamespace(
        agent_manager=manager,
        cancel_request=AsyncMock(return_value=expected),
    )
    server = object.__new__(AgentWebSocketServer)
    server._runtime = runtime
    server._agent_manager = manager
    request = AgentRequest(
        request_id="cancel-request",
        channel_id="tui",
        session_id="session-1",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel"},
    )

    response = await server._handle_cancel(
        None,
        request,
        asyncio.Lock(),
        send_response=False,
    )

    assert response is expected
    runtime.cancel_request.assert_awaited_once_with(request, allow_create=False)
