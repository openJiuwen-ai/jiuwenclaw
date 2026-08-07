# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""小艺 GUI 自动化（xiaoyi_gui_agent）：通过 InvokeJarvisGUIAgent 与设备协同完成屏幕操作."""

from __future__ import annotations

import asyncio
from typing import Any, Dict

from openjiuwen.core.foundation.tool import tool

from jiuwenswarm.common.utils import logger
from jiuwenswarm.server.gui_rpc import get_gui_rpc_client
from jiuwenswarm.server.gui_rpc.client import (
    GUI_RPC_DEFAULT_TIMEOUT_SECONDS,
    GuiRpcClientError,
    GuiRpcContextError,
)
from jiuwenswarm.server.request_context import get_current_agent_request

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


@tool(
    name="xiaoyi_gui_agent",
    description=(
        "通过模拟手机屏幕交互（点击、滑动、输入等）完成仅能在 App 内完成的操作。\n\n"
        "注意：超时约 3 分钟；执行期间勿并行调用其他工具；备忘录/日程请用专用工具而非写入 query。"
        "参数 query：自然语言操作指令与期望结果。"
    ),
)
async def xiaoyi_gui_agent(query: str) -> Dict[str, Any]:
    """执行 GUI Agent 指令."""
    if not query or not isinstance(query, str) or not query.strip():
        raise ToolInputError("缺少有效参数 query（非空字符串）")

    query = query.strip()
    request = get_current_agent_request()
    if request is None:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CONTEXT_MISSING query_len=%s",
            len(query),
        )
        raise RuntimeError(
            "GUI Agent 调用失败 [INVALID_CONTEXT]: 当前请求上下文不可用"
        )
    logger.info(
        "[GUI_RPC_TRACE] phase=TOOL_CALL_BEGIN source_request_id=%s "
        "jiuwen_session_id=%s channel_id=%s query_len=%s",
        request.request_id,
        request.session_id,
        request.channel_id,
        len(query),
    )
    try:
        response = await get_gui_rpc_client().call(
            query=query,
            request=request,
            timeout=GUI_RPC_DEFAULT_TIMEOUT_SECONDS,
        )
    except GuiRpcContextError as exc:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=%s error_type=%s",
            request.request_id,
            exc.error_code,
            type(exc).__name__,
        )
        raise RuntimeError(
            f"GUI Agent 调用失败 [{exc.error_code}]: {exc}"
        ) from exc
    except GuiRpcClientError as exc:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=%s error_type=%s",
            request.request_id,
            exc.error_code,
            type(exc).__name__,
        )
        raise RuntimeError(
            f"GUI Agent 调用失败 [{exc.error_code}]: {exc}"
        ) from exc
    except asyncio.TimeoutError as exc:
        logger.error(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=GUI_TIMEOUT error_type=%s",
            request.request_id,
            type(exc).__name__,
        )
        raise RuntimeError(
            "GUI Agent 调用失败 [GUI_TIMEOUT]: 小艺 GUI Agent 操作超时（3 分钟）"
        ) from exc
    except Exception as exc:
        logger.exception(
            "[GUI_RPC_TRACE] phase=TOOL_CALL_FAILED source_request_id=%s "
            "error_code=INTERNAL_ERROR error_type=%s",
            request.request_id,
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
            request.request_id,
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
        request.request_id,
        response.rpc_id,
        len(text),
    )
    logger.info(
        "[GUI_AGENT_DIAG] phase=TOOL_RESULT_RETURNED "
        "source_request_id=%s rpc_id=%s result=%r",
        request.request_id,
        response.rpc_id,
        text,
    )
    return format_success_response(
        {"success": True, "result": text},
        "GUI 操作完成",
    )
