from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL,
    E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_WIRE_SERVER_PUSH_KEY,
)
from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_CANCEL_MESSAGE_TYPE,
    GUI_RPC_REQUEST_MESSAGE_TYPE,
    GuiRpcCancel,
    GuiRpcRequest,
    GuiRpcResponse,
)
from jiuwenswarm.common.schema.agent import AgentRequest

logger = logging.getLogger(__name__)

GUI_RPC_DEFAULT_TIMEOUT_SECONDS = 180.0


@dataclass
class PendingGuiRpc:
    request: GuiRpcRequest
    future: asyncio.Future[GuiRpcResponse]


SendPushCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


class GuiRpcClientError(RuntimeError):
    def __init__(self, error_code: str, message: str) -> None:
        super().__init__(message)
        self.error_code = error_code


class GuiRpcContextError(GuiRpcClientError):
    pass


def build_gui_rpc_request(
    *,
    query: str,
    request: AgentRequest,
    timeout: float,
) -> GuiRpcRequest:
    source_request_id = str(request.request_id or "").strip()
    if not source_request_id:
        raise GuiRpcContextError(
            "INVALID_CONTEXT",
            "current request_id is missing",
        )
    if str(request.channel_id or "").strip() != "xiaoyi":
        raise GuiRpcContextError(
            "INVALID_CONTEXT",
            "xiaoyi_gui_agent can only be used for a Xiaoyi request",
        )
    metadata = dict(request.metadata or {})
    params = request.params if isinstance(request.params, dict) else {}
    xiaoyi_session_id = _first_text(
        metadata.get("xiaoyi_root_session_id"),
        metadata.get("xiaoyi_session_id"),
        metadata.get("xiaoyi_params_session_id"),
        params.get("xiaoyi_session_id"),
        params.get("session_id"),
        request.chat_id,
    )
    xiaoyi_task_id = _first_text(
        metadata.get("xiaoyi_task_id"),
        params.get("task_id"),
    )
    xiaoyi_message_id = _first_text(
        metadata.get("xiaoyi_rpc_id"),
        metadata.get("xiaoyi_message_id"),
    )
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
        raise GuiRpcContextError(
            "INVALID_CONTEXT",
            f"current Xiaoyi request is missing: {', '.join(missing)}",
        )
    return GuiRpcRequest(
        message_type=GUI_RPC_REQUEST_MESSAGE_TYPE,
        rpc_id=f"xiaoyi_gui_rpc_{uuid.uuid4().hex}",
        query=query,
        source_request_id=source_request_id,
        jiuwen_session_id=_first_text(request.session_id),
        xiaoyi_session_id=xiaoyi_session_id,
        xiaoyi_task_id=xiaoyi_task_id,
        xiaoyi_message_id=xiaoyi_message_id,
        device_id=_first_text(
            metadata.get("xiaoyi_device_id"),
            metadata.get("device_id"),
        ),
        deadline=time.time() + timeout,
    )


def build_gui_rpc_push(request: GuiRpcRequest) -> dict[str, Any]:
    return {
        "request_id": f"gui_rpc_push_{request.rpc_id}",
        "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_REQUEST,
        "is_final": False,
        "status": E2A_RESPONSE_STATUS_IN_PROGRESS,
        "body": request.to_dict(),
        "channel_id": "xiaoyi",
        "session_id": request.jiuwen_session_id,
        "metadata": {E2A_WIRE_SERVER_PUSH_KEY: True},
    }


def build_gui_rpc_cancel_push(cancel: GuiRpcCancel) -> dict[str, Any]:
    return {
        "request_id": f"gui_rpc_cancel_{cancel.rpc_id}",
        "response_kind": E2A_RESPONSE_KIND_XIAOYI_GUI_RPC_CANCEL,
        "is_final": False,
        "status": E2A_RESPONSE_STATUS_IN_PROGRESS,
        "body": cancel.to_dict(),
        "channel_id": "xiaoyi",
        "metadata": {E2A_WIRE_SERVER_PUSH_KEY: True},
    }


class GuiRpcClient:
    def __init__(self) -> None:
        self._pending: dict[str, PendingGuiRpc] = {}
        self._send_push_callback: SendPushCallback | None = None

    def set_send_push_callback(self, callback: SendPushCallback) -> None:
        self._send_push_callback = callback

    async def call(
        self,
        *,
        query: str,
        request: AgentRequest,
        timeout: float = GUI_RPC_DEFAULT_TIMEOUT_SECONDS,
    ) -> GuiRpcResponse:
        if self._send_push_callback is None:
            raise RuntimeError("GUI RPC Gateway push callback is not configured")
        gui_request = build_gui_rpc_request(
            query=query,
            request=request,
            timeout=timeout,
        )
        logger.info(
            "[GUI_RPC_TRACE] phase=REQUEST_CREATED rpc_id=%s "
            "source_request_id=%s xiaoyi_session_id=%s xiaoyi_task_id=%s "
            "jiuwen_session_id=%s device_id=%s timeout_seconds=%s query_len=%s",
            gui_request.rpc_id,
            gui_request.source_request_id,
            gui_request.xiaoyi_session_id,
            gui_request.xiaoyi_task_id,
            gui_request.jiuwen_session_id,
            gui_request.device_id,
            timeout,
            len(query),
        )
        future = asyncio.get_running_loop().create_future()
        self._pending[gui_request.rpc_id] = PendingGuiRpc(gui_request, future)
        logger.info(
            "[GUI_RPC_TRACE] phase=PENDING_ADDED rpc_id=%s "
            "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
            "device_id=%s pending_count=%s",
            gui_request.rpc_id,
            gui_request.xiaoyi_session_id,
            gui_request.xiaoyi_task_id,
            gui_request.jiuwen_session_id,
            gui_request.device_id,
            len(self._pending),
        )
        try:
            try:
                logger.info(
                    "[GUI_RPC_TRACE] phase=SEND_TO_GATEWAY_BEGIN rpc_id=%s "
                    "deadline_remaining_ms=%s",
                    gui_request.rpc_id,
                    max(0, int((gui_request.deadline - time.time()) * 1000)),
                )
                await self._send(build_gui_rpc_push(gui_request))
                logger.info(
                    "[GUI_RPC_TRACE] phase=SEND_TO_GATEWAY_DONE rpc_id=%s "
                    "deadline_remaining_ms=%s",
                    gui_request.rpc_id,
                    max(0, int((gui_request.deadline - time.time()) * 1000)),
                )
            except asyncio.CancelledError:
                await self._send_cancel(
                    gui_request.rpc_id,
                    "GUI Tool call cancelled while sending",
                )
                raise
            except Exception as exc:
                raise GuiRpcClientError(
                    "CHANNEL_NOT_READY",
                    f"Failed to send GUI RPC request: {type(exc).__name__}",
                ) from exc
            remaining = gui_request.deadline - time.time()
            if remaining <= 0:
                raise asyncio.TimeoutError
            try:
                logger.info(
                    "[GUI_RPC_TRACE] phase=WAIT_RESPONSE_BEGIN rpc_id=%s "
                    "remaining_ms=%s",
                    gui_request.rpc_id,
                    int(remaining * 1000),
                )
                response = await asyncio.wait_for(
                    asyncio.shield(future),
                    remaining,
                )
                logger.info(
                    "[GUI_RPC_TRACE] phase=WAIT_RESPONSE_DONE rpc_id=%s "
                    "success=%s error_code=%s",
                    gui_request.rpc_id,
                    response.success,
                    response.error_code,
                )
                return response
            except asyncio.TimeoutError:
                logger.error(
                    "[GUI_RPC_TRACE] phase=WAIT_RESPONSE_TIMEOUT rpc_id=%s",
                    gui_request.rpc_id,
                )
                await self._send_cancel(gui_request.rpc_id, "GUI RPC timed out")
                raise
            except asyncio.CancelledError:
                logger.warning(
                    "[GUI_RPC_TRACE] phase=WAIT_RESPONSE_CANCELLED rpc_id=%s",
                    gui_request.rpc_id,
                )
                await self._send_cancel(gui_request.rpc_id, "GUI Tool call cancelled")
                raise
        finally:
            pending = self._pending.pop(gui_request.rpc_id, None)
            if pending is not None and not pending.future.done():
                pending.future.cancel()
            logger.info(
                "[GUI_RPC_TRACE] phase=CLIENT_CLEANUP rpc_id=%s "
                "xiaoyi_session_id=%s xiaoyi_task_id=%s jiuwen_session_id=%s "
                "device_id=%s pending_count=%s",
                gui_request.rpc_id,
                gui_request.xiaoyi_session_id,
                gui_request.xiaoyi_task_id,
                gui_request.jiuwen_session_id,
                gui_request.device_id,
                len(self._pending),
            )

    def complete(self, response: GuiRpcResponse) -> bool:
        pending = self._pending.pop(response.rpc_id, None)
        if pending is None or pending.future.done():
            logger.warning(
                "[GUI_RPC_TRACE] phase=RESPONSE_IGNORED rpc_id=%s "
                "reason=unknown_or_completed success=%s",
                response.rpc_id,
                response.success,
            )
            return False
        pending.future.set_result(response)
        logger.info(
            "[GUI_RPC_TRACE] phase=PENDING_COMPLETED rpc_id=%s "
            "success=%s error_code=%s pending_count=%s",
            response.rpc_id,
            response.success,
            response.error_code,
            len(self._pending),
        )
        return True

    def fail_all(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        logger.warning(
            "[GUI_RPC_TRACE] phase=FAIL_ALL pending_count=%s error_type=%s",
            len(pending),
            type(exc).__name__,
        )
        for item in pending:
            if not item.future.done():
                item.future.set_exception(exc)

    async def _send(self, message: dict[str, Any]) -> None:
        callback = self._send_push_callback
        if callback is None:
            raise RuntimeError("GUI RPC Gateway push callback is not configured")
        result = callback(message)
        if inspect.isawaitable(result):
            await result

    async def _send_cancel(self, rpc_id: str, reason: str) -> None:
        cancel = GuiRpcCancel(
            message_type=GUI_RPC_CANCEL_MESSAGE_TYPE,
            rpc_id=rpc_id,
            reason=reason,
        )
        try:
            logger.info(
                "[GUI_RPC_TRACE] phase=CANCEL_SEND_BEGIN rpc_id=%s "
                "reason_len=%s",
                rpc_id,
                len(reason),
            )
            await asyncio.shield(self._send(build_gui_rpc_cancel_push(cancel)))
            logger.info(
                "[GUI_RPC_TRACE] phase=CANCEL_SEND_DONE rpc_id=%s",
                rpc_id,
            )
        except Exception as exc:
            logger.warning(
                "[GUI_RPC_TRACE] phase=CANCEL_SEND_FAILED rpc_id=%s "
                "error_type=%s",
                rpc_id,
                type(exc).__name__,
            )


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


_gui_rpc_client = GuiRpcClient()


def get_gui_rpc_client() -> GuiRpcClient:
    return _gui_rpc_client
