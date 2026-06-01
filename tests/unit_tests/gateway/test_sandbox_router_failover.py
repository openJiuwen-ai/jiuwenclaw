"""Cross-gateway sandbox routing: DCS adopt, NX race, terminate clears mapping."""
from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.e2a.models import E2AEnvelope

# Avoid loading gateway/__init__.py (pulls agentserver → broken optional deps in some envs).
_repo_root = Path(__file__).resolve().parents[3]
_gw_pkg_path = _repo_root / "jiuwenclaw" / "gateway"
if "jiuwenclaw.gateway" not in sys.modules:
    _gw_pkg = types.ModuleType("jiuwenclaw.gateway")
    _gw_pkg.__path__ = [str(_gw_pkg_path)]
    sys.modules["jiuwenclaw.gateway"] = _gw_pkg

_sandbox_router_mod = importlib.import_module("jiuwenclaw.gateway.sandbox_router")
SandboxRouterAgentClient = _sandbox_router_mod.SandboxRouterAgentClient
from jiuwenclaw.sandbox.open_ability import OpenAbilityConfig, OpenAbilityEndpoint
from jiuwenclaw.sandbox.sandbox_client import ExecutionResult
from jiuwenclaw.sandbox.sandbox_routing_dcs_store import SandboxRoutingRecord


class FakeAgentClient:
    def __init__(self) -> None:
        self.disconnected = False
        self.request_ids: list[str] = []

    async def send_request(self, envelope: E2AEnvelope) -> Any:
        self.request_ids.append(str(envelope.request_id))
        return MagicMock(ok=True, request_id=envelope.request_id, channel_id=envelope.channel or "")

    async def send_request_stream(self, envelope: E2AEnvelope):
        yield MagicMock(request_id=envelope.request_id, channel_id=envelope.channel or "", is_complete=True)

    def set_or_update_server_config(self, *, config: dict, env: dict | None = None) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeOpenAbilityClient(FakeAgentClient):
    instances: list["FakeOpenAbilityClient"] = []

    def __init__(self, sandbox_id: str, *, request_timeout_seconds: float = 600.0) -> None:
        super().__init__()
        self.sandbox_id = sandbox_id
        self.request_timeout_seconds = request_timeout_seconds
        self.connected_uri: str | None = None
        self._connection_lost_handler = None
        self._server_push_handler = None
        FakeOpenAbilityClient.instances.append(self)

    async def connect(self, uri: str) -> None:
        self.connected_uri = uri

    def set_server_push_handler(self, handler) -> None:
        self._server_push_handler = handler

    def set_connection_lost_handler(self, handler) -> None:
        self._connection_lost_handler = handler

    async def emit_connection_lost(self, payload: dict[str, Any]) -> None:
        assert self._connection_lost_handler is not None
        await self._connection_lost_handler(payload)


@pytest.fixture
def router() -> SandboxRouterAgentClient:
    router = SandboxRouterAgentClient(
        max_sandboxes=5,
        adopt_existing=True,
        gateway_instance_id="gw-test",
    )
    router._open_ability_config = OpenAbilityConfig(ws_path="/ws")
    return router


@pytest.fixture
def mock_sandbox_client(router: SandboxRouterAgentClient):
    client = MagicMock()
    client.create_sandbox = AsyncMock(
        return_value=ExecutionResult(success=True, output="sb-new", error=None)
    )
    client.delete_sandbox = AsyncMock(return_value=ExecutionResult(success=True, output="", error=None))
    client.close = AsyncMock()
    router._sandbox_client = client
    return client


@pytest.fixture
def mock_dcs_store(router: SandboxRouterAgentClient):
    store = MagicMock()
    store.get_openability_endpoint = AsyncMock(
        return_value=OpenAbilityEndpoint(host="127.0.0.1", port=9001)
    )
    store.save_sandbox = AsyncMock()
    store.delete_sandbox = AsyncMock()
    store.close = AsyncMock()
    router._dcs_store = store
    return store


@pytest.fixture
def mock_routing_store(router: SandboxRouterAgentClient):
    store = MagicMock()
    store.get_routing = AsyncMock(return_value=None)
    store.set_routing_nx = AsyncMock(return_value=True)
    store.delete_routing = AsyncMock()
    store.close = AsyncMock()
    router._routing_dcs_store = store
    return store


def _envelope(*, user_id: str = "user-a", session_id: str = "sess-a") -> E2AEnvelope:
    return E2AEnvelope(
        request_id="req-1",
        channel="vibeskill",
        session_id=session_id,
        user_id=user_id,
        method="skilldev.chat",
        params={"task_id": session_id},
        is_stream=False,
    )


@pytest.mark.asyncio
async def test_adopt_existing_skips_create_sandbox(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_routing_store.get_routing.return_value = SandboxRoutingRecord(
        routing_key="vibeskill:user:user-a",
        sandbox_id="sb-existing",
        gateway_id="gw-1",
        updated_at=1.0,
    )
    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(router, "_register_sandbox_record", AsyncMock())

    runtime = await router._acquire_runtime(_envelope())

    assert runtime.sandbox_id == "sb-existing"
    mock_sandbox_client.create_sandbox.assert_not_called()
    router._register_sandbox_record.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_stale_routing_without_endpoint_creates_new(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_routing_store.get_routing.return_value = SandboxRoutingRecord(
        routing_key="vibeskill:user:user-a",
        sandbox_id="sb-dead",
        gateway_id="gw-1",
        updated_at=1.0,
    )
    mock_dcs_store.get_openability_endpoint.return_value = None
    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())

    assert runtime.sandbox_id == "sb-new"
    mock_routing_store.delete_routing.assert_called_with("vibeskill:user:user-a")
    mock_sandbox_client.create_sandbox.assert_called_once()


@pytest.mark.asyncio
async def test_nx_race_loser_adopts_winner(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_routing_store.set_routing_nx.return_value = False

    call_count = 0

    async def get_routing_side_effect(routing_key: str):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return SandboxRoutingRecord(
                routing_key=routing_key,
                sandbox_id="sb-peer",
                gateway_id="gw-peer",
                updated_at=2.0,
            )
        return None

    mock_routing_store.get_routing.side_effect = get_routing_side_effect

    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(router, "_register_sandbox_record", AsyncMock())

    runtime = await router._acquire_runtime(_envelope())

    assert runtime.sandbox_id == "sb-peer"
    mock_sandbox_client.create_sandbox.assert_called_once()
    mock_sandbox_client.delete_sandbox.assert_called_with("sb-new")
    router._register_sandbox_record.assert_not_called()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_terminate_deletes_routing_mapping(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())
    await router._terminate_runtime(runtime.routing_key)

    mock_routing_store.delete_routing.assert_called_with("vibeskill:user:user-a")
    assert "vibeskill:user:user-a" not in router._runtimes


@pytest.mark.asyncio
async def test_terminate_skips_dcs_when_sandbox_delete_fails(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_sandbox_client.delete_sandbox = AsyncMock(
        return_value=ExecutionResult(success=False, output="", error="network error")
    )
    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())
    await router._terminate_runtime(runtime.routing_key)

    mock_dcs_store.delete_sandbox.assert_not_called()
    mock_routing_store.delete_routing.assert_not_called()
    assert "vibeskill:user:user-a" not in router._runtimes


@pytest.mark.asyncio
async def test_adopt_disabled_always_creates(
    mock_sandbox_client,
    mock_dcs_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    router = SandboxRouterAgentClient(
        max_sandboxes=5,
        adopt_existing=False,
        gateway_instance_id="gw-test",
    )
    routing_store = MagicMock()
    routing_store.get_routing = AsyncMock(
        return_value=SandboxRoutingRecord(
            routing_key="vibeskill:user:user-a",
            sandbox_id="sb-existing",
            gateway_id="gw-1",
            updated_at=1.0,
        )
    )
    router._routing_dcs_store = routing_store
    router._dcs_store = mock_dcs_store
    router._sandbox_client = mock_sandbox_client

    fake_client = FakeAgentClient()
    monkeypatch.setattr(
        router,
        "_connect_open_ability_client",
        AsyncMock(return_value=fake_client),
    )
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())
    assert runtime.sandbox_id == "sb-new"
    routing_store.get_routing.assert_not_called()


@pytest.mark.asyncio
async def test_oa_physical_disconnect_refreshes_runtime_client(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAbilityClient.instances.clear()
    monkeypatch.setattr(_sandbox_router_mod, "OpenAbilityWebSocketClient", FakeOpenAbilityClient)
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())

    assert len(FakeOpenAbilityClient.instances) == 1
    first_client = FakeOpenAbilityClient.instances[0]
    assert runtime.agent_client is first_client

    mock_dcs_store.get_openability_endpoint.side_effect = [
        OpenAbilityEndpoint(host="127.0.0.1", port=9001),
        OpenAbilityEndpoint(host="127.0.0.1", port=9001),
        OpenAbilityEndpoint(host="127.0.0.1", port=9002),
    ]

    await first_client.emit_connection_lost({"event": "openability.connection_lost"})

    assert len(FakeOpenAbilityClient.instances) == 2
    second_client = FakeOpenAbilityClient.instances[1]
    assert runtime.agent_client is second_client
    assert second_client.connected_uri == "ws://127.0.0.1:9002/ws"
    assert first_client.disconnected is True
    assert runtime.metadata.get("openability_reconnect_required") is False


@pytest.mark.asyncio
async def test_oa_physical_disconnect_drops_runtime_when_refresh_fails(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAbilityClient.instances.clear()
    monkeypatch.setattr(_sandbox_router_mod, "OpenAbilityWebSocketClient", FakeOpenAbilityClient)
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())
    first_client = FakeOpenAbilityClient.instances[0]
    mock_dcs_store.get_openability_endpoint.side_effect = RuntimeError("no fresh endpoint")

    await first_client.emit_connection_lost({"event": "openability.connection_lost"})

    assert runtime.routing_key not in router._runtimes
    assert first_client.disconnected is True


@pytest.mark.asyncio
async def test_requests_are_buffered_during_oa_reconnect(
    router: SandboxRouterAgentClient,
    mock_sandbox_client,
    mock_dcs_store,
    mock_routing_store,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeOpenAbilityClient.instances.clear()
    monkeypatch.setattr(_sandbox_router_mod, "OpenAbilityWebSocketClient", FakeOpenAbilityClient)
    monkeypatch.setattr(
        router,
        "_register_sandbox_record",
        AsyncMock(return_value={"sandbox_id": "sb-new", "api_key": "k", "api_key_sha256": "h"}),
    )

    runtime = await router._acquire_runtime(_envelope())
    first_client = FakeOpenAbilityClient.instances[0]
    reconnect_gate = asyncio.Event()

    async def delayed_connect_open_ability_client(
        sandbox_id: str,
        routing_key: str,
        metadata: dict[str, Any],
    ):
        if metadata.get("openability_reconnect_required"):
            await reconnect_gate.wait()
        return await SandboxRouterAgentClient._connect_open_ability_client(
            router,
            sandbox_id,
            routing_key,
            metadata,
        )

    monkeypatch.setattr(router, "_connect_open_ability_client", delayed_connect_open_ability_client)
    mock_dcs_store.get_openability_endpoint.side_effect = [
        OpenAbilityEndpoint(host="127.0.0.1", port=9001),
        OpenAbilityEndpoint(host="127.0.0.1", port=9002),
    ]

    disconnect_task = asyncio.create_task(
        first_client.emit_connection_lost({"event": "openability.connection_lost"})
    )
    await asyncio.sleep(0.01)

    buffered_request = _envelope(user_id="user-a", session_id="")
    buffered_request.request_id = "req-buffered"
    send_task = asyncio.create_task(router.send_request(buffered_request))

    await asyncio.sleep(0.01)
    assert len(router._reconnect_waiters.get(runtime.routing_key, ())) == 1
    assert first_client.request_ids == []

    reconnect_gate.set()
    await disconnect_task
    response = await send_task

    second_client = FakeOpenAbilityClient.instances[1]
    assert response.request_id == "req-buffered"
    assert runtime.agent_client is second_client
    assert second_client.request_ids == ["req-buffered"]
    assert first_client.request_ids == []
    assert runtime.routing_key not in router._reconnect_waiters
