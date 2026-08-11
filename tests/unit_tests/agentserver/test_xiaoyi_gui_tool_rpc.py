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
from jiuwenswarm.common.reverse_rpc.codec import request_from_wire
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.gateway.gui_rpc.reverse_rpc import (
    register_xiaoyi_gui_reverse_rpc,
)
from jiuwenswarm.gateway.reverse_rpc import (
    CapabilityRegistry,
    ReverseRpcDispatcher,
)
from jiuwenswarm.server.gui_rpc.reverse_rpc import XiaoyiGuiReverseRpcClient
from jiuwenswarm.server.invocation_context_builder import build_invocation_context
from jiuwenswarm.server.reverse_rpc.client import ReverseRpcClient


def _request() -> AgentRequest:
    return AgentRequest(
        request_id="request-1",
        channel_id="xiaoyi",
        session_id="jiuwen-1",
        metadata={
            "xiaoyi_root_session_id": "xiaoyi-session-1",
            "xiaoyi_task_id": "xiaoyi-task-1",
            "xiaoyi_rpc_id": "xiaoyi-message-1",
            "app_id": "app-1",
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
    monkeypatch.setattr(
        tool_module,
        "get_current_invocation_context",
        lambda: build_invocation_context(_request()),
    )
    monkeypatch.setattr(
        tool_module,
        "get_xiaoyi_gui_reverse_rpc_client",
        lambda: client,
    )

    result = await tool_module.xiaoyi_gui_agent.invoke({"query": "open settings"})

    assert client.calls[0]["query"] == "open settings"
    assert client.calls[0]["source_request_id"] == "request-1"
    assert client.calls[0]["execution_id"].startswith("inv_")
    assert client.calls[0]["app_id"] == "app-1"
    assert client.calls[0]["xiaoyi_task_id"] == "xiaoyi-task-1"
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
    monkeypatch.setattr(
        tool_module,
        "get_current_invocation_context",
        lambda: build_invocation_context(_request()),
    )
    monkeypatch.setattr(
        tool_module,
        "get_xiaoyi_gui_reverse_rpc_client",
        lambda: client,
    )

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

    class FakeChannelManager:
        def get_by_key(self, key):
            if key.channel_id == "xiaoyi" and key.app_id == "app-1":
                return channel
            return None

        def get_channels_by_id(self, channel_id):
            return [channel] if channel_id == "xiaoyi" else []

    generic_client = ReverseRpcClient()

    class ResponseTransport:
        async def send(self, response, request) -> None:
            del request
            assert generic_client.complete(response)

    registry = CapabilityRegistry()
    channel = FakeChannel()
    register_xiaoyi_gui_reverse_rpc(registry, FakeChannelManager())
    dispatcher = ReverseRpcDispatcher(
        registry,
        ResponseTransport(),
    )

    class RequestTransport:
        def __init__(self) -> None:
            self.requests = []

        async def send(self, message: dict, route) -> None:
            del route
            self.requests.append(request_from_wire(message))
            await dispatcher.handle(message)

    request_transport = RequestTransport()
    generic_client.set_transport(request_transport)
    gui_client = XiaoyiGuiReverseRpcClient(generic_client)
    monkeypatch.setattr(
        tool_module,
        "get_current_invocation_context",
        lambda: build_invocation_context(_request()),
    )
    monkeypatch.setattr(
        tool_module,
        "get_xiaoyi_gui_reverse_rpc_client",
        lambda: gui_client,
    )

    result = await tool_module.xiaoyi_gui_agent.invoke(
        {"query": "open settings"}
    )

    assert "opened settings" in result["content"][0]["text"]
    assert generic_client.registry.pending_count() == 0
    assert dispatcher.execution_count == 0
    assert channel.handlers == []
    assert request_transport.requests[0].method == "xiaoyi.gui.execute"
