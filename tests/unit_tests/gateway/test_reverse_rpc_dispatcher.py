from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.reverse_rpc.codec import build_cancel_wire, build_request_wire
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
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
