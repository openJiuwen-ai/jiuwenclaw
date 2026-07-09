from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools import (
    xiaoyi_gui_tool as tool_module,
)
from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_RESPONSE_MESSAGE_TYPE,
    GuiRpcResponse,
)
from jiuwenswarm.gateway.gui_rpc import executor as executor_module
from jiuwenswarm.gateway.gui_rpc.dispatcher import XiaoyiGuiRpcDispatcher
from jiuwenswarm.gateway.gui_rpc.executor import XiaoyiGuiExecutor
from jiuwenswarm.server.gui_rpc.client import GuiRpcClient
from jiuwenswarm.common.schema.agent import AgentRequest


def _request() -> AgentRequest:
    return AgentRequest(
        request_id="request-1",
        channel_id="xiaoyi",
        session_id="jiuwen-1",
        metadata={
            "xiaoyi_root_session_id": "xiaoyi-session-1",
            "xiaoyi_task_id": "xiaoyi-task-1",
            "xiaoyi_rpc_id": "xiaoyi-message-1",
        },
    )


class FakeGuiRpcClient:
    def __init__(self, response: GuiRpcResponse) -> None:
        self.response = response
        self.calls = []

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_xiaoyi_gui_tool_calls_independent_rpc(monkeypatch) -> None:
    client = FakeGuiRpcClient(
        GuiRpcResponse(
            message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
            rpc_id="gui-rpc-1",
            success=True,
            result="opened",
        )
    )
    monkeypatch.setattr(tool_module, "get_current_agent_request", _request)
    monkeypatch.setattr(tool_module, "get_gui_rpc_client", lambda: client)

    result = await tool_module.xiaoyi_gui_agent.invoke({"query": "open settings"})

    assert client.calls[0]["query"] == "open settings"
    assert client.calls[0]["request"].request_id == "request-1"
    assert result["content"][0]["type"] == "text"
    assert "opened" in result["content"][0]["text"]


@pytest.mark.asyncio
async def test_xiaoyi_gui_tool_preserves_remote_error_code(monkeypatch) -> None:
    client = FakeGuiRpcClient(
        GuiRpcResponse(
            message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
            rpc_id="gui-rpc-1",
            success=False,
            error_code="CHANNEL_NOT_READY",
            error_message="not connected",
        )
    )
    monkeypatch.setattr(tool_module, "get_current_agent_request", _request)
    monkeypatch.setattr(tool_module, "get_gui_rpc_client", lambda: client)

    with pytest.raises(RuntimeError, match="CHANNEL_NOT_READY"):
        await tool_module.xiaoyi_gui_agent.invoke({"query": "open settings"})


@pytest.mark.asyncio
async def test_xiaoyi_gui_tool_full_in_memory_rpc_round_trip(monkeypatch) -> None:
    class FakeChannel:
        def __init__(self) -> None:
            self.gui_tool_lock = asyncio.Lock()
            self.is_ready = True
            self.handlers = []

        def register_gui_agent_handler(self, handler) -> None:
            self.handlers.append(handler)

        def unregister_gui_agent_handler(self, handler) -> None:
            self.handlers.remove(handler)

        async def send_xiaoyi_phone_tools_command(self, **kwargs) -> bool:
            item = {
                "_xiaoyi_session_id": kwargs["session_id"],
                "payload": {
                    "interactionId": kwargs["task_id"],
                    "isFinal": True,
                    "streamInfo": {"streamContent": "opened settings"},
                },
            }
            for handler in list(self.handlers):
                handler(item)
            return True

    gui_client = GuiRpcClient()

    class ResponseTransport:
        async def send_request(self, envelope) -> None:
            assert gui_client.complete(
                GuiRpcResponse.from_dict(envelope.params)
            )

    dispatcher = XiaoyiGuiRpcDispatcher(
        ResponseTransport(),
        XiaoyiGuiExecutor(),
    )

    async def send_push(message: dict) -> None:
        await dispatcher.handle(message)

    gui_client.set_send_push_callback(send_push)
    channel = FakeChannel()
    monkeypatch.setattr(executor_module, "get_xiaoyi_channel", lambda: channel)
    monkeypatch.setattr(tool_module, "get_current_agent_request", _request)
    monkeypatch.setattr(tool_module, "get_gui_rpc_client", lambda: gui_client)

    result = await tool_module.xiaoyi_gui_agent.invoke(
        {"query": "open settings"}
    )

    assert "opened settings" in result["content"][0]["text"]
    assert gui_client._pending == {}
    assert dispatcher._executions == {}
    assert channel.handlers == []
