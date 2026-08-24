"""Gateway-to-AgentServer response transport for generic Reverse RPC."""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, Protocol

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.reverse_rpc.constants import (
    ERROR_INTERNAL,
    ERROR_RESULT_TOO_LARGE,
    REVERSE_RPC_RESPONSE_METHOD,
    REVERSE_RPC_VERSION,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcTransportDisconnected
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcErrorPayload,
    ReverseRpcRequest,
    ReverseRpcResponse,
)
from jiuwenswarm.common.ws_limits import AGENT_WS_SEND_BUDGET_BYTES


logger = logging.getLogger(__name__)
REVERSE_RPC_RESPONSE_ACK_TIMEOUT_SECONDS = 10.0


class AgentRequestSender(Protocol):
    async def send_request(self, envelope: E2AEnvelope) -> Any: ...


class ReverseRpcResponseTransport:
    def __init__(
        self,
        agent_client: AgentRequestSender,
        *,
        ack_timeout_seconds: float = REVERSE_RPC_RESPONSE_ACK_TIMEOUT_SECONDS,
    ) -> None:
        if ack_timeout_seconds <= 0:
            raise ValueError("ack_timeout_seconds must be positive")
        self._agent_client = agent_client
        self._ack_timeout_seconds = ack_timeout_seconds

    async def send(
        self,
        response: ReverseRpcResponse,
        request: ReverseRpcRequest,
    ) -> None:
        envelope = self._build_envelope(response, request)
        envelope = self._replace_invalid_or_oversized_response(
            envelope,
            response,
            request,
        )
        try:
            ack = await asyncio.wait_for(
                self._agent_client.send_request(envelope),
                timeout=self._ack_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise ReverseRpcTransportDisconnected(
                "AgentServer did not acknowledge the Reverse RPC response within "
                f"{self._ack_timeout_seconds}s"
            ) from exc

        if getattr(ack, "ok", True) is False:
            raise ReverseRpcTransportDisconnected(
                "AgentServer rejected the Reverse RPC response"
            )

    @staticmethod
    def _build_envelope(
        response: ReverseRpcResponse,
        request: ReverseRpcRequest,
    ) -> E2AEnvelope:
        envelope = E2AEnvelope(
            request_id=f"reverse_rpc_resp_{uuid.uuid4().hex}",
            method=REVERSE_RPC_RESPONSE_METHOD,
            channel=request.route.channel_id or request.origin.channel_id,
            session_id=request.origin.session_id,
            params=response.to_dict(),
            is_stream=False,
        )
        return envelope

    def _replace_invalid_or_oversized_response(
        self,
        envelope: E2AEnvelope,
        response: ReverseRpcResponse,
        request: ReverseRpcRequest,
    ) -> E2AEnvelope:
        try:
            actual_bytes = self._serialized_size(envelope)
        except (TypeError, ValueError, OverflowError):
            logger.exception(
                "[REVERSE_RPC] phase=GATEWAY_RESPONSE_SERIALIZATION_FAILED "
                "rpc_id=%s method=%s",
                response.rpc_id,
                request.method,
            )
            fallback = self._error_response(
                response.rpc_id,
                ERROR_INTERNAL,
                "Gateway capability result is not JSON serializable",
            )
            return self._checked_fallback_envelope(fallback, request)

        if actual_bytes <= AGENT_WS_SEND_BUDGET_BYTES:
            return envelope

        logger.warning(
            "[REVERSE_RPC] phase=GATEWAY_RESPONSE_TOO_LARGE rpc_id=%s method=%s "
            "actual_bytes=%s max_bytes=%s",
            response.rpc_id,
            request.method,
            actual_bytes,
            AGENT_WS_SEND_BUDGET_BYTES,
        )
        fallback = self._error_response(
            response.rpc_id,
            ERROR_RESULT_TOO_LARGE,
            "Gateway capability result exceeds the Reverse RPC response budget",
            details={
                "actual_bytes": actual_bytes,
                "max_bytes": AGENT_WS_SEND_BUDGET_BYTES,
            },
        )
        return self._checked_fallback_envelope(fallback, request)

    def _checked_fallback_envelope(
        self,
        fallback: ReverseRpcResponse,
        request: ReverseRpcRequest,
    ) -> E2AEnvelope:
        envelope = self._build_envelope(fallback, request)
        fallback_bytes = self._serialized_size(envelope)
        if fallback_bytes > AGENT_WS_SEND_BUDGET_BYTES:
            raise RuntimeError(
                "Reverse RPC error fallback exceeds the WebSocket send budget"
            )
        return envelope

    @staticmethod
    def _serialized_size(envelope: E2AEnvelope) -> int:
        serialized = json.dumps(envelope.to_dict(), ensure_ascii=False)
        return len(serialized.encode("utf-8"))

    @staticmethod
    def _error_response(
        rpc_id: str,
        code: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> ReverseRpcResponse:
        return ReverseRpcResponse(
            version=REVERSE_RPC_VERSION,
            rpc_id=rpc_id,
            ok=False,
            error=ReverseRpcErrorPayload(
                code=code,
                message=message,
                details=details,
            ),
        )
