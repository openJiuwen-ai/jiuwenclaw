from __future__ import annotations

import asyncio
import time

import pytest

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL,
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
)
from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_CANCEL_MESSAGE_TYPE,
    GUI_RPC_REQUEST_MESSAGE_TYPE,
    GuiRpcCancel,
    GuiRpcRequest,
)
from jiuwenswarm.gateway.gui_rpc.dispatcher import XiaoyiGuiRpcDispatcher
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


def _request() -> GuiRpcRequest:
    return GuiRpcRequest(
        message_type=GUI_RPC_REQUEST_MESSAGE_TYPE,
        rpc_id="gui-rpc-1",
        query="open settings",
        source_request_id="request-1",
        jiuwen_session_id="jiuwen-1",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
        device_id=None,
        deadline=time.time() + 10.0,
    )


class FakeAgentClient:
    def __init__(self) -> None:
        self.sent = []

    async def send_request(self, envelope):
        self.sent.append(envelope)


class SuccessfulExecutor:
    async def execute(self, request: GuiRpcRequest) -> str:
        return f"completed:{request.xiaoyi_task_id}"


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def execute(self, request: GuiRpcRequest) -> str:
        self.started.set()
        await asyncio.Event().wait()
        return "unreachable"


@pytest.mark.asyncio
async def test_dispatcher_returns_gui_rpc_response() -> None:
    agent_client = FakeAgentClient()
    dispatcher = XiaoyiGuiRpcDispatcher(agent_client, SuccessfulExecutor())
    request = _request()

    await dispatcher.handle(
        {
            "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
            "body": request.to_dict(),
        }
    )

    assert len(agent_client.sent) == 1
    envelope = agent_client.sent[0]
    assert envelope.method == "xiaoyi.gui_rpc.response"
    assert envelope.params["rpc_id"] == request.rpc_id
    assert envelope.params["success"] is True
    assert dispatcher._executions == {}


@pytest.mark.asyncio
async def test_dispatcher_cancel_is_correlated_and_cleans_execution() -> None:
    agent_client = FakeAgentClient()
    executor = BlockingExecutor()
    dispatcher = XiaoyiGuiRpcDispatcher(agent_client, executor)
    request = _request()
    request_task = asyncio.create_task(
        dispatcher.handle(
            {
                "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
                "body": request.to_dict(),
            }
        )
    )
    await executor.started.wait()

    cancel = GuiRpcCancel(
        message_type=GUI_RPC_CANCEL_MESSAGE_TYPE,
        rpc_id=request.rpc_id,
        reason="tool cancelled",
    )
    await dispatcher.handle(
        {
            "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL,
            "body": cancel.to_dict(),
        }
    )
    await request_task

    assert agent_client.sent[0].params["rpc_id"] == request.rpc_id
    assert agent_client.sent[0].params["success"] is False
    assert agent_client.sent[0].params["error_code"] == "CANCELLED"
    assert dispatcher._executions == {}


@pytest.mark.asyncio
async def test_message_handler_routes_gui_rpc_before_normal_publish() -> None:
    calls = []

    class FakeDispatcher:
        async def handle(self, wire):
            calls.append(wire)

    handler = object.__new__(MessageHandler)
    handler._xiaoyi_gui_rpc_dispatcher = FakeDispatcher()
    handler._xiaoyi_device_handler = None
    wire = {
        "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
        "body": _request().to_dict(),
    }

    await handler._handle_agent_server_push(wire)

    assert calls == [wire]
