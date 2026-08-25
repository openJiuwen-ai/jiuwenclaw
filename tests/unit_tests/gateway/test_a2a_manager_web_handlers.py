import pytest

from jiuwenswarm.gateway.a2a_manager import A2AIngressConfig, A2AManager
from jiuwenswarm.gateway.channel_manager.web.app_web_handlers import (
    WebHandlersBindParams,
    _register_web_handlers,
)
from jiuwenswarm.gateway.channel_manager.web.web_http_routes import MAPPED_ROUTES


class _WebChannelProbe:
    def __init__(self) -> None:
        self.methods = {}
        self.responses = []

    def register_method(self, name, handler) -> None:
        self.methods[name] = handler

    def on_connect(self, handler) -> None:
        self.on_connect_handler = handler

    async def send_response(self, ws, req_id, *, ok, payload=None, error=None, code=None) -> None:
        self.responses.append({"id": req_id, "ok": ok, "payload": payload, "error": error, "code": code})


class _ChannelManagerProbe:
    def register_channel(self, channel) -> None:
        return None

    def unregister_channel(self, channel_id) -> None:
        return None


class _RepositoryProbe:
    def save(self, config) -> None:
        return None


class _ChannelProbe:
    channel_id = "a2a"

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


@pytest.mark.asyncio
async def test_a2a_ingress_web_handlers_return_snapshots():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(), object(), A2AIngressConfig(), repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.get"](object(), "get", {}, "session")
    await channel.methods["a2a.ingress.update"](
        object(), "update", {"port": 19123}, "session"
    )
    await channel.methods["a2a.ingress.enable"](object(), "enable", {}, "session")

    assert channel.responses[0]["payload"]["state"] == "disabled"
    assert channel.responses[1]["payload"]["desired_port"] == 19123
    assert channel.responses[2]["payload"]["state"] == "running"


@pytest.mark.asyncio
async def test_a2a_ingress_update_apply_disables_the_running_service():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(), object(), A2AIngressConfig(), repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.enable"](object(), "enable", {}, "session")
    await channel.methods["a2a.ingress.update"](
        object(), "update", {"config": {"enabled": False}, "apply": True}, "session"
    )

    assert channel.responses[-1]["ok"] is True
    assert channel.responses[-1]["payload"]["state"] == "disabled"
    assert channel.responses[-1]["payload"]["effective_rpc_url"] is None


@pytest.mark.asyncio
async def test_a2a_ingress_handler_returns_operation_error_snapshot():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(), object(), A2AIngressConfig(), repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.update"](
        object(), "update", {"config": {"rpc_path": "not-absolute"}, "apply": True}, "session"
    )

    assert channel.responses[-1]["ok"] is False
    assert channel.responses[-1]["code"] == "A2A_CONFIG_INVALID"
    assert channel.responses[-1]["payload"]["desired_rpc_path"] == "/a2a"


def test_a2a_ingress_http_routes_map_to_rpc_methods():
    routes = {(route.http_method, route.path): route.rpc_method for route in MAPPED_ROUTES}

    assert routes[("GET", "/a2a/ingress")] == "a2a.ingress.get"
    assert routes[("PATCH", "/a2a/ingress")] == "a2a.ingress.update"
    assert routes[("POST", "/a2a/ingress:enable")] == "a2a.ingress.enable"
    assert routes[("POST", "/a2a/ingress:disable")] == "a2a.ingress.disable"
    assert routes[("POST", "/a2a/ingress:reload")] == "a2a.ingress.reload"
