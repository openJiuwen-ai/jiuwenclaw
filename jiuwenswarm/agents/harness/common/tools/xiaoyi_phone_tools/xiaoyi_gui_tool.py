# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""小艺 GUI 自动化（xiaoyi_gui_agent）：通过 InvokeJarvisGUIAgent 与设备协同完成屏幕操作."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.common.gui_rpc.reverse_rpc import XIAOYI_GUI_MAX_TIMEOUT_SECONDS
from jiuwenswarm.common.invocation_context import get_current_invocation_context
from jiuwenswarm.common.reverse_rpc.errors import (
    ReverseRpcError,
    ReverseRpcTimeoutError,
    ReverseRpcTransportDisconnected,
)
from jiuwenswarm.common.utils import logger
from jiuwenswarm.server.gui_rpc.reverse_rpc import (
    get_xiaoyi_gui_reverse_rpc_client,
)
from jiuwenswarm.server.xiaoyi_invocation import get_xiaoyi_invocation_extension

from .utils import ToolInputError, format_success_response


def _get_gui_tool_async_lock(channel: Any) -> asyncio.Lock:
    gl = getattr(channel, "gui_tool_lock", None)
    if gl is not None:
        return gl
    inner = getattr(channel, "_gui_tool_lock", None)
    if inner is None:
        inner = asyncio.Lock()
        setattr(channel, "_gui_tool_lock", inner)
    return inner


def _payload_is_gui_final(payload: Dict[str, Any]) -> bool:
    """兼容设备 isFinal 为 bool / 1 / \"true\" 等."""
    v = payload.get("isFinal")
    if v is True:
        return True
    if isinstance(v, (int, float)) and int(v) == 1:
        return True
    if isinstance(v, str) and v.strip().lower() in ("1", "true", "yes"):
        return True
    return False


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


@tool(
    name="xiaoyi_gui_agent",
    description=(
        "通过模拟人在手机屏幕上的交互行为（点击、滑动、输入、页面导航等），自动完成手机APP中的各类任务。\n\n"
        "该工具操作方式类似真实用户在手机上的操作，因此可以完成许多无法通过互联网API实现的任务，例如：\n"
        "- 任务需要真实操作手机APP界面\n"
        "- 数据仅存在于APP内部\n"
        "- 无法通过互联网API获取数据\n"
        "- 需要完成用户行为（签到、关注、购买等）\n"
        "- 需要在APP中发布或发送内容\n"
        "- 需要修改APP或手机设置\n\n"
        "注意事项：\n"
        "- 操作超时时间为3分钟（180秒）\n"
        "- 该工具执行时间较长，请勿重复调用\n"
        "- 该工具执行期间不要执行别的工具调用，必须等到该工具有结果返回或者超时之后才能执行别的操作，"
        "无论是新的文本回复还是下一步的工具调用，在此工具执行期间必须严格等待\n"
        "- 如果超时或失败，最多重试一次\n"
        "- 如果用户指令中包含备忘录读写、日程查看，不需要将这类操作放在query参数中，"
        "需要使用预置的note相关工具与calendar相关工具完成相关操作\n\n"
        "参数 query：自然语言操作指令与期望结果。"
    ),
)
async def xiaoyi_gui_agent(query: str) -> Dict[str, Any]:
    """执行 GUI Agent 指令."""
    if not query or not isinstance(query, str) or not query.strip():
        raise ToolInputError("缺少有效参数 query（非空字符串）")

    query = query.strip()
    invocation = get_current_invocation_context()
    if invocation is None:
        logger.error(
            "[INVOCATION_CTX] MISSING phase=TOOL_READ capability=gui query_len=%s",
            len(query),
        )
        raise RuntimeError(
            "GUI Agent 调用失败 [INVALID_CONTEXT]: 当前 invocation context 不可用"
        )
    logger.info(
        "[INVOCATION_CTX] TOOL_READ capability=gui invocation_id=%s "
        "request_id=%s session_id=%s channel_id=%s asyncio_task_id=%s",
        invocation.invocation_id,
        invocation.request_id,
        invocation.session_id,
        invocation.channel_id,
        id(asyncio.current_task()) if asyncio.current_task() else None,
    )
    if str(invocation.channel_id or "").strip().lower() != "xiaoyi":
        raise RuntimeError(
            "GUI Agent 调用失败 [INVALID_CONTEXT]: 当前调用不是 Xiaoyi channel"
        )
    xiaoyi = get_xiaoyi_invocation_extension(invocation)
    xiaoyi_session_id = _first_text(
        xiaoyi.root_session_id if xiaoyi else None,
        xiaoyi.params_session_id if xiaoyi else None,
        invocation.chat_id,
    )
    xiaoyi_task_id = _first_text(xiaoyi.task_id if xiaoyi else None)
    xiaoyi_message_id = _first_text(xiaoyi.message_id if xiaoyi else None)
    missing = [
        name
        for name, value in (
            ("xiaoyi_session_id", xiaoyi_session_id),
            ("xiaoyi_task_id", xiaoyi_task_id),
            ("xiaoyi_message_id", xiaoyi_message_id),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "GUI Agent 调用失败 [INVALID_CONTEXT]: 当前 Xiaoyi invocation 缺少 "
            + ", ".join(missing)
        )
    logger.info(
        "[GUI_RPC_TRACE] phase=TOOL_CALL_BEGIN source_request_id=%s "
        "jiuwen_session_id=%s channel_id=%s query_len=%s",
        invocation.request_id,
        invocation.session_id,
        invocation.channel_id,
        len(query),
    )
    try:
        response = await get_xiaoyi_gui_reverse_rpc_client().call(
            query=query,
            source_request_id=invocation.request_id,
            jiuwen_session_id=invocation.session_id,
            xiaoyi_session_id=xiaoyi_session_id,
            xiaoyi_task_id=xiaoyi_task_id,
            xiaoyi_message_id=xiaoyi_message_id,
            device_id=_first_text(xiaoyi.device_id if xiaoyi else None),
            execution_id=invocation.invocation_id,
            app_id=_first_text(xiaoyi.app_id if xiaoyi else None),
            binding_id=_first_text(xiaoyi.binding_id if xiaoyi else None),
            timeout=XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
        )
    except ReverseRpcTransportDisconnected as exc:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=TRANSPORT_DISCONNECTED error_type=%s",
            invocation.request_id,
            type(exc).__name__,
        )
        raise RuntimeError(
            "GUI Agent 调用失败 [TRANSPORT_DISCONNECTED]: Gateway 连接已断开"
        ) from exc
    except (ReverseRpcTimeoutError, asyncio.TimeoutError) as exc:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=GUI_TIMEOUT error_type=%s",
            invocation.request_id,
            type(exc).__name__,
        )
        raise RuntimeError(
            "GUI Agent 调用失败 [GUI_TIMEOUT]: 小艺 GUI Agent 操作超时（3 分钟）"
        ) from exc
    except ReverseRpcError as exc:
        error_code = str(getattr(exc, "code", None) or "REVERSE_RPC_ERROR")
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=%s error_type=%s",
            invocation.request_id,
            error_code,
            type(exc).__name__,
        )
        raise RuntimeError(
            f"GUI Agent 调用失败 [{error_code}]: {exc}"
        ) from exc
    except Exception as exc:
        logger.exception(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=INTERNAL_ERROR error_type=%s",
            invocation.request_id,
            type(exc).__name__,
        )
        raise RuntimeError(
            f"GUI Agent 调用失败 [INTERNAL_ERROR]: {exc}"
        ) from exc

    if not response.success:
        error_code = response.error_code or "INTERNAL_ERROR"
        error_message = response.error_message or "GUI RPC execution failed"
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_RESPONSE_FAILED source_request_id=%s "
            "rpc_id=%s error_code=%s",
            invocation.request_id,
            response.rpc_id,
            error_code,
        )
        raise RuntimeError(
            f"GUI Agent 调用失败 [{error_code}]: {error_message}"
        )
    text = response.result or ""
    logger.info(
        "[GUI_RPC_TRACE] phase=TOOL_CALL_COMPLETED source_request_id=%s "
        "rpc_id=%s result_len=%s",
        invocation.request_id,
        response.rpc_id,
        len(text),
    )
    logger.info(
        "[GUI_AGENT_DIAG] phase=TOOL_RESULT_RETURNED "
        "source_request_id=%s rpc_id=%s result=%r",
        invocation.request_id,
        response.rpc_id,
        text,
    )
    return format_success_response(
        {"success": True, "result": text},
        "GUI 操作完成",
    )
