from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.gui_rpc.reverse_rpc import (
    GuiReverseRpcPayload,
    GuiReverseRpcResult,
    XIAOYI_GUI_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcRemoteError
from jiuwenswarm.server.gui_rpc.reverse_rpc import XiaoyiGuiReverseRpcClient


def test_gui_reverse_rpc_payload_and_result_round_trip() -> None:
    payload = GuiReverseRpcPayload(
        query="open settings",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
        device_id="device-1",
    )
    assert GuiReverseRpcPayload.from_dict(payload.to_dict()) == payload

    result = GuiReverseRpcResult(rpc_id="rrpc-1", result="done")
    assert GuiReverseRpcResult.from_dict(result.to_dict()) == result


@pytest.mark.asyncio
async def test_typed_gui_client_maps_business_data_to_generic_call() -> None:
    class FakeReverseRpcClient:
        def __init__(self) -> None:
            self.call_args = None

        async def call(self, **kwargs):
            self.call_args = kwargs
            return {"rpc_id": "rrpc-1", "result": "done"}

    generic_client = FakeReverseRpcClient()
    client = XiaoyiGuiReverseRpcClient(generic_client)  # type: ignore[arg-type]
    response = await client.call(
        query="open settings",
        source_request_id="request-1",
        jiuwen_session_id="jiuwen-1",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
        device_id="device-1",
        execution_id="invocation-1",
        app_id="app-1",
        binding_id="binding-1",
        timeout=45.0,
    )

    assert response.success is True
    assert response.rpc_id == "rrpc-1"
    assert response.result == "done"
    call_args = generic_client.call_args
    assert call_args["method"] == XIAOYI_GUI_REVERSE_RPC_METHOD
    assert call_args["timeout"] == 45.0
    assert call_args["remote_cancel"] is True
    assert call_args["origin"].execution_id == "invocation-1"
    assert call_args["origin"].request_id == "request-1"
    assert call_args["origin"].session_id == "jiuwen-1"
    assert call_args["route"].channel_id == "xiaoyi"
    assert call_args["route"].app_id == "app-1"
    assert call_args["route"].binding_id == "binding-1"
    assert call_args["payload"]["xiaoyi_task_id"] == "xiaoyi-task-1"


@pytest.mark.asyncio
async def test_typed_gui_client_maps_remote_capability_error() -> None:
    class FailingReverseRpcClient:
        async def call(self, **kwargs):
            del kwargs
            raise ReverseRpcRemoteError(
                "GUI_EXECUTION_FAILED",
                "Jarvis failed",
                details={"rpc_id": "rrpc-2"},
            )

    client = XiaoyiGuiReverseRpcClient(  # type: ignore[arg-type]
        FailingReverseRpcClient()
    )
    response = await client.call(
        query="open settings",
        source_request_id="request-1",
        jiuwen_session_id="jiuwen-1",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
    )

    assert response.success is False
    assert response.rpc_id == "rrpc-2"
    assert response.error_code == "GUI_EXECUTION_FAILED"
    assert response.error_message == "Jarvis failed"


@pytest.mark.asyncio
async def test_typed_gui_client_preserves_caller_cancellation() -> None:
    class CancelledReverseRpcClient:
        async def call(self, **kwargs):
            assert kwargs["remote_cancel"] is True
            raise asyncio.CancelledError

    client = XiaoyiGuiReverseRpcClient(  # type: ignore[arg-type]
        CancelledReverseRpcClient()
    )
    with pytest.raises(asyncio.CancelledError):
        await client.call(
            query="open settings",
            source_request_id="request-1",
            jiuwen_session_id="jiuwen-1",
            xiaoyi_session_id="xiaoyi-session-1",
            xiaoyi_task_id="xiaoyi-task-1",
            xiaoyi_message_id="xiaoyi-message-1",
        )
