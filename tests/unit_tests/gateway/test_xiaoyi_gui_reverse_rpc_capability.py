from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.gui_rpc.reverse_rpc import (
    GuiReverseRpcPayload,
    XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
    XIAOYI_GUI_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.codec import (
    build_cancel_wire,
    build_request_wire,
    request_from_wire,
)
from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcCancel,
    ReverseRpcOrigin,
    ReverseRpcRequest,
    ReverseRpcRoute,
)
from jiuwenswarm.gateway.gui_rpc.executor import GuiExecutionError
from jiuwenswarm.gateway.gui_rpc.reverse_rpc import (
    XiaoyiGuiCapability,
    register_xiaoyi_gui_reverse_rpc,
)
from jiuwenswarm.gateway.reverse_rpc import (
    CapabilityError,
    CapabilityRegistry,
    ReverseRpcCapabilityContext,
    ReverseRpcDispatcher,
)
from jiuwenswarm.server.gui_rpc.reverse_rpc import XiaoyiGuiReverseRpcClient
from jiuwenswarm.server.reverse_rpc.client import ReverseRpcClient


def _payload() -> GuiReverseRpcPayload:
    return GuiReverseRpcPayload(
        query="open settings",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
        device_id="device-1",
    )


def _context(
    *,
    route: ReverseRpcRoute | None = None,
    request_id: str | None = "request-1",
) -> ReverseRpcCapabilityContext:
    return ReverseRpcCapabilityContext(
        rpc_id="rrpc-1",
        origin=ReverseRpcOrigin(
            execution_id="invocation-1",
            request_id=request_id,
            session_id="jiuwen-1",
            channel_id="xiaoyi",
        ),
        route=route or ReverseRpcRoute(channel_id="xiaoyi", app_id="app-1"),
        timeout_seconds=30.0,
        connection_generation=0,
    )


class FakeChannel:
    def __init__(self, *, ready: bool = True) -> None:
        self.is_ready = ready


class FakeChannelManager:
    def __init__(self, channels: dict[str, FakeChannel]) -> None:
        self.channels = channels

    def get_by_key(self, key):
        if key.channel_id != "xiaoyi":
            return None
        return self.channels.get(key.app_id)

    def get_channels_by_id(self, channel_id):
        return list(self.channels.values()) if channel_id == "xiaoyi" else []


class SuccessfulExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, request, *, channel):
        self.calls.append((request, channel))
        return "done"


@pytest.mark.asyncio
async def test_gui_capability_builds_legacy_business_request() -> None:
    channel = FakeChannel()
    executor = SuccessfulExecutor()
    capability = XiaoyiGuiCapability(
        FakeChannelManager({"app-1": channel}),
        executor,  # type: ignore[arg-type]
    )

    result = await capability.handle(_context(), _payload().to_dict())

    assert result == {"rpc_id": "rrpc-1", "result": "done"}
    request, resolved_channel = executor.calls[0]
    assert resolved_channel is channel
    assert request.rpc_id == "rrpc-1"
    assert request.source_request_id == "request-1"
    assert request.jiuwen_session_id == "jiuwen-1"
    assert request.xiaoyi_task_id == "xiaoyi-task-1"
    assert 0 < request.deadline


@pytest.mark.asyncio
async def test_gui_capability_executes_real_executor_on_resolved_channel() -> None:
    class ExecutingChannel(FakeChannel):
        def __init__(self) -> None:
            super().__init__()
            self.gui_tool_lock = asyncio.Lock()
            self.handlers = []
            self.sent = []

        def register_gui_agent_handler(self, handler):
            self.handlers.append(handler)

        def unregister_gui_agent_handler(self, handler):
            self.handlers.remove(handler)

        async def send_xiaoyi_phone_tools_command(self, **kwargs):
            self.sent.append(kwargs)
            frame = {
                "_xiaoyi_session_id": "xiaoyi-session-1",
                "payload": {
                    "interactionId": "xiaoyi-task-1",
                    "isFinal": True,
                    "streamInfo": {"streamContent": "done"},
                },
            }
            for handler in list(self.handlers):
                handler(frame)
            return True

    channel = ExecutingChannel()
    capability = XiaoyiGuiCapability(FakeChannelManager({"app-1": channel}))

    result = await capability.handle(_context(), _payload().to_dict())

    assert result == {"rpc_id": "rrpc-1", "result": "done"}
    assert channel.sent[0]["message_id"] == "xiaoyi-message-1"
    assert channel.handlers == []


@pytest.mark.asyncio
async def test_gui_capability_route_and_origin_are_fail_closed() -> None:
    capability = XiaoyiGuiCapability(
        FakeChannelManager(
            {"app-1": FakeChannel(), "app-2": FakeChannel()}
        ),
        SuccessfulExecutor(),  # type: ignore[arg-type]
    )
    with pytest.raises(CapabilityError, match="ambiguous") as ambiguous:
        await capability.handle(
            _context(route=ReverseRpcRoute(channel_id="xiaoyi")),
            _payload().to_dict(),
        )
    assert ambiguous.value.code == "ROUTE_NOT_FOUND"

    with pytest.raises(CapabilityError, match="origin.request_id") as invalid:
        await capability.handle(
            _context(request_id=None),
            _payload().to_dict(),
        )
    assert invalid.value.code == "INVALID_REQUEST"

    not_ready = XiaoyiGuiCapability(
        FakeChannelManager({"app-1": FakeChannel(ready=False)}),
        SuccessfulExecutor(),  # type: ignore[arg-type]
    )
    with pytest.raises(CapabilityError, match="active device") as offline:
        await not_ready.handle(_context(), _payload().to_dict())
    assert offline.value.code == "CHANNEL_NOT_READY"


@pytest.mark.asyncio
async def test_gui_capability_maps_executor_error() -> None:
    class FailingExecutor:
        async def execute(self, request, *, channel):
            del request, channel
            raise GuiExecutionError("SEND_FAILED", "Jarvis send failed")

    capability = XiaoyiGuiCapability(
        FakeChannelManager({"app-1": FakeChannel()}),
        FailingExecutor(),  # type: ignore[arg-type]
    )
    with pytest.raises(CapabilityError) as exc_info:
        await capability.handle(_context(), _payload().to_dict())
    assert exc_info.value.code == "SEND_FAILED"
    assert exc_info.value.details == {"rpc_id": "rrpc-1"}


def test_gui_capability_registration_declares_cancel_policy() -> None:
    registry = CapabilityRegistry()
    register_xiaoyi_gui_reverse_rpc(
        registry,
        FakeChannelManager({"app-1": FakeChannel()}),
    )
    spec = registry.resolve(XIAOYI_GUI_REVERSE_RPC_METHOD)
    assert spec is not None
    assert spec.supports_cancel is True
    assert spec.cancel_on_disconnect is True
    assert spec.max_timeout_seconds == XIAOYI_GUI_MAX_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_gui_capability_is_cancelled_by_generic_dispatcher() -> None:
    class BlockingExecutor:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def execute(self, request, *, channel):
            del request, channel
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    executor = BlockingExecutor()
    registry = CapabilityRegistry()
    registry.register(
        _gui_spec(
            XiaoyiGuiCapability(
                FakeChannelManager({"app-1": FakeChannel()}),
                executor,  # type: ignore[arg-type]
            )
        )
    )

    class ResponseTransport:
        def __init__(self) -> None:
            self.responses = []

        async def send(self, response, request):
            del request
            self.responses.append(response)

    transport = ResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)
    request = _generic_request()
    task = asyncio.create_task(dispatcher.handle(build_request_wire(request)))
    await executor.started.wait()
    await dispatcher.handle(
        build_cancel_wire(
            ReverseRpcCancel(
                version=REVERSE_RPC_VERSION,
                rpc_id=request.rpc_id,
                reason="tool cancelled",
            ),
            request,
        )
    )
    await task

    assert executor.cancelled.is_set()
    assert transport.responses[0].error.code == "CANCELLED"


@pytest.mark.asyncio
async def test_gui_executor_cancellation_preserves_generic_timeout_code() -> None:
    class CancellationTranslatingExecutor:
        async def execute(self, request, *, channel):
            del request, channel
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError as exc:
                raise GuiExecutionError(
                    "CANCELLED",
                    "GUI RPC execution was cancelled",
                ) from exc

    registry = CapabilityRegistry()
    registry.register(
        _gui_spec(
            XiaoyiGuiCapability(
                FakeChannelManager({"app-1": FakeChannel()}),
                CancellationTranslatingExecutor(),  # type: ignore[arg-type]
            )
        )
    )

    class ResponseTransport:
        def __init__(self) -> None:
            self.responses = []

        async def send(self, response, request):
            del request
            self.responses.append(response)

    transport = ResponseTransport()
    dispatcher = ReverseRpcDispatcher(registry, transport)

    await dispatcher.handle(build_request_wire(_generic_request(timeout_ms=10)))

    assert transport.responses[0].error.code == "TIMEOUT"


@pytest.mark.asyncio
async def test_gui_typed_client_and_gateway_capability_loopback() -> None:
    executor = SuccessfulExecutor()
    registry = CapabilityRegistry()
    registry.register(
        _gui_spec(
            XiaoyiGuiCapability(
                FakeChannelManager({"app-1": FakeChannel()}),
                executor,  # type: ignore[arg-type]
            )
        )
    )
    generic_client = ReverseRpcClient()

    class LoopbackResponseTransport:
        async def send(self, response, request):
            del request
            assert generic_client.complete(response)

    dispatcher = ReverseRpcDispatcher(registry, LoopbackResponseTransport())

    class LoopbackRequestTransport:
        def __init__(self) -> None:
            self.requests = []

        async def send(self, wire, route):
            del route
            self.requests.append(request_from_wire(wire))
            await dispatcher.handle(wire)

    request_transport = LoopbackRequestTransport()
    generic_client.set_transport(request_transport)
    client = XiaoyiGuiReverseRpcClient(generic_client)
    response = await client.call(
        query="open settings",
        source_request_id="request-1",
        jiuwen_session_id="jiuwen-1",
        xiaoyi_session_id="xiaoyi-session-1",
        xiaoyi_task_id="xiaoyi-task-1",
        xiaoyi_message_id="xiaoyi-message-1",
        app_id="app-1",
    )

    assert response.success is True
    assert response.result == "done"
    assert response.rpc_id == request_transport.requests[0].rpc_id
    assert executor.calls[0][0].rpc_id == response.rpc_id


def _generic_request(timeout_ms: int = 30_000) -> ReverseRpcRequest:
    return ReverseRpcRequest(
        version=REVERSE_RPC_VERSION,
        rpc_id="rrpc-cancel",
        method=XIAOYI_GUI_REVERSE_RPC_METHOD,
        payload=_payload().to_dict(),
        timeout_ms=timeout_ms,
        origin=ReverseRpcOrigin(
            request_id="request-1",
            session_id="jiuwen-1",
            channel_id="xiaoyi",
        ),
        route=ReverseRpcRoute(channel_id="xiaoyi", app_id="app-1"),
    )


def _gui_spec(handler):
    from jiuwenswarm.gateway.reverse_rpc import CapabilitySpec

    return CapabilitySpec(
        method=XIAOYI_GUI_REVERSE_RPC_METHOD,
        handler=handler,
        supports_cancel=True,
        cancel_on_disconnect=True,
        max_timeout_seconds=XIAOYI_GUI_MAX_TIMEOUT_SECONDS,
    )
