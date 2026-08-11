"""Gateway-to-AgentServer response transport for generic Reverse RPC."""

from __future__ import annotations

import uuid
from typing import Any, Protocol

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_RESPONSE_METHOD
from jiuwenswarm.common.reverse_rpc.models import ReverseRpcRequest, ReverseRpcResponse


class AgentRequestSender(Protocol):
    async def send_request(self, envelope: E2AEnvelope) -> Any: ...


class ReverseRpcResponseTransport:
    def __init__(self, agent_client: AgentRequestSender) -> None:
        self._agent_client = agent_client

    async def send(
        self,
        response: ReverseRpcResponse,
        request: ReverseRpcRequest,
    ) -> None:
        envelope = E2AEnvelope(
            request_id=f"reverse_rpc_resp_{uuid.uuid4().hex}",
            method=REVERSE_RPC_RESPONSE_METHOD,
            channel=request.route.channel_id or request.origin.channel_id,
            session_id=request.origin.session_id,
            params=response.to_dict(),
            is_stream=False,
        )
        await self._agent_client.send_request(envelope)
