from __future__ import annotations

import pytest

from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.device_reverse_rpc import (
    XiaoyiDeviceReverseRpcClient,
)
from jiuwenswarm.common.device_rpc.models import DeviceCommandContext
from jiuwenswarm.common.device_rpc.reverse_rpc import (
    DeviceReverseRpcPayload,
    XIAOYI_DEVICE_MAX_TIMEOUT_SECONDS,
    XIAOYI_DEVICE_REVERSE_RPC_METHOD,
)
from jiuwenswarm.common.reverse_rpc.codec import request_from_wire
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcOrigin,
    ReverseRpcRoute,
)
from jiuwenswarm.gateway.gateway_push.xiaoyi_device_command_handler import (
    execute_device_command_request,
)
from jiuwenswarm.gateway.gateway_push.xiaoyi_device_reverse_rpc import (
    XiaoyiDeviceCapability,
    register_xiaoyi_device_reverse_rpc,
)
from jiuwenswarm.gateway.reverse_rpc import (
    CapabilityError,
    CapabilityRegistry,
    ReverseRpcCapabilityContext,
    ReverseRpcDispatcher,
)
from jiuwenswarm.server.reverse_rpc.client import ReverseRpcClient


def _device_context(*, channel_id: str = "xiaoyi") -> DeviceCommandContext:
    return DeviceCommandContext(
        source_request_id="request-1",
        channel_id=channel_id,
        jiuwen_session_id="session-1",
        xiaoyi_root_session_id="root-1",
        xiaoyi_params_session_id=None,
        xiaoyi_task_id="task-1",
        xiaoyi_rpc_id="message-1",
        metadata={"app_id": "app-1"},
    )


def _payload(*, channel_id: str = "xiaoyi") -> DeviceReverseRpcPayload:
    return DeviceReverseRpcPayload(
        operation_id="operation-1",
        intent_name="CreateNote",
        command={"title": "test"},
        context=_device_context(channel_id=channel_id),
    )


def _capability_context(
    *,
    route: ReverseRpcRoute | None = None,
) -> ReverseRpcCapabilityContext:
    return ReverseRpcCapabilityContext(
        rpc_id="rrpc-1",
        origin=ReverseRpcOrigin(request_id="request-1"),
        route=route or ReverseRpcRoute(channel_id="xiaoyi", app_id="app-1"),
        timeout_seconds=30.0,
        connection_generation=0,
    )


class FakeChannel:
    def __init__(self, *, ready: bool = True) -> None:
        self.is_ready = ready
        self.requests = []
        self.login_calls = []

    async def execute_phone_tool_command(self, *, request):
        self.requests.append(("normal", request))
        return {"created": True}

    async def execute_scheduled_phone_tool_command(self, *, request):
        self.requests.append(("scheduled", request))
        return {"scheduled": True}

    async def send_login_token_artifact(self, **kwargs):
        self.login_calls.append(kwargs)
        return True


class FakeChannelManager:
    def __init__(self, channels: dict[str, FakeChannel]) -> None:
        self.channels = channels

    def get_by_key(self, key):
        if key.channel_id != "xiaoyi":
            return None
        return self.channels.get(key.app_id)

    def get_channels_by_id(self, channel_id):
        return list(self.channels.values()) if channel_id == "xiaoyi" else []


@pytest.mark.asyncio
async def test_device_capability_resolves_exact_channel_and_executes() -> None:
    channel = FakeChannel()
    capability = XiaoyiDeviceCapability(FakeChannelManager({"app-1": channel}))

    result = await capability.handle(
        _capability_context(),
        _payload().to_dict(),
    )

    assert result == {
        "rpc_id": "rrpc-1",
        "operation_id": "operation-1",
        "result": {"created": True},
    }
    mode, request = channel.requests[0]
    assert mode == "normal"
    assert request.rpc_id == "rrpc-1"
    assert request.operation_id == "operation-1"
    assert request.timeout_seconds == 30.0


@pytest.mark.asyncio
async def test_device_capability_route_is_fail_closed() -> None:
    channel = FakeChannel()
    capability = XiaoyiDeviceCapability(
        FakeChannelManager({"app-1": channel, "app-2": FakeChannel()})
    )

    with pytest.raises(CapabilityError, match="ambiguous") as ambiguous:
        await capability.handle(
            _capability_context(route=ReverseRpcRoute(channel_id="xiaoyi")),
            _payload().to_dict(),
        )
    assert ambiguous.value.code == "ROUTE_NOT_FOUND"

    with pytest.raises(CapabilityError, match="active device") as not_ready:
        await XiaoyiDeviceCapability(
            FakeChannelManager({"app-1": FakeChannel(ready=False)})
        ).handle(_capability_context(), _payload().to_dict())
    assert not_ready.value.code == "CHANNEL_NOT_READY"


@pytest.mark.asyncio
async def test_extracted_device_executor_preserves_special_paths() -> None:
    channel = FakeChannel()
    scheduled = _payload(channel_id="__cron__")
    scheduled_request = _request_from_payload(scheduled)
    scheduled_response = await execute_device_command_request(
        scheduled_request,
        channel,
    )
    assert scheduled_response.result == {"scheduled": True}
    assert channel.requests[-1][0] == "scheduled"

    login_payload = DeviceReverseRpcPayload(
        operation_id="operation-login",
        intent_name="GetLoginToken",
        command={"client_id": "client-1", "skill_name": "skill-1"},
        context=_device_context(),
    )
    login_response = await execute_device_command_request(
        _request_from_payload(login_payload),
        channel,
    )
    assert login_response.result == {"sent": True}
    assert channel.login_calls[-1]["message_id"] == "message-1"


def test_device_capability_registration_declares_business_policy() -> None:
    registry = CapabilityRegistry()
    register_xiaoyi_device_reverse_rpc(
        registry,
        FakeChannelManager({"app-1": FakeChannel()}),
    )
    spec = registry.resolve(XIAOYI_DEVICE_REVERSE_RPC_METHOD)
    assert spec is not None
    assert spec.supports_cancel is False
    assert spec.cancel_on_disconnect is True
    assert spec.max_timeout_seconds == XIAOYI_DEVICE_MAX_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_device_typed_client_and_gateway_capability_loopback() -> None:
    channel = FakeChannel()
    registry = CapabilityRegistry()
    register_xiaoyi_device_reverse_rpc(
        registry,
        FakeChannelManager({"app-1": channel}),
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
    client = XiaoyiDeviceReverseRpcClient(generic_client)
    response = await client.call(
        intent_name="CreateNote",
        command={"title": "test"},
        context=_device_context(),
    )

    assert response.ok is True
    assert response.result == {"created": True}
    generic_request = request_transport.requests[0]
    business_request = channel.requests[0][1]
    assert generic_request.rpc_id == business_request.rpc_id
    assert generic_request.rpc_id != business_request.operation_id


def _request_from_payload(payload: DeviceReverseRpcPayload):
    from jiuwenswarm.common.device_rpc.models import DeviceCommandRequest

    return DeviceCommandRequest(
        rpc_id="rrpc-1",
        operation_id=payload.operation_id,
        intent_name=payload.intent_name,
        command=payload.command,
        context=payload.context,
        timeout_seconds=30.0,
    )
