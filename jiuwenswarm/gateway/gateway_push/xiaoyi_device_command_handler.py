from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict
from typing import Any

from jiuwenswarm.common.device_rpc.models import (
    DeviceCommandContext,
    DeviceCommandRequest,
    DeviceCommandResponse,
)
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    get_xiaoyi_channel,
)

logger = logging.getLogger(__name__)


class XiaoyiDeviceCommandHandler:
    def __init__(self, agent_client: Any) -> None:
        self._agent_client = agent_client

    async def handle(self, data: dict[str, Any]) -> None:
        request = parse_device_command_request(data)
        try:
            channel = get_xiaoyi_channel()
            if channel is None:
                response = build_failure(
                    request,
                    "CHANNEL_NOT_FOUND",
                    "XiaoyiChannel is not active",
                )
            else:
                result = await channel.execute_phone_tool_command(request=request)
                response = DeviceCommandResponse(
                    rpc_id=request.rpc_id,
                    operation_id=request.operation_id,
                    ok=True,
                    result=result,
                )
        except asyncio.TimeoutError:
            response = build_failure(
                request,
                "DEVICE_EXECUTION_TIMEOUT",
                "Timed out waiting for device data-event",
            )
        except Exception as exc:
            logger.exception(
                "[XiaoyiDeviceCommandHandler] execution failed: rpc_id=%s operation_id=%s intent_name=%s",
                request.rpc_id,
                request.operation_id,
                request.intent_name,
            )
            response = build_failure(
                request,
                "DEVICE_EXECUTION_FAILED",
                str(exc),
            )

        await self._send_response(request=request, response=response)

    async def _send_response(
        self,
        *,
        request: DeviceCommandRequest,
        response: DeviceCommandResponse,
    ) -> None:
        envelope = E2AEnvelope(
            request_id=f"device_resp_{uuid.uuid4().hex}",
            method=ReqMethod.XIAOYI_DEVICE_COMMAND_RESPONSE.value,
            channel="xiaoyi",
            session_id=request.context.jiuwen_session_id,
            params=asdict(response),
            is_stream=False,
        )
        await self._agent_client.send_request(envelope)
        logger.info(
            "[XiaoyiDeviceCommandHandler] response sent: rpc_id=%s operation_id=%s ok=%s",
            response.rpc_id,
            response.operation_id,
            response.ok,
        )


def parse_device_command_request(data: dict[str, Any]) -> DeviceCommandRequest:
    body = data.get("body") if isinstance(data.get("body"), dict) else data
    context_raw = body.get("context") if isinstance(body, dict) else {}
    if not isinstance(context_raw, dict):
        context_raw = {}
    context = DeviceCommandContext(
        source_request_id=str(context_raw.get("source_request_id") or ""),
        channel_id=str(context_raw.get("channel_id") or ""),
        jiuwen_session_id=_optional_text(context_raw.get("jiuwen_session_id")),
        xiaoyi_root_session_id=_optional_text(context_raw.get("xiaoyi_root_session_id")),
        xiaoyi_params_session_id=_optional_text(context_raw.get("xiaoyi_params_session_id")),
        xiaoyi_task_id=_optional_text(context_raw.get("xiaoyi_task_id")),
        xiaoyi_rpc_id=_optional_text(context_raw.get("xiaoyi_rpc_id")),
        metadata=dict(context_raw.get("metadata") or {}),
    )
    timeout_raw = body.get("timeout_seconds", 60.0)
    try:
        timeout = float(timeout_raw)
    except (TypeError, ValueError):
        timeout = 60.0
    return DeviceCommandRequest(
        rpc_id=str(body.get("rpc_id") or ""),
        operation_id=str(body.get("operation_id") or ""),
        intent_name=str(body.get("intent_name") or ""),
        command=dict(body.get("command") or {}),
        context=context,
        timeout_seconds=timeout,
    )


def build_failure(
    request: DeviceCommandRequest,
    error_code: str,
    error_message: str,
) -> DeviceCommandResponse:
    return DeviceCommandResponse(
        rpc_id=request.rpc_id,
        operation_id=request.operation_id,
        ok=False,
        error_code=error_code,
        error_message=error_message,
    )


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
