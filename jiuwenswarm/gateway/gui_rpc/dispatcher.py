from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL,
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
)
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_RESPONSE_MESSAGE_TYPE,
    GuiRpcCancel,
    GuiRpcRequest,
    GuiRpcResponse,
)
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.gateway.gui_rpc.executor import (
    GuiExecutionError,
    XiaoyiGuiExecutor,
)

logger = logging.getLogger(__name__)


class XiaoyiGuiRpcDispatcher:
    def __init__(
        self,
        agent_client: Any,
        executor: XiaoyiGuiExecutor | None = None,
    ) -> None:
        self._agent_client = agent_client
        self._executor = executor or XiaoyiGuiExecutor()
        self._executions: dict[str, asyncio.Task[str]] = {}
        self._cancel_reasons: dict[str, str] = {}

    async def handle(self, wire: dict[str, Any]) -> None:
        response_kind = str(wire.get("response_kind") or "")
        if response_kind == E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST:
            await self._handle_request(wire)
            return
        if response_kind == E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL:
            await self._handle_cancel(wire)
            return
        raise ValueError(f"unsupported GUI RPC response kind: {response_kind}")

    async def _handle_request(self, wire: dict[str, Any]) -> None:
        body = wire.get("body") if isinstance(wire.get("body"), dict) else {}
        try:
            request = GuiRpcRequest.from_dict(body)
        except ValueError as exc:
            rpc_id = str(body.get("rpc_id") or "").strip()
            logger.warning(
                "[GUI_RPC_TRACE] phase=DISPATCH_INVALID_REQUEST rpc_id=%s "
                "error_type=%s",
                rpc_id,
                type(exc).__name__,
            )
            if rpc_id:
                await self._send_response(
                    GuiRpcResponse(
                        message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
                        rpc_id=rpc_id,
                        success=False,
                        error_code="INVALID_CONTEXT",
                        error_message="Invalid GUI RPC request",
                    ),
                    jiuwen_session_id=None,
                )
            return

        logger.info(
            "[GUI_RPC_TRACE] phase=DISPATCH_REQUEST_RECEIVED rpc_id=%s "
            "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
            "device_id=%s deadline_remaining_ms=%s",
            request.rpc_id,
            request.xiaoyi_session_id,
            request.xiaoyi_task_id,
            request.jiuwen_session_id,
            request.device_id,
            max(0, int((request.deadline - time.time()) * 1000)),
        )
        if request.rpc_id in self._executions:
            logger.warning(
                "[GUI_RPC_TRACE] phase=DISPATCH_DUPLICATE rpc_id=%s",
                request.rpc_id,
            )
            await self._send_response(
                _failure(
                    request.rpc_id,
                    "INTERNAL_ERROR",
                    "Duplicate GUI RPC request",
                ),
                jiuwen_session_id=request.jiuwen_session_id,
            )
            return

        execution = asyncio.create_task(self._executor.execute(request))
        self._executions[request.rpc_id] = execution
        logger.info(
            "[GUI_RPC_TRACE] phase=EXECUTOR_TASK_CREATED rpc_id=%s "
            "execution_count=%s",
            request.rpc_id,
            len(self._executions),
        )
        try:
            result = await execution
            logger.info(
                "[GUI_RPC_TRACE] phase=EXECUTOR_TASK_DONE rpc_id=%s "
                "success=true result_len=%s",
                request.rpc_id,
                len(result),
            )
            response = GuiRpcResponse(
                message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
                rpc_id=request.rpc_id,
                success=True,
                result=result,
            )
        except GuiExecutionError as exc:
            logger.error(
                "[GUI_RPC_TRACE] phase=EXECUTOR_TASK_DONE rpc_id=%s "
                "success=false error_code=%s error_type=%s",
                request.rpc_id,
                exc.error_code,
                type(exc).__name__,
            )
            response = _failure(request.rpc_id, exc.error_code, str(exc))
        except asyncio.CancelledError:
            logger.warning(
                "[GUI_RPC_TRACE] phase=EXECUTOR_TASK_CANCELLED rpc_id=%s",
                request.rpc_id,
            )
            response = _failure(
                request.rpc_id,
                "CANCELLED",
                self._cancel_reasons.get(
                    request.rpc_id,
                    "GUI RPC execution was cancelled",
                ),
            )
        except Exception as exc:
            logger.exception(
                "[GUI_RPC_TRACE] phase=EXECUTOR_TASK_EXCEPTION rpc_id=%s "
                "error_type=%s",
                request.rpc_id,
                type(exc).__name__,
            )
            response = _failure(
                request.rpc_id,
                "INTERNAL_ERROR",
                f"GUI RPC execution failed: {type(exc).__name__}",
            )
        finally:
            if self._executions.get(request.rpc_id) is execution:
                self._executions.pop(request.rpc_id, None)
            self._cancel_reasons.pop(request.rpc_id, None)
            logger.info(
                "[GUI_RPC_TRACE] phase=DISPATCH_EXECUTION_CLEANUP rpc_id=%s "
                "execution_count=%s",
                request.rpc_id,
                len(self._executions),
            )

        await self._send_response(
            response,
            jiuwen_session_id=request.jiuwen_session_id,
        )

    async def _handle_cancel(self, wire: dict[str, Any]) -> None:
        body = wire.get("body") if isinstance(wire.get("body"), dict) else {}
        try:
            cancel = GuiRpcCancel.from_dict(body)
        except ValueError as exc:
            logger.warning(
                "[GUI_RPC_TRACE] phase=DISPATCH_INVALID_CANCEL rpc_id=%s "
                "error_type=%s",
                str(body.get("rpc_id") or ""),
                type(exc).__name__,
            )
            return
        execution = self._executions.get(cancel.rpc_id)
        if execution is None or execution.done():
            logger.info(
                "[GUI_RPC_TRACE] phase=DISPATCH_CANCEL_IGNORED rpc_id=%s",
                cancel.rpc_id,
            )
            return
        self._cancel_reasons[cancel.rpc_id] = cancel.reason
        execution.cancel()
        logger.info(
            "[GUI_RPC_TRACE] phase=DISPATCH_CANCELLED rpc_id=%s",
            cancel.rpc_id,
        )

    async def cancel_all(self, reason: str = "AgentServer disconnected") -> None:
        tasks = list(self._executions.items())
        for rpc_id, task in tasks:
            self._cancel_reasons[rpc_id] = reason
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(
                *(task for _, task in tasks),
                return_exceptions=True,
            )

    async def _send_response(
        self,
        response: GuiRpcResponse,
        *,
        jiuwen_session_id: str | None,
    ) -> None:
        if hasattr(self._agent_client, "server_ready"):
            if not self._agent_client.server_ready:
                logger.info(
                    "[GUI_RPC_TRACE] phase=RESPONSE_SEND_SKIPPED rpc_id=%s "
                    "reason=agent_server_disconnected",
                    response.rpc_id,
                )
                return
        envelope = E2AEnvelope(
            request_id=f"gui_rpc_resp_{uuid.uuid4().hex}",
            method=ReqMethod.XIAOYI_GUI_RPC_RESPONSE.value,
            channel="xiaoyi",
            session_id=jiuwen_session_id,
            params=response.to_dict(),
            is_stream=False,
        )
        try:
            logger.info(
                "[GUI_RPC_TRACE] phase=RESPONSE_SEND_BEGIN rpc_id=%s "
                "success=%s error_code=%s",
                response.rpc_id,
                response.success,
                response.error_code,
            )
            await self._agent_client.send_request(envelope)
        except Exception as exc:
            logger.warning(
                "[GUI_RPC_TRACE] phase=RESPONSE_SEND_FAILED rpc_id=%s "
                "error_type=%s",
                response.rpc_id,
                type(exc).__name__,
            )
            return
        logger.info(
            "[GUI_RPC_TRACE] phase=RESPONSE_SEND_DONE rpc_id=%s success=%s",
            response.rpc_id,
            response.success,
        )


def _failure(
    rpc_id: str,
    error_code: str,
    error_message: str,
) -> GuiRpcResponse:
    return GuiRpcResponse(
        message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
        rpc_id=rpc_id,
        success=False,
        error_code=error_code,
        error_message=error_message,
    )
