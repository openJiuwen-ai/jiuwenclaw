"""Typed AgentServer client for Xiaoyi GUI over generic Reverse RPC."""

from __future__ import annotations

from jiuwenswarm.common.gui_rpc.models import (
    GUI_RPC_RESPONSE_MESSAGE_TYPE,
    GuiRpcResponse,
)
from jiuwenswarm.common.gui_rpc.reverse_rpc import (
    GuiReverseRpcPayload,
    GuiReverseRpcResult,
    XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
    XIAOYI_GUI_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcRemoteError
from jiuwenswarm.common.reverse_rpc.models import ReverseRpcOrigin, ReverseRpcRoute
from jiuwenswarm.server.reverse_rpc import ReverseRpcClient, get_reverse_rpc_client


class XiaoyiGuiReverseRpcClient:
    """Business adapter; the generic client remains unaware of GUI models."""

    def __init__(self, client: ReverseRpcClient | None = None) -> None:
        self._client = client or get_reverse_rpc_client()

    async def call(
        self,
        *,
        query: str,
        source_request_id: str,
        jiuwen_session_id: str | None,
        xiaoyi_session_id: str,
        xiaoyi_task_id: str,
        xiaoyi_message_id: str,
        device_id: str | None = None,
        execution_id: str | None = None,
        app_id: str | None = None,
        binding_id: str | None = None,
        timeout: float = XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
    ) -> GuiRpcResponse:
        payload = GuiReverseRpcPayload(
            query=query,
            xiaoyi_session_id=xiaoyi_session_id,
            xiaoyi_task_id=xiaoyi_task_id,
            xiaoyi_message_id=xiaoyi_message_id,
            device_id=device_id,
        )
        try:
            raw_result = await self._client.call(
                method=XIAOYI_GUI_REVERSE_RPC_METHOD,
                payload=payload.to_dict(),
                origin=ReverseRpcOrigin(
                    execution_id=execution_id,
                    request_id=source_request_id,
                    session_id=jiuwen_session_id,
                    channel_id="xiaoyi",
                ),
                route=ReverseRpcRoute(
                    channel_id="xiaoyi",
                    app_id=app_id,
                    binding_id=binding_id,
                ),
                timeout=timeout,
                remote_cancel=True,
            )
        except ReverseRpcRemoteError as exc:
            details = exc.details if isinstance(exc.details, dict) else {}
            return GuiRpcResponse(
                message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
                rpc_id=str(details.get("rpc_id") or ""),
                success=False,
                error_code=exc.code,
                error_message=exc.message,
            )

        result = GuiReverseRpcResult.from_dict(raw_result)
        return GuiRpcResponse(
            message_type=GUI_RPC_RESPONSE_MESSAGE_TYPE,
            rpc_id=result.rpc_id,
            success=True,
            result=result.result,
        )


_gui_reverse_rpc_client = XiaoyiGuiReverseRpcClient()


def get_xiaoyi_gui_reverse_rpc_client() -> XiaoyiGuiReverseRpcClient:
    return _gui_reverse_rpc_client
