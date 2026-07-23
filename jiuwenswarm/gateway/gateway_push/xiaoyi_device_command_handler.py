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
            elif request.intent_name == "GetLoginToken":
                # huawei_id_tool 跨进程桥特判：不走普通 phone tool command（commands wire），
                # 改走 send_login_token_artifact（artifact wire，1:1 复刻
                # xy_channel login-token-tool.ts 第 53-90 行），端侧小艺 App 收到
                # {kind:"getLoginToken"} part 后弹授权 UI。agentserver 侧工具
                # login_token_tool.py 通过 execute_device_command("GetLoginToken", ...)
                # 把请求投到本 handler，由 gateway 进程代为下发（channel 实例只在此进程）。
                result = await _dispatch_login_token_artifact(channel, request)
                response = DeviceCommandResponse(
                    rpc_id=request.rpc_id,
                    operation_id=request.operation_id,
                    ok=True,
                    result=result,
                )
            else:
                if request.context.channel_id == "__cron__":
                    logger.info(
                        "[CRON_DEVICE] phase=GATEWAY_DISPATCH rpc_id=%s "
                        "operation_id=%s intent_name=%s mode=scheduled",
                        request.rpc_id,
                        request.operation_id,
                        request.intent_name,
                    )
                    result = await channel.execute_scheduled_phone_tool_command(
                        request=request
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


async def _dispatch_login_token_artifact(
    channel: Any,
    request: DeviceCommandRequest,
) -> dict[str, Any]:
    """huawei_id_tool 跨进程桥：调 channel.send_login_token_artifact 下发 artifact.

    从 command 取 client_id / skill_name（login_token_tool.py 构造），从 context
    解析 session_id / task_id / message_id（与 XiaoyiChannel.execute_phone_tool_command
    的解析顺序一致），message_id 优先用 xiaoyi_rpc_id（端侧用它关联授权回调，对应
    TS 第 75 行 jsonRpcResponse.id = messageId）。

    Returns:
        {"sent": bool} ——是否至少成功发送到一个活跃 WS 连接。
    """
    command = request.command or {}
    client_id = str(command.get("client_id") or "").strip()
    skill_name = str(command.get("skill_name") or "").strip()
    if not client_id or not skill_name:
        raise RuntimeError(
            "GetLoginToken command missing client_id / skill_name"
        )

    context = request.context
    session_id = (
        context.xiaoyi_root_session_id
        or context.xiaoyi_params_session_id
        or context.jiuwen_session_id
        or ""
    )
    if not session_id:
        raise RuntimeError("Xiaoyi session_id is missing for GetLoginToken")
    task_id = context.xiaoyi_task_id or session_id
    message_id = context.xiaoyi_rpc_id or f"cmd_{request.operation_id}"

    sent = await channel.send_login_token_artifact(
        session_id=session_id,
        task_id=task_id,
        message_id=message_id,
        client_id=client_id,
        skill_name=skill_name,
    )
    if not sent:
        raise RuntimeError("下发授权请求失败：Xiaoyi WebSocket 未连接")
    return {"sent": True}


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
