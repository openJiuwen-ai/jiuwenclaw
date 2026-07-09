from __future__ import annotations

import asyncio
import inspect
import uuid
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable

from jiuwenswarm.common.device_rpc.models import (
    DeviceCommandContext,
    DeviceCommandRequest,
    DeviceCommandResponse,
)
from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_XIAOYI_DEVICE_COMMAND_REQUEST,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_WIRE_SERVER_PUSH_KEY,
)


@dataclass
class PendingDeviceCommand:
    request: DeviceCommandRequest
    future: asyncio.Future[DeviceCommandResponse]


SendPushCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


def build_device_command_push(request: DeviceCommandRequest) -> dict[str, Any]:
    return {
        "request_id": f"device_push_{request.rpc_id}",
        "response_kind": E2A_RESPONSE_KIND_XIAOYI_DEVICE_COMMAND_REQUEST,
        "is_final": False,
        "status": E2A_RESPONSE_STATUS_IN_PROGRESS,
        "body": {
            "rpc_id": request.rpc_id,
            "operation_id": request.operation_id,
            "intent_name": request.intent_name,
            "command": request.command,
            "context": asdict(request.context),
            "timeout_seconds": request.timeout_seconds,
        },
        "channel_id": "xiaoyi",
        "session_id": request.context.jiuwen_session_id,
        "metadata": {E2A_WIRE_SERVER_PUSH_KEY: True},
    }


class DeviceCommandManager:
    def __init__(self) -> None:
        self._pending: dict[str, PendingDeviceCommand] = {}
        self._send_push_callback: SendPushCallback | None = None

    def set_send_push_callback(self, callback: SendPushCallback) -> None:
        self._send_push_callback = callback

    async def call(
        self,
        *,
        intent_name: str,
        command: dict[str, Any],
        context: DeviceCommandContext,
        timeout: float = 60.0,
    ) -> DeviceCommandResponse:
        if self._send_push_callback is None:
            raise RuntimeError("Gateway push callback is not configured")

        rpc_id = f"xiaoyi_rpc_{uuid.uuid4().hex}"
        operation_id = f"xiaoyi_op_{uuid.uuid4().hex}"
        request = DeviceCommandRequest(
            rpc_id=rpc_id,
            operation_id=operation_id,
            intent_name=intent_name,
            command=command,
            context=context,
            timeout_seconds=timeout,
        )
        future = asyncio.get_running_loop().create_future()
        self._pending[rpc_id] = PendingDeviceCommand(request, future)

        try:
            result = self._send_push_callback(build_device_command_push(request))
            if inspect.isawaitable(result):
                await result
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(rpc_id, None)

    def complete(self, response: DeviceCommandResponse) -> bool:
        pending = self._pending.pop(response.rpc_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(response)
        return True

    def fail_all(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(exc)


_device_command_manager = DeviceCommandManager()


def get_device_command_manager() -> DeviceCommandManager:
    return _device_command_manager
