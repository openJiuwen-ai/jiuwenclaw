from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from websockets.legacy.server import serve

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.e2a.wire_codec import encode_agent_response_for_wire
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_RESPONSE_METHOD
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcOrigin,
    ReverseRpcResponse,
    ReverseRpcRoute,
)
from jiuwenswarm.common.schema.agent import AgentResponse
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
from jiuwenswarm.gateway.reverse_rpc import (
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcCapabilityContext,
    ReverseRpcDispatcher,
    ReverseRpcResponseTransport,
)
from jiuwenswarm.gateway.routing.agent_client import WebSocketAgentServerClient
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire
from jiuwenswarm.server.reverse_rpc import (
    ReverseRpcClient,
    SingleGatewayReverseRpcTransport,
)


class _EchoCapability:
    async def handle(
        self,
        ctx: ReverseRpcCapabilityContext,
        payload: dict[str, Any],
    ) -> Any:
        return {
            "payload": payload,
            "rpc_id": ctx.rpc_id,
            "session_id": ctx.origin.session_id,
        }


@pytest.mark.asyncio
async def test_reverse_rpc_core_echo_crosses_real_websocket(
    unused_tcp_port: int,
) -> None:
    """Exercise Core through real E2A WebSocket frames in both directions."""
    client = ReverseRpcClient()
    connected = asyncio.Event()
    server_ws: list[Any] = []
    server_send_lock = asyncio.Lock()

    async def agent_server_endpoint(ws: Any) -> None:
        server_ws[:] = [ws]
        await ws.send(
            json.dumps(
                {
                    "type": "event",
                    "event": "connection.ack",
                    "payload": {"status": "ready"},
                }
            )
        )
        connected.set()
        async for raw in ws:
            envelope = E2AEnvelope.from_dict(json.loads(raw))
            assert envelope.method == REVERSE_RPC_RESPONSE_METHOD
            response = ReverseRpcResponse.from_dict(envelope.params)
            accepted = client.complete(response)
            ack = encode_agent_response_for_wire(
                AgentResponse(
                    request_id=envelope.request_id,
                    channel_id=envelope.channel or "",
                    ok=True,
                    payload={"accepted": accepted, "rpc_id": response.rpc_id},
                ),
                response_id=envelope.request_id,
            )
            async with server_send_lock:
                await ws.send(json.dumps(ack, ensure_ascii=False))

    ws_server = await serve(
        agent_server_endpoint,
        "127.0.0.1",
        unused_tcp_port,
    )
    gateway_client = WebSocketAgentServerClient()
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(method="test.echo", handler=_EchoCapability()))
    dispatcher = ReverseRpcDispatcher(
        registry,
        ReverseRpcResponseTransport(gateway_client),
    )
    message_handler = object.__new__(MessageHandler)
    message_handler._reverse_rpc_dispatcher = dispatcher
    gateway_client.set_server_push_handler(message_handler._handle_agent_server_push)
    gateway_client.set_disconnect_handler(dispatcher.on_agent_disconnect)

    async def strict_server_push(message: dict[str, Any]) -> None:
        await connected.wait()
        wire = build_server_push_wire(message)
        async with server_send_lock:
            await server_ws[0].send(json.dumps(wire, ensure_ascii=False))

    client.set_transport(SingleGatewayReverseRpcTransport(strict_server_push))

    try:
        await gateway_client.connect(f"ws://127.0.0.1:{unused_tcp_port}")
        result = await client.call(
            method="test.echo",
            payload={"value": "hello"},
            origin=ReverseRpcOrigin(
                request_id="request-e2e",
                session_id="session-e2e",
                channel_id="test",
            ),
            route=ReverseRpcRoute(channel_id="test"),
            timeout=2.0,
        )

        for _ in range(100):
            if dispatcher.execution_count == 0:
                break
            await asyncio.sleep(0.01)

        assert result["payload"] == {"value": "hello"}
        assert result["session_id"] == "session-e2e"
        assert result["rpc_id"].startswith("rrpc_")
        assert client.registry.pending_count() == 0
        assert dispatcher.execution_count == 0
    finally:
        await gateway_client.disconnect()
        ws_server.close()
        await ws_server.wait_closed()
