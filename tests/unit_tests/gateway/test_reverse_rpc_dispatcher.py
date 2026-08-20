from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.common.reverse_rpc.codec import build_cancel_wire, build_request_wire
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcTransportDisconnected
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcCancel,
    ReverseRpcOrigin,
    ReverseRpcRequest,
    ReverseRpcResponse,
    ReverseRpcRoute,
)
from jiuwenswarm.gateway.reverse_rpc import (
    CapabilityError,
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcDispatcher,
    ReverseRpcResponseTransport,
)


class FakeResponseTransport:
    def __init__(self) -> None:
        self.sent = []

    async def send(self, response, request) -> None:
        self.sent.append((response, request))


class EchoHandler:
    async def handle(self, ctx, payload):
        return {"rpc_id": ctx.rpc_id, **payload}


class ErrorHandler:
    async def handle(self, ctx, payload):
        del ctx, payload
        raise CapabilityError("TEST_FAILURE", "failed")


class BlockingHandler:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def handle(self, ctx, payload):
        del ctx, payload
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class BlockingResponseTransport(FakeResponseTransport):
    def __init__(self, blocked_rpc_id: str) -> None:
        super().__init__()
        self.blocked_rpc_id = blocked_rpc_id
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send(self, response, request) -> None:
        self.sent.append((response, request))
        if response.rpc_id == self.blocked_rpc_id:
            self.started.set()
            await self.release.wait()


def _request(method: str = "test.echo", rpc_id: str = "rpc-1") -> ReverseRpcRequest:
    return ReverseRpcRequest(
        version=REVERSE_RPC_VERSION,
        rpc_id=rpc_id,
        method=method,
        payload={"value": 1},
        timeout_ms=1000,
        origin=ReverseRpcOrigin(request_id="request-1"),
        route=ReverseRpcRoute(channel_id="test"),
    )


def test_capability_registry_rejects_duplicate_method() -> None:
    registry = CapabilityRegistry()
    spec = CapabilitySpec(method="test.echo", handler=EchoHandler())
    registry.register(spec)
    with pytest.raises(RuntimeError, match="duplicate"):
        registry.register(spec)


@pytest.mark.asyncio
async def test_dispatcher_success_error_unknown_method_and_cleanup() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(method="test.echo", handler=EchoHandler()))
    registry.register(CapabilitySpec(method="test.error", handler=ErrorHandler()))
    transport = FakeResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)

    await dispatcher.handle(build_request_wire(_request()))
    assert transport.sent[-1][0].result["value"] == 1

    await dispatcher.handle(build_request_wire(_request("test.error", "rpc-2")))
    assert transport.sent[-1][0].error.code == "TEST_FAILURE"

    await dispatcher.handle(build_request_wire(_request("test.missing", "rpc-3")))
    assert transport.sent[-1][0].error.code == "METHOD_NOT_FOUND"
    assert dispatcher.execution_count == 0


@pytest.mark.asyncio
async def test_dispatcher_reports_unsupported_version_when_rpc_id_is_usable() -> None:
    registry = CapabilityRegistry()
    transport = FakeResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)
    wire = build_request_wire(_request())
    wire["body"]["version"] = REVERSE_RPC_VERSION + 1

    await dispatcher.handle(wire)

    assert len(transport.sent) == 1
    assert transport.sent[0][0].error.code == "UNSUPPORTED_VERSION"


@pytest.mark.asyncio
async def test_dispatcher_cancel_race_completes_once() -> None:
    handler = BlockingHandler()
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(method="test.blocking", handler=handler, supports_cancel=True)
    )
    transport = FakeResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)
    request = _request("test.blocking")
    task = asyncio.create_task(dispatcher.handle(build_request_wire(request)))
    await handler.started.wait()
    await dispatcher.handle(
        build_cancel_wire(
            ReverseRpcCancel(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                reason="test cancel",
            ),
            request,
        )
    )
    await task
    assert handler.cancelled.is_set()
    assert len(transport.sent) == 1
    assert transport.sent[0][0].error.code == "CANCELLED"
    assert dispatcher.execution_count == 0


@pytest.mark.asyncio
async def test_dispatcher_disconnect_cancels_and_drops_stale_response() -> None:
    handler = BlockingHandler()
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            method="test.blocking",
            handler=handler,
            cancel_on_disconnect=True,
        )
    )
    transport = FakeResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)
    task = asyncio.create_task(
        dispatcher.handle(build_request_wire(_request("test.blocking")))
    )
    await handler.started.wait()
    await dispatcher.on_agent_disconnect()
    await task
    assert handler.cancelled.is_set()
    assert transport.sent == []
    assert dispatcher.execution_count == 0


@pytest.mark.asyncio
async def test_dispatcher_tracks_execution_until_response_delivery_finishes() -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilitySpec(method="test.echo", handler=EchoHandler()))
    transport = BlockingResponseTransport("rpc-1")
    dispatcher = ReverseRpcDispatcher(registry, transport, max_in_flight=1)

    first = asyncio.create_task(dispatcher.handle(build_request_wire(_request())))
    await transport.started.wait()
    assert dispatcher.execution_count == 1

    await dispatcher.handle(build_request_wire(_request(rpc_id="rpc-2")))
    assert transport.sent[-1][0].error.code == "REVERSE_RPC_OVERLOADED"
    assert dispatcher.execution_count == 1

    transport.release.set()
    await first
    assert dispatcher.execution_count == 0


@pytest.mark.asyncio
async def test_dispatcher_disconnect_cancels_blocked_response_delivery() -> None:
    registry = CapabilityRegistry()
    registry.register(
        CapabilitySpec(
            method="test.echo",
            handler=EchoHandler(),
            cancel_on_disconnect=False,
        )
    )
    transport = BlockingResponseTransport("rpc-1")
    dispatcher = ReverseRpcDispatcher(registry, transport)

    task = asyncio.create_task(dispatcher.handle(build_request_wire(_request())))
    await transport.started.wait()
    await dispatcher.on_agent_disconnect()
    await task

    assert dispatcher.execution_count == 0


@pytest.mark.asyncio
async def test_response_transport_uses_generic_response_method() -> None:
    class FakeAgentClient:
        def __init__(self) -> None:
            self.envelopes = []

        async def send_request(self, envelope):
            self.envelopes.append(envelope)

    agent_client = FakeAgentClient()
    transport = ReverseRpcResponseTransport(agent_client)
    request = _request()
    model = ReverseRpcResponse(
        version=REVERSE_RPC_VERSION,
        rpc_id=request.rpc_id,
        ok=True,
        result={"done": True},
    )
    await transport.send(model, request)

    envelope = agent_client.envelopes[0]
    assert envelope.method == "reverse_rpc.response"
    assert envelope.params == model.to_dict()


@pytest.mark.asyncio
async def test_response_transport_replaces_oversized_result(monkeypatch) -> None:
    from jiuwenswarm.gateway.reverse_rpc import transport as transport_module

    class FakeAgentClient:
        def __init__(self) -> None:
            self.envelopes = []

        async def send_request(self, envelope):
            self.envelopes.append(envelope)
            return SimpleNamespace(ok=True)

    monkeypatch.setattr(transport_module, "AGENT_WS_SEND_BUDGET_BYTES", 1024)
    agent_client = FakeAgentClient()
    transport = ReverseRpcResponseTransport(agent_client)

    await transport.send(
        ReverseRpcResponse(
            version=REVERSE_RPC_VERSION,
            rpc_id="rpc-large",
            ok=True,
            result={"content": "x" * 4096},
        ),
        _request(rpc_id="rpc-large"),
    )

    params = agent_client.envelopes[0].params
    assert params["ok"] is False
    assert params["error"]["code"] == "RESULT_TOO_LARGE"
    assert "x" * 100 not in str(params)


@pytest.mark.asyncio
async def test_response_transport_replaces_non_json_result() -> None:
    class FakeAgentClient:
        def __init__(self) -> None:
            self.envelopes = []

        async def send_request(self, envelope):
            self.envelopes.append(envelope)
            return SimpleNamespace(ok=True)

    agent_client = FakeAgentClient()
    transport = ReverseRpcResponseTransport(agent_client)
    await transport.send(
        ReverseRpcResponse(
            version=REVERSE_RPC_VERSION,
            rpc_id="rpc-invalid-result",
            ok=True,
            result=object(),
        ),
        _request(rpc_id="rpc-invalid-result"),
    )

    params = agent_client.envelopes[0].params
    assert params["ok"] is False
    assert params["error"]["code"] == "INTERNAL_ERROR"


@pytest.mark.asyncio
async def test_response_transport_ack_timeout_is_bounded() -> None:
    class BlockingAgentClient:
        async def send_request(self, envelope):
            del envelope
            await asyncio.Event().wait()

    transport = ReverseRpcResponseTransport(
        BlockingAgentClient(),
        ack_timeout_seconds=0.01,
    )
    with pytest.raises(ReverseRpcTransportDisconnected, match="acknowledge"):
        await transport.send(
            ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id="rpc-timeout",
                ok=True,
                result={},
            ),
            _request(rpc_id="rpc-timeout"),
        )


@pytest.mark.asyncio
async def test_response_transport_rejects_negative_ack() -> None:
    class RejectingAgentClient:
        async def send_request(self, envelope):
            del envelope
            return SimpleNamespace(ok=False)

    transport = ReverseRpcResponseTransport(RejectingAgentClient())
    with pytest.raises(ReverseRpcTransportDisconnected, match="rejected"):
        await transport.send(
            ReverseRpcResponse(
                version=REVERSE_RPC_VERSION,
                rpc_id="rpc-rejected",
                ok=True,
                result={},
            ),
            _request(rpc_id="rpc-rejected"),
        )
