# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""session.create 网关短路测试。

背景：AgentServer 端 session.create 仅回显 session_id（AgentManager.create_session
无副作用，不建目录、不初始化 Agent），而透传路径在 _forward_loop 中串行 await，
且每次转发先做一次企业配置 DB 查询 —— DB 时延波动时大量 session.create 在
_user_messages 队列排队，造成队头阻塞（生产曾积压数小时）。

短路后网关本地构造回显响应（payload 契约同 build_acp_session_new_result），
不再转发 AgentServer。注意：acp/vibeskill 的内部 session ensure 直接调
send_request 不入队，不受影响；session.delete 是真删除，必须继续透传。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from jiuwenclaw.gateway.message_handler import MessageHandler
from jiuwenclaw.schema.agent import AgentResponse
from jiuwenclaw.schema.message import Message, ReqMethod


class _RecordingAgentClient:
    """记录 send_request 调用的最小桩。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def send_request(self, envelope: Any) -> AgentResponse:
        self.calls.append(envelope)
        return AgentResponse(
            request_id=envelope.request_id or "",
            channel_id=envelope.channel or "",
            ok=True,
            payload={"content": "ok"},
        )

    async def send_request_stream(self, envelope: Any):  # pragma: no cover - 测试不触达
        if False:
            yield envelope


class _HangingAgentClient:
    """send_request 永久挂起的桩，模拟 DB 时延波动卡死透传路径。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def send_request(self, envelope: Any) -> AgentResponse:
        self.calls.append(envelope)
        await asyncio.Event().wait()
        raise AssertionError("unreachable")  # pragma: no cover

    async def send_request_stream(self, envelope: Any):  # pragma: no cover - 测试不触达
        if False:
            yield envelope


@pytest.fixture(autouse=True)
def _fresh_message_handler_singleton():
    """MessageHandler 是类级单例（__new__ 复用 _instance）。

    每个用例重置，避免上一个用例的实例（其 asyncio.Queue 绑定在旧事件循环上）
    泄漏到下一个用例导致 "bound to a different event loop"。
    同时准备空 ExtensionRegistry：_forward_loop 对每条消息都会触发
    BEFORE_CHAT_REQUEST 钩子，未初始化的注册表会直接抛 RuntimeError。
    """
    from jiuwenclaw.extensions.registry import ExtensionRegistry
    from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework

    MessageHandler._instance = None
    ExtensionRegistry.create_instance(
        callback_framework=AsyncCallbackFramework(),
        config={},
        logger=logging.getLogger("ut_session_create_short_circuit"),
    )
    yield
    MessageHandler._instance = None
    ExtensionRegistry.reset_instance()


def _session_create_message(session_id: str = "sess-123") -> Message:
    return Message(
        id="req-session-create-1",
        type="req",
        channel_id="web",
        session_id=session_id,
        params={"session_id": session_id},
        timestamp=time.time(),
        ok=True,
        req_method=ReqMethod.SESSION_CREATE,
        is_stream=False,
    )


def _chat_send_message() -> Message:
    return Message(
        id="req-chat-send-1",
        type="req",
        channel_id="web",
        session_id="sess-123",
        params={"query": "hi"},
        timestamp=time.time(),
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=False,
    )


async def _start_forward_loop(handler: MessageHandler) -> asyncio.Task:
    handler._running = True
    return asyncio.create_task(handler._forward_loop())


async def _stop_forward_loop(handler: MessageHandler, task: asyncio.Task) -> None:
    handler._running = False
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def _drain_one_robot_message(handler: MessageHandler) -> Message:
    return await asyncio.wait_for(handler.consume_robot_messages(), timeout=5.0)


@pytest.mark.asyncio
async def test_session_create_short_circuits_without_agent_dispatch() -> None:
    """session.create 不转发 AgentServer，网关本地回显 sessionId。"""
    client = _RecordingAgentClient()
    handler = MessageHandler(client)
    task = await _start_forward_loop(handler)
    try:
        await handler.publish_user_messages(_session_create_message())

        out = await _drain_one_robot_message(handler)

        assert client.calls == [], "session.create 不应转发 AgentServer"
        assert out.ok is True
        assert out.type == "res"
        assert out.id == "req-session-create-1"
        assert out.session_id == "sess-123"
        payload = out.payload or {}
        assert payload.get("sessionId") == "sess-123", "前端契约：回显 sessionId"
        assert payload.get("configOptions") == []
    finally:
        await _stop_forward_loop(handler, task)


@pytest.mark.asyncio
async def test_non_session_create_request_still_dispatches() -> None:
    """对照组：非流式 chat.send 仍走 send_request（短路不误伤其它 method）。"""
    client = _RecordingAgentClient()
    handler = MessageHandler(client)
    task = await _start_forward_loop(handler)
    try:
        await handler.publish_user_messages(_chat_send_message())

        out = await _drain_one_robot_message(handler)

        assert len(client.calls) == 1, "chat.send 应照常转发 AgentServer"
        assert out.ok is True
        assert out.id == "req-chat-send-1"
    finally:
        await _stop_forward_loop(handler, task)


@pytest.mark.asyncio
async def test_session_create_returns_promptly_when_agent_dispatch_hangs() -> None:
    """事故场景：send_request 永久挂死（如 DB 波动）时 session.create 依然秒回。"""
    client = _HangingAgentClient()
    handler = MessageHandler(client)
    task = await _start_forward_loop(handler)
    try:
        await handler.publish_user_messages(_session_create_message())

        out = await asyncio.wait_for(handler.consume_robot_messages(), timeout=2.0)

        assert out.ok is True
        payload = out.payload or {}
        assert payload.get("sessionId") == "sess-123"
    finally:
        await _stop_forward_loop(handler, task)
