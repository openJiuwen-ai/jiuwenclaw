from __future__ import annotations

import asyncio
import json
import random

import pytest

from jiuwenswarm.common.reverse_rpc.codec import build_request_wire, request_from_wire
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
from jiuwenswarm.common.reverse_rpc.errors import (
    ReverseRpcOverloadedError,
    ReverseRpcRemoteError,
    ReverseRpcTimeoutError,
    ReverseRpcTransportDisconnected,
    ReverseRpcValidationError,
)
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcErrorPayload,
    ReverseRpcOrigin,
    ReverseRpcRequest,
    ReverseRpcResponse,
    ReverseRpcRoute,
)
from jiuwenswarm.server.reverse_rpc.client import ReverseRpcClient
from jiuwenswarm.server.reverse_rpc.pending_registry import (
    PendingReverseRpc,
    ReverseRpcPendingRegistry,
)


class FakeTransport:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.on_send = None

    async def send(self, message: dict, route: ReverseRpcRoute) -> None:
        self.messages.append(message)
        if self.on_send is not None:
            await self.on_send(message, route)


def _request(rpc_id: str = "rpc-1") -> ReverseRpcRequest:
    return ReverseRpcRequest(
        version=REVERSE_RPC_VERSION,
        rpc_id=rpc_id,
        method="test.echo",
        payload={"value": rpc_id},
        timeout_ms=1000,
        origin=ReverseRpcOrigin(request_id="request-1", channel_id="test"),
        route=ReverseRpcRoute(channel_id="test"),
    )


def test_reverse_rpc_models_and_wire_round_trip() -> None:
    request = _request()
    assert ReverseRpcRequest.from_dict(request.to_dict()) == request
    assert request_from_wire(build_request_wire(request)) == request

    response = ReverseRpcResponse(
        version=REVERSE_RPC_VERSION,
        rpc_id=request.rpc_id,
        ok=False,
        error=ReverseRpcErrorPayload(code="TEST_ERROR", message="failed"),
    )
    assert ReverseRpcResponse.from_dict(response.to_dict()) == response


@pytest.mark.parametrize(
    "mutation",
    [
        {"version": None},
        {"version": 2},
        {"rpc_id": ""},
        {"method": ""},
        {"payload": []},
        {"timeout_ms": 0},
    ],
)
def test_reverse_rpc_request_validation(mutation: dict) -> None:
    data = _request().to_dict()
    data.update(mutation)
    with pytest.raises(ReverseRpcValidationError):
        ReverseRpcRequest.from_dict(data)


def test_pending_registry_duplicate_capacity_and_fail_all() -> None:
    loop = asyncio.new_event_loop()
    try:
        registry = ReverseRpcPendingRegistry(max_pending=1)
        future = loop.create_future()
        pending = PendingReverseRpc(_request(), future, loop.time())
        registry.register(pending)
        with pytest.raises(RuntimeError, match="duplicate"):
            registry.register(pending)
        other = PendingReverseRpc(_request("rpc-2"), loop.create_future(), loop.time())
        with pytest.raises(ReverseRpcOverloadedError):
            registry.register(other)
        registry.fail_all(ReverseRpcTransportDisconnected("disconnected"))
        assert registry.pending_count() == 0
        assert isinstance(future.exception(), ReverseRpcTransportDisconnected)
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_reverse_rpc_client_success_and_remote_error() -> None:
    transport = FakeTransport()
    client = ReverseRpcClient(transport=transport)

    async def complete(message: dict, route: ReverseRpcRoute) -> None:
        del route
        request = request_from_wire(message)
        if request.payload.get("fail"):
            response = ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                ok=False,
                error=ReverseRpcErrorPayload(code="TEST_ERROR", message="failed"),
            )
        else:
            response = ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                ok=True,
                result=request.payload,
            )
        assert client.complete(response)

    transport.on_send = complete
    result = await client.call(
        method="test.echo",
        payload={"value": 1},
        origin=ReverseRpcOrigin(request_id="request-1"),
        route=ReverseRpcRoute(channel_id="test"),
        timeout=1.0,
    )
    assert result == {"value": 1}
    assert client.registry.pending_count() == 0

    with pytest.raises(ReverseRpcRemoteError) as exc_info:
        await client.call(
            method="test.echo",
            payload={"fail": True},
            origin=ReverseRpcOrigin(request_id="request-2"),
            route=ReverseRpcRoute(channel_id="test"),
            timeout=1.0,
        )
    assert exc_info.value.code == "TEST_ERROR"


@pytest.mark.asyncio
async def test_reverse_rpc_client_timeout_cancel_and_cleanup() -> None:
    transport = FakeTransport()
    client = ReverseRpcClient(transport=transport)

    with pytest.raises(ReverseRpcTimeoutError):
        await client.call(
            method="test.slow",
            payload={},
            origin=ReverseRpcOrigin(),
            route=ReverseRpcRoute(channel_id="test"),
            timeout=0.01,
        )
    assert [item["response_kind"] for item in transport.messages] == [
        "reverse_rpc.request",
        "reverse_rpc.cancel",
    ]
    assert client.registry.pending_count() == 0


@pytest.mark.asyncio
async def test_reverse_rpc_client_disconnect_fails_all() -> None:
    transport = FakeTransport()
    sent = asyncio.Event()

    async def mark_sent(message: dict, route: ReverseRpcRoute) -> None:
        del message, route
        sent.set()

    transport.on_send = mark_sent
    client = ReverseRpcClient(transport=transport)
    task = asyncio.create_task(
        client.call(
            method="test.slow",
            payload={},
            origin=ReverseRpcOrigin(),
            route=ReverseRpcRoute(channel_id="test"),
            timeout=10.0,
        )
    )
    await sent.wait()
    client.fail_all(ReverseRpcTransportDisconnected("disconnected"))
    with pytest.raises(ReverseRpcTransportDisconnected):
        await task
    assert client.registry.pending_count() == 0


@pytest.mark.asyncio
async def test_reverse_rpc_client_correlates_100_out_of_order_responses() -> None:
    transport = FakeTransport()
    client = ReverseRpcClient(transport=transport)
    calls = [
        asyncio.create_task(
            client.call(
                method="test.echo",
                payload={"index": index},
                origin=ReverseRpcOrigin(request_id=f"request-{index}"),
                route=ReverseRpcRoute(channel_id="test"),
                timeout=2.0,
            )
        )
        for index in range(100)
    ]
    while len(transport.messages) < 100:
        await asyncio.sleep(0)
    requests = [request_from_wire(item) for item in transport.messages]
    random.Random(42).shuffle(requests)
    for request in requests:
        assert client.complete(
            ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                ok=True,
                result=request.payload,
            )
        )
    results = await asyncio.gather(*calls)
    assert results == [{"index": index} for index in range(100)]
    assert client.registry.pending_count() == 0


@pytest.mark.asyncio
async def test_agent_ws_reverse_rpc_response_completes_and_acks() -> None:
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer
    from jiuwenswarm.server.reverse_rpc.runtime import get_reverse_rpc_client

    class FakeWs:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, value: str) -> None:
            self.sent.append(value)

    client = get_reverse_rpc_client()
    request_model = _request("rpc-agent-ws")
    future = asyncio.get_running_loop().create_future()
    client.registry.register(
        PendingReverseRpc(request_model, future, asyncio.get_running_loop().time())
    )
    response = ReverseRpcResponse(
        version=REVERSE_RPC_VERSION,
        rpc_id=request_model.rpc_id,
        ok=True,
        result={"done": True},
    )
    request = AgentRequest(
        request_id="reverse-response-1",
        channel_id="test",
        req_method=ReqMethod.REVERSE_RPC_RESPONSE,
        params=response.to_dict(),
    )
    ws = FakeWs()
    try:
        await AgentWebSocketServer._handle_reverse_rpc_response(
            object.__new__(AgentWebSocketServer),
            ws,
            request,
            asyncio.Lock(),
        )
        assert future.result() == response
        ack = json.loads(ws.sent[0])
        assert ack["body"]["result"]["accepted"] is True
    finally:
        client.registry.remove(request_model.rpc_id)


@pytest.mark.asyncio
async def test_agent_ws_keeps_send_push_best_effort_but_reverse_rpc_is_strict() -> None:
    from jiuwenswarm.common.reverse_rpc.errors import (
        ReverseRpcTransportDisconnected,
    )
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    server = object.__new__(AgentWebSocketServer)
    server._current_ws = None
    server._current_send_lock = None

    await server.send_push({"response_kind": "ext", "body": {}})
    with pytest.raises(ReverseRpcTransportDisconnected):
        await server._send_reverse_rpc_push(
            build_request_wire(_request("rpc-no-gateway"))
        )


@pytest.mark.asyncio
async def test_agent_ws_reverse_rpc_rejects_oversized_fallback(monkeypatch) -> None:
    from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    class FakeWs:
        pass

    async def oversized_send(ws, wire):
        del ws, wire
        return False

    server = object.__new__(AgentWebSocketServer)
    server._current_ws = FakeWs()
    server._current_send_lock = asyncio.Lock()
    monkeypatch.setattr(
        agent_ws_server_module,
        "send_wire_payload",
        oversized_send,
    )

    with pytest.raises(RuntimeError, match="send budget"):
        await server._send_reverse_rpc_push(
            build_request_wire(_request("rpc-oversized"))
        )
