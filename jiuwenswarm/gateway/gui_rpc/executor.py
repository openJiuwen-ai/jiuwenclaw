from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from jiuwenswarm.common.gui_rpc.models import GuiRpcRequest
from jiuwenswarm.gateway.channel_manager.im_platforms.xiaoyi.xiaoyi_connect import (
    get_xiaoyi_channel,
)

logger = logging.getLogger(__name__)


class GuiExecutionError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


def _payload_is_gui_final(payload: dict[str, Any]) -> bool:
    value = payload.get("isFinal")
    if value is True:
        return True
    if isinstance(value, (int, float)) and int(value) == 1:
        return True
    if isinstance(value, str) and value.strip().lower() in ("1", "true", "yes"):
        return True
    return False


class XiaoyiGuiExecutor:
    async def execute(self, request: GuiRpcRequest) -> str:
        logger.info(
            "[GUI_RPC_TRACE] phase=EXECUTOR_ENTER rpc_id=%s "
            "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
            "device_id=%s deadline_remaining_ms=%s",
            request.rpc_id,
            request.xiaoyi_session_id,
            request.xiaoyi_task_id,
            request.jiuwen_session_id,
            request.device_id,
            max(0, int((request.deadline - time.time()) * 1000)),
        )
        channel = get_xiaoyi_channel()
        if channel is None:
            logger.error(
                "[GUI_RPC_TRACE] phase=CHANNEL_LOOKUP_FAILED rpc_id=%s "
                "reason=no_active_channel",
                request.rpc_id,
            )
            raise GuiExecutionError("NO_ACTIVE_DEVICE", "XiaoyiChannel is not active")
        if not channel.is_ready:
            logger.error(
                "[GUI_RPC_TRACE] phase=CHANNEL_NOT_READY rpc_id=%s "
                "running=%s connection_count=%s",
                request.rpc_id,
                getattr(channel, "_running", None),
                sum(bool(item) for item in getattr(channel, "_ws_connections", {}).values()),
            )
            raise GuiExecutionError(
                "CHANNEL_NOT_READY",
                "XiaoyiChannel has no active device connection",
            )
        remaining = request.deadline - time.time()
        if remaining <= 0:
            raise GuiExecutionError("GUI_TIMEOUT", "GUI RPC deadline has expired")

        lock = channel.gui_tool_lock
        acquired = False
        try:
            logger.info(
                "[GUI_RPC_TRACE] phase=LOCK_WAIT_BEGIN rpc_id=%s "
                "locked=%s remaining_ms=%s",
                request.rpc_id,
                lock.locked(),
                int(remaining * 1000),
            )
            await asyncio.wait_for(lock.acquire(), timeout=remaining)
            acquired = True
            logger.info(
                "[GUI_RPC_TRACE] phase=LOCK_ACQUIRED rpc_id=%s "
                "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
                "device_id=%s",
                request.rpc_id,
                request.xiaoyi_session_id,
                request.xiaoyi_task_id,
                request.jiuwen_session_id,
                request.device_id,
            )
            return await self._execute_locked(channel, request)
        except asyncio.TimeoutError as exc:
            logger.error(
                "[GUI_RPC_TRACE] phase=LOCK_WAIT_TIMEOUT rpc_id=%s",
                request.rpc_id,
            )
            raise GuiExecutionError(
                "GUI_TIMEOUT",
                "Timed out waiting for the Xiaoyi GUI lock",
            ) from exc
        except asyncio.CancelledError as exc:
            logger.warning(
                "[GUI_RPC_TRACE] phase=EXECUTOR_CANCELLED rpc_id=%s",
                request.rpc_id,
            )
            raise GuiExecutionError("CANCELLED", "GUI RPC execution was cancelled") from exc
        finally:
            if acquired:
                lock.release()
                logger.info(
                    "[GUI_RPC_TRACE] phase=LOCK_RELEASED rpc_id=%s",
                    request.rpc_id,
                )

    async def _execute_locked(self, channel: Any, request: GuiRpcRequest) -> str:
        response_ready = asyncio.Event()
        latest_non_empty_content = ""
        response_error: GuiExecutionError | None = None
        missing_interaction_logged = False
        started_at = time.monotonic()

        def on_gui(item: dict[str, Any]) -> None:
            nonlocal latest_non_empty_content, response_error
            nonlocal missing_interaction_logged
            try:
                payload = item.get("payload")
                if not isinstance(payload, dict):
                    logger.warning(
                        "[GUI_RPC_TRACE] phase=HANDLER_FRAME_INVALID rpc_id=%s "
                        "reason=payload_not_object",
                        request.rpc_id,
                    )
                    return
                interaction_id = str(payload.get("interactionId") or "").strip()
                stream_info_raw = payload.get("streamInfo")
                content_raw = (
                    stream_info_raw.get("streamContent")
                    if isinstance(stream_info_raw, dict)
                    else None
                )
                raw_is_final = payload.get("isFinal")
                normalized_is_final = _payload_is_gui_final(payload)
                logger.info(
                    "[GUI_AGENT_DIAG] phase=JARVIS_FRAME_PARSED rpc_id=%s "
                    "raw_is_final=%r raw_is_final_type=%s "
                    "normalized_is_final=%s interaction_id=%s "
                    "expected_interaction_id=%s response_session_id=%s "
                    "expected_session_id=%s stream_content=%r payload=%r item=%r",
                    request.rpc_id,
                    raw_is_final,
                    type(raw_is_final).__name__,
                    normalized_is_final,
                    interaction_id,
                    request.xiaoyi_task_id,
                    str(
                        payload.get("sessionId")
                        or item.get("_xiaoyi_session_id")
                        or ""
                    ),
                    request.xiaoyi_session_id,
                    content_raw,
                    payload,
                    item,
                )
                logger.info(
                    "[GUI_RPC_TRACE] phase=HANDLER_FRAME_RECEIVED rpc_id=%s "
                    "response_interaction_id=%s expected_interaction_id=%s "
                    "is_final=%s content_len=%s response_session_id=%s",
                    request.rpc_id,
                    interaction_id,
                    request.xiaoyi_task_id,
                    _payload_is_gui_final(payload),
                    len(str(content_raw)) if content_raw is not None else 0,
                    str(
                        payload.get("sessionId")
                        or item.get("_xiaoyi_session_id")
                        or ""
                    ),
                )
                if interaction_id:
                    if interaction_id != request.xiaoyi_task_id:
                        logger.info(
                            "[GUI_RPC_TRACE] phase=HANDLER_FRAME_IGNORED "
                            "rpc_id=%s xiaoyi_session_id=%s xiaoyi_task_id=%s "
                            "jiuwen_session_id=%s device_id=%s "
                            "reason=interaction_mismatch",
                            request.rpc_id,
                            request.xiaoyi_session_id,
                            request.xiaoyi_task_id,
                            request.jiuwen_session_id,
                            request.device_id,
                        )
                        return
                elif not missing_interaction_logged:
                    missing_interaction_logged = True
                    logger.warning(
                        "[GUI_RPC_TRACE] phase=HANDLER_FRAME_DEGRADED rpc_id=%s "
                        "xiaoyi_session_id=%s xiaoyi_task_id=%s "
                        "jiuwen_session_id=%s device_id=%s "
                        "reason=missing_interaction_id",
                        request.rpc_id,
                        request.xiaoyi_session_id,
                        request.xiaoyi_task_id,
                        request.jiuwen_session_id,
                        request.device_id,
                    )

                response_session_id = str(
                    payload.get("sessionId")
                    or item.get("_xiaoyi_session_id")
                    or ""
                ).strip()
                if (
                    response_session_id
                    and response_session_id != request.xiaoyi_session_id
                ):
                    logger.info(
                        "[GUI_RPC_TRACE] phase=HANDLER_FRAME_IGNORED rpc_id=%s "
                        "xiaoyi_session_id=%s xiaoyi_task_id=%s "
                        "jiuwen_session_id=%s device_id=%s "
                        "reason=session_mismatch",
                        request.rpc_id,
                        request.xiaoyi_session_id,
                        request.xiaoyi_task_id,
                        request.jiuwen_session_id,
                        request.device_id,
                    )
                    return

                stream_info = payload.get("streamInfo")
                if not isinstance(stream_info, dict):
                    stream_info = {}
                content = stream_info.get("streamContent")
                if content is not None and str(content):
                    latest_non_empty_content = str(content)
                logger.info(
                    "[GUI_AGENT_DIAG] phase=JARVIS_FRAME_DECISION rpc_id=%s "
                    "normalized_is_final=%s current_content=%r "
                    "latest_non_empty_content=%r action=%s",
                    request.rpc_id,
                    normalized_is_final,
                    content,
                    latest_non_empty_content,
                    "complete_rpc" if normalized_is_final else "wait_next_frame",
                )
                if not normalized_is_final:
                    logger.info(
                        "[GUI_RPC_TRACE] phase=HANDLER_FRAME_NON_FINAL rpc_id=%s "
                        "latest_content_len=%s",
                        request.rpc_id,
                        len(latest_non_empty_content),
                    )
                    return
                if not latest_non_empty_content:
                    response_error = GuiExecutionError(
                        "INVALID_RESPONSE",
                        "Final GUI response contains no streamContent",
                    )
                logger.info(
                    "[GUI_RPC_TRACE] phase=HANDLER_FINAL_READY rpc_id=%s "
                    "result_len=%s has_error=%s",
                    request.rpc_id,
                    len(latest_non_empty_content),
                    response_error is not None,
                )
                response_ready.set()
            except Exception as exc:
                logger.exception(
                    "[GUI_RPC_TRACE] phase=HANDLER_EXCEPTION rpc_id=%s "
                    "error_type=%s",
                    request.rpc_id,
                    type(exc).__name__,
                )
                response_error = GuiExecutionError(
                    "INVALID_RESPONSE",
                    f"Failed to parse GUI response: {type(exc).__name__}",
                )
                response_ready.set()

        channel.register_gui_agent_handler(on_gui)
        logger.info(
            "[GUI_RPC_TRACE] phase=HANDLER_REGISTERED rpc_id=%s",
            request.rpc_id,
        )
        response_task: asyncio.Task[bool] | None = None
        disconnect_task: asyncio.Task[None] | None = None
        try:
            command = {
                "header": {
                    "namespace": "ClawAgent",
                    "name": "InvokeJarvisGUIAgentRequest",
                },
                "payload": {
                    "query": request.query,
                    "sessionId": request.xiaoyi_session_id,
                    "interactionId": request.xiaoyi_task_id,
                },
            }
            logger.info(
                "[GUI_AGENT_DIAG] phase=JARVIS_REQUEST_BUILT rpc_id=%s "
                "query=%r command=%r",
                request.rpc_id,
                request.query,
                command,
            )
            try:
                logger.info(
                    "[GUI_RPC_TRACE] phase=JARVIS_SEND_BEGIN rpc_id=%s "
                    "xiaoyi_session_id=%s xiaoyi_task_id=%s "
                    "xiaoyi_message_id=%s query_len=%s channel_ready=%s",
                    request.rpc_id,
                    request.xiaoyi_session_id,
                    request.xiaoyi_task_id,
                    request.xiaoyi_message_id,
                    len(request.query),
                    channel.is_ready,
                )
                sent = await channel.send_xiaoyi_phone_tools_command(
                    session_id=request.xiaoyi_session_id,
                    task_id=request.xiaoyi_task_id,
                    message_id=request.xiaoyi_message_id,
                    command=command,
                )
            except Exception as exc:
                logger.exception(
                    "[GUI_RPC_TRACE] phase=JARVIS_SEND_EXCEPTION rpc_id=%s "
                    "error_type=%s",
                    request.rpc_id,
                    type(exc).__name__,
                )
                raise GuiExecutionError(
                    "SEND_FAILED",
                    f"Failed to send Jarvis GUI request: {type(exc).__name__}",
                ) from exc
            if not sent:
                logger.error(
                    "[GUI_RPC_TRACE] phase=JARVIS_SEND_DONE rpc_id=%s sent=false",
                    request.rpc_id,
                )
                raise GuiExecutionError(
                    "SEND_FAILED",
                    "Failed to send Jarvis GUI request",
                )
            logger.info(
                "[GUI_RPC_TRACE] phase=JARVIS_SEND_DONE rpc_id=%s sent=true "
                "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
                "device_id=%s",
                request.rpc_id,
                request.xiaoyi_session_id,
                request.xiaoyi_task_id,
                request.jiuwen_session_id,
                request.device_id,
            )

            remaining = request.deadline - time.time()
            if remaining <= 0:
                raise GuiExecutionError("GUI_TIMEOUT", "GUI RPC deadline has expired")
            response_task = asyncio.create_task(response_ready.wait())
            disconnect_task = asyncio.create_task(_wait_until_disconnected(channel))
            logger.info(
                "[GUI_RPC_TRACE] phase=JARVIS_WAIT_BEGIN rpc_id=%s "
                "remaining_ms=%s",
                request.rpc_id,
                int(remaining * 1000),
            )
            finished, _ = await asyncio.wait(
                {response_task, disconnect_task},
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if response_task in finished and response_ready.is_set():
                if response_error is not None:
                    logger.error(
                        "[GUI_RPC_TRACE] phase=JARVIS_WAIT_DONE rpc_id=%s "
                        "success=false error_code=%s",
                        request.rpc_id,
                        response_error.error_code,
                    )
                    raise response_error
                logger.info(
                    "[GUI_RPC_TRACE] phase=JARVIS_WAIT_DONE rpc_id=%s "
                    "success=true xiaoyi_session_id=%s xiaoyi_task_id=%s "
                    "jiuwen_session_id=%s device_id=%s result_len=%s",
                    request.rpc_id,
                    request.xiaoyi_session_id,
                    request.xiaoyi_task_id,
                    request.jiuwen_session_id,
                    request.device_id,
                    len(latest_non_empty_content),
                )
                logger.info(
                    "[GUI_AGENT_DIAG] phase=JARVIS_RESULT_RETURNED rpc_id=%s "
                    "result=%r",
                    request.rpc_id,
                    latest_non_empty_content,
                )
                return latest_non_empty_content
            if disconnect_task in finished or not channel.is_ready:
                logger.error(
                    "[GUI_RPC_TRACE] phase=JARVIS_WAIT_DONE rpc_id=%s "
                    "success=false error_code=DEVICE_DISCONNECTED",
                    request.rpc_id,
                )
                raise GuiExecutionError(
                    "DEVICE_DISCONNECTED",
                    "Xiaoyi device disconnected while GUI RPC was running",
                )
            logger.error(
                "[GUI_RPC_TRACE] phase=JARVIS_WAIT_DONE rpc_id=%s "
                "success=false error_code=GUI_TIMEOUT",
                request.rpc_id,
            )
            raise GuiExecutionError(
                "GUI_TIMEOUT",
                "Timed out waiting for Jarvis GUI response",
            )
        finally:
            for task in (response_task, disconnect_task):
                if task is not None and not task.done():
                    task.cancel()
            tasks = [
                task
                for task in (response_task, disconnect_task)
                if task is not None
            ]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            channel.unregister_gui_agent_handler(on_gui)
            logger.info(
                "[GUI_RPC_TRACE] phase=HANDLER_UNREGISTERED rpc_id=%s "
                "xiaoyi_session_id=%s xiaoyi_task_id=%s "
                "jiuwen_session_id=%s device_id=%s elapsed_ms=%s",
                request.rpc_id,
                request.xiaoyi_session_id,
                request.xiaoyi_task_id,
                request.jiuwen_session_id,
                request.device_id,
                int((time.monotonic() - started_at) * 1000),
            )


async def _wait_until_disconnected(channel: Any) -> None:
    while channel.is_ready:
        await asyncio.sleep(0.1)
