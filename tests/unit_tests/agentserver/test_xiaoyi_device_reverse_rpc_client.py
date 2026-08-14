from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.device_reverse_rpc import (
    XiaoyiDeviceReverseRpcClient,
)
from jiuwenswarm.common.device_rpc.models import DeviceCommandContext
from jiuwenswarm.common.device_rpc.reverse_rpc import (
    DeviceReverseRpcPayload,
    DeviceReverseRpcResult,
    XIAOYI_DEVICE_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcRemoteError


def _context() -> DeviceCommandContext:
    return DeviceCommandContext(
        source_request_id="request-1",
        channel_id="xiaoyi",
        jiuwen_session_id="session-1",
        xiaoyi_root_session_id="root-1",
        xiaoyi_params_session_id="params-1",
        xiaoyi_task_id="task-1",
        xiaoyi_rpc_id="message-1",
        metadata={
            "invocation_id": "invocation-1",
            "app_id": "app-1",
            "binding_id": "binding-1",
        },
    )


def test_device_reverse_rpc_payload_and_result_round_trip() -> None:
    payload = DeviceReverseRpcPayload(
        operation_id="operation-1",
        intent_name="CreateNote",
        command={"title": "test"},
        context=_context(),
    )
    assert DeviceReverseRpcPayload.from_dict(payload.to_dict()) == payload

    result = DeviceReverseRpcResult(
        rpc_id="rrpc-1",
        operation_id="operation-1",
        result={"created": True},
    )
    assert DeviceReverseRpcResult.from_dict(result.to_dict()) == result


@pytest.mark.asyncio
async def test_typed_client_maps_device_data_to_generic_call() -> None:
    class FakeReverseRpcClient:
        def __init__(self) -> None:
            self.call_args = None

        async def call(self, **kwargs):
            self.call_args = kwargs
            operation_id = kwargs["payload"]["operation_id"]
            return {
                "rpc_id": "rrpc-1",
                "operation_id": operation_id,
                "result": {"created": True},
            }

    generic_client = FakeReverseRpcClient()
    client = XiaoyiDeviceReverseRpcClient(generic_client)  # type: ignore[arg-type]
    response = await client.call(
        intent_name="CreateNote",
        command={"title": "test"},
        context=_context(),
        timeout=12.5,
    )

    assert response.ok is True
    assert response.result == {"created": True}
    assert response.rpc_id == "rrpc-1"
    call_args = generic_client.call_args
    assert call_args["method"] == XIAOYI_DEVICE_REVERSE_RPC_METHOD
    assert call_args["timeout"] == 12.5
    assert call_args["remote_cancel"] is False
    assert call_args["origin"].execution_id == "invocation-1"
    assert call_args["origin"].request_id == "request-1"
    assert call_args["route"].channel_id == "xiaoyi"
    assert call_args["route"].app_id == "app-1"
    assert call_args["route"].binding_id == "binding-1"
    assert call_args["payload"]["operation_id"].startswith("xiaoyi_op_")


@pytest.mark.asyncio
async def test_typed_client_maps_remote_capability_error() -> None:
    class FailingReverseRpcClient:
        async def call(self, **kwargs):
            operation_id = kwargs["payload"]["operation_id"]
            raise ReverseRpcRemoteError(
                "CHANNEL_NOT_READY",
                "device is offline",
                details={
                    "rpc_id": "rrpc-2",
                    "operation_id": operation_id,
                },
            )

    client = XiaoyiDeviceReverseRpcClient(  # type: ignore[arg-type]
        FailingReverseRpcClient()
    )
    response = await client.call(
        intent_name="CreateNote",
        command={},
        context=_context(),
    )

    assert response.ok is False
    assert response.rpc_id == "rrpc-2"
    assert response.error_code == "CHANNEL_NOT_READY"
    assert response.error_message == "device is offline"
