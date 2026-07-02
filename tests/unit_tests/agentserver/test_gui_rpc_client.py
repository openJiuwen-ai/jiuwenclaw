from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_CANCEL_MESSAGE_TYPE,
    GUI_RPC_REQUEST_MESSAGE_TYPE,
    GUI_RPC_RESPONSE_MESSAGE_TYPE,
    GuiRpcCancel,
    GuiRpcRequest,
    GuiRpcResponse,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.server.gui_rpc.client import (
    GuiRpcClient,
    GuiRpcClientError,
    GuiRpcContextError,
    build_gui_rpc_request,
)


def _agent_request(channel_id: str = "xiaoyi") -> AgentRequest:
    return AgentRequest(
        request_id="request-1",
        channel_id=channel_id,
        session_id="jiuwen-1",
        chat_id="xiaoyi-session-1",
        params={"task_id": "xiaoyi-task-1"},
        metadata={
            "xiaoyi_root_session_id": "xiaoyi-session-1",
            "xiaoyi_task_id": "xiaoyi-task-1",
            "xiaoyi_rpc_id": "xiaoyi-message-1",
            "xiaoyi_device_id": "device-1",
        },
    )


def test_gui_rpc_models_round_trip() -> None:
    request = build_gui_rpc_request(
        query="open settings",
        request=_agent_request(),
        timeout=30.0,
    )
    assert GuiRpcRequest.from_dict(request.to_dict()) == request

    response = GuiRpcResponse(
        message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
        rpc_id=request.rpc_id,
        success=True,
        result="done",
    )
    assert GuiRpcResponse.from_dict(response.to_dict()) == response

    cancel = GuiRpcCancel(
        message_type=GUI_RPC_CANCEL_MESSAGE_TYPE,
        rpc_id=request.rpc_id,
        reason="cancelled",
    )
    assert GuiRpcCancel.from_dict(cancel.to_dict()) == cancel


def test_gui_rpc_request_rejects_non_xiaoyi_context() -> None:
    with pytest.raises(GuiRpcContextError) as exc_info:
        build_gui_rpc_request(
            query="open settings",
            request=_agent_request(channel_id="web"),
            timeout=30.0,
        )
    assert exc_info.value.error_code == "INVALID_CONTEXT"


@pytest.mark.asyncio
async def test_gui_rpc_client_completes_matching_pending() -> None:
    client = GuiRpcClient()
    sent: list[dict] = []

    async def send_push(message: dict) -> None:
        sent.append(message)
        if message["response_kind"] != GUI_RPC_REQUEST_MESSAGE_TYPE:
            return
        rpc_id = message["body"]["rpc_id"]
        assert client.complete(
            GuiRpcResponse(
                message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
                rpc_id=rpc_id,
                success=True,
                result="completed",
            )
        )

    client.set_send_push_callback(send_push)
    response = await client.call(
        query="open settings",
        request=_agent_request(),
        timeout=1.0,
    )

    assert response.result == "completed"
    assert client._pending == {}
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_gui_rpc_client_timeout_sends_cancel_and_cleans_pending() -> None:
    client = GuiRpcClient()
    sent: list[dict] = []

    async def send_push(message: dict) -> None:
        sent.append(message)

    client.set_send_push_callback(send_push)
    with pytest.raises(asyncio.TimeoutError):
        await client.call(
            query="open settings",
            request=_agent_request(),
            timeout=0.01,
        )

    assert client._pending == {}
    assert [item["response_kind"] for item in sent] == [
        GUI_RPC_REQUEST_MESSAGE_TYPE,
        GUI_RPC_CANCEL_MESSAGE_TYPE,
    ]


def test_gui_rpc_client_ignores_unknown_and_duplicate_response() -> None:
    client = GuiRpcClient()
    response = GuiRpcResponse(
        message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
        rpc_id="unknown",
        success=True,
        result="done",
    )
    assert client.complete(response) is False


@pytest.mark.asyncio
async def test_gui_rpc_client_disconnect_fails_and_cleans_pending() -> None:
    client = GuiRpcClient()
    request_sent = asyncio.Event()

    async def send_push(message: dict) -> None:
        request_sent.set()

    client.set_send_push_callback(send_push)
    task = asyncio.create_task(
        client.call(
            query="open settings",
            request=_agent_request(),
            timeout=10.0,
        )
    )
    await request_sent.wait()
    client.fail_all(
        GuiRpcClientError("DEVICE_DISCONNECTED", "Gateway disconnected")
    )

    with pytest.raises(GuiRpcClientError) as exc_info:
        await task

    assert exc_info.value.error_code == "DEVICE_DISCONNECTED"
    assert client._pending == {}
