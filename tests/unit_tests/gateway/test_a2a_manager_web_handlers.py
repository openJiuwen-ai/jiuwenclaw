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

    async def send_response(
        self, ws, req_id, *, ok, payload=None, error=None, code=None
    ) -> None:
        self.responses.append(
            {"id": req_id, "ok": ok, "payload": payload, "error": error, "code": code}
        )


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


class _OutboundRegistryProbe:
    async def discover(self, url, card_path=None):
        return {"discovery_id": "disc-1", "url": url, "card_path": card_path}

    async def register(self, params):
        return {"agent_id": "agent-1", "display_name": params.get("display_name")}

    async def list_agents(self):
        return {"items": [], "total": 0}

    async def get_agent(self, agent_id):
        return {"agent_id": agent_id}

    async def update_agent(self, agent_id, params):
        return {"agent_id": agent_id, **params}

    async def refresh_agent(self, agent_id):
        return {"agent_id": agent_id, "refreshed": True}

    async def confirm_revision(self, agent_id, *, accept=True):
        return {"agent_id": agent_id, "accepted": accept}

    async def delete_agent(self, agent_id):
        return {"agent_id": agent_id, "deleted": True}

    async def get_dispatch(self, dispatch_id):
        return {"dispatch_id": dispatch_id}


@pytest.mark.asyncio
async def test_a2a_ingress_web_handlers_return_snapshots():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.get"](object(), "get", {}, "session")
    await channel.methods["a2a.ingress.history"](
        object(), "history", {"limit": 20}, "session"
    )
    await channel.methods["a2a.ingress.update"](
        object(), "update", {"port": 19123}, "session"
    )
    await channel.methods["a2a.ingress.enable"](object(), "enable", {}, "session")

    assert channel.responses[0]["payload"]["state"] == "disabled"
    assert channel.responses[1]["payload"] == {"items": [], "total": 0}
    assert channel.responses[2]["payload"]["desired_port"] == 19123
    assert channel.responses[3]["payload"]["state"] == "running"


@pytest.mark.asyncio
async def test_a2a_ingress_update_apply_disables_the_running_service():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
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
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.update"](
        object(),
        "update",
        {"config": {"rpc_path": "not-absolute"}, "apply": True},
        "session",
    )

    assert channel.responses[-1]["ok"] is False
    assert channel.responses[-1]["code"] == "A2A_CONFIG_INVALID"
    assert channel.responses[-1]["payload"]["desired_rpc_path"] == "/a2a"


@pytest.mark.asyncio
async def test_a2a_ingress_history_rejects_non_integer_limit():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.ingress.history"](
        object(), "history", {"limit": "invalid"}, "session"
    )

    assert channel.responses[-1]["ok"] is False
    assert channel.responses[-1]["code"] == "A2A_CONFIG_INVALID"
    assert channel.responses[-1]["error"] == "limit must be an integer"


@pytest.mark.asyncio
async def test_a2a_ingress_lifecycle_handlers_tolerate_missing_manager():
    channel = _WebChannelProbe()
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=None))

    await channel.methods["a2a.ingress.enable"](object(), "enable", {}, "session")
    await channel.methods["a2a.ingress.disable"](object(), "disable", {}, "session")
    await channel.methods["a2a.ingress.reload"](object(), "reload", {}, "session")

    assert [item["ok"] for item in channel.responses] == [False, False, False]
    assert {item["code"] for item in channel.responses} == {"A2A_BIND_FAILED"}


def test_a2a_ingress_http_routes_map_to_rpc_methods():
    routes = {
        (route.http_method, route.path): route.rpc_method for route in MAPPED_ROUTES
    }

    assert routes[("GET", "/a2a/ingress")] == "a2a.ingress.get"
    assert routes[("GET", "/a2a/ingress/history")] == "a2a.ingress.history"
    assert routes[("PATCH", "/a2a/ingress")] == "a2a.ingress.update"
    assert routes[("POST", "/a2a/ingress:enable")] == "a2a.ingress.enable"
    assert routes[("POST", "/a2a/ingress:disable")] == "a2a.ingress.disable"
    assert routes[("POST", "/a2a/ingress:reload")] == "a2a.ingress.reload"


@pytest.mark.asyncio
async def test_a2a_outbound_web_handlers_expose_management_facade():
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
        outbound_registry=_OutboundRegistryProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.outbound.discover"](
        object(), "discover", {"url": "https://agent.example.com"}, "session"
    )
    await channel.methods["a2a.outbound.register"](
        object(),
        "register",
        {"discovery_id": "disc-1", "display_name": "Agent"},
        "session",
    )
    await channel.methods["a2a.outbound.update"](
        object(), "update", {"agent_id": "agent-1", "enabled": False}, "session"
    )
    await channel.methods["a2a.outbound.confirm_revision"](
        object(), "confirm", {"agent_id": "agent-1", "accept": False}, "session"
    )

    assert channel.responses[0]["payload"]["discovery_id"] == "disc-1"
    assert channel.responses[1]["payload"]["agent_id"] == "agent-1"
    assert channel.responses[2]["payload"]["enabled"] is False
    assert channel.responses[3]["payload"]["accepted"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"agent_id": "agent-1"}, {"agent_id": "agent-1", "accept": "false"}])
async def test_a2a_outbound_confirm_revision_requires_explicit_boolean(params):
    channel = _WebChannelProbe()
    manager = A2AManager(
        _ChannelManagerProbe(),
        object(),
        A2AIngressConfig(),
        repository=_RepositoryProbe(),
        channel_factory=lambda config, router: _ChannelProbe(),
        outbound_registry=_OutboundRegistryProbe(),
    )
    _register_web_handlers(WebHandlersBindParams(channel=channel, a2a_manager=manager))

    await channel.methods["a2a.outbound.confirm_revision"](
        object(), "confirm", params, "session"
    )

    assert channel.responses[-1]["ok"] is False
    assert channel.responses[-1]["code"] == "A2A_OUTBOUND_STORE_INVALID"


def test_a2a_outbound_http_routes_map_to_rpc_methods():
    routes = {
        (route.http_method, route.path): route.rpc_method for route in MAPPED_ROUTES
    }

    assert routes[("POST", "/a2a/outbound/discover")] == "a2a.outbound.discover"
    assert routes[("POST", "/a2a/outbound/agents")] == "a2a.outbound.register"
    assert routes[("GET", "/a2a/outbound/agents")] == "a2a.outbound.list"
    assert routes[("PATCH", "/a2a/outbound/agents/{agent_id}")] == "a2a.outbound.update"
    assert (
        routes[("POST", "/a2a/outbound/agents/{agent_id}:refresh")]
        == "a2a.outbound.refresh"
    )
    assert (
        routes[("POST", "/a2a/outbound/agents/{agent_id}:confirm-revision")]
        == "a2a.outbound.confirm_revision"
    )
    assert (
        routes[("DELETE", "/a2a/outbound/agents/{agent_id}")] == "a2a.outbound.delete"
    )
