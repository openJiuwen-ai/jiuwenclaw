from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.gateway.sandbox_router import SandboxRouterAgentClient
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.message import ReqMethod


class FakeAgentClient(AgentServerClient):
    def __init__(self, sandbox_id: str, *, release_event: asyncio.Event | None = None) -> None:
        self.sandbox_id = sandbox_id
        self._release_event = release_event
        self.requests: list[str] = []
        self.disconnected = False

    async def connect(self, uri: str) -> None:
        return None

    async def disconnect(self) -> None:
        self.disconnected = True

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        return None

    async def send_request(self, envelope) -> AgentResponse:
        self.requests.append(str(envelope.request_id))
        if self._release_event is not None:
            await self._release_event.wait()
        else:
            await asyncio.sleep(0.01)
        return AgentResponse(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            ok=True,
            payload={"sandbox_id": self.sandbox_id},
        )

    async def send_request_stream(self, envelope) -> AsyncIterator[AgentResponseChunk]:
        self.requests.append(str(envelope.request_id))
        yield AgentResponseChunk(
            request_id=str(envelope.request_id),
            channel_id=str(envelope.channel or ""),
            payload={"sandbox_id": self.sandbox_id},
        )


class FakeSandboxClient:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.deleted: list[str] = []

    async def create_sandbox(self) -> str:
        sandbox_id = f"sb-{len(self.created) + 1}"
        self.created.append(sandbox_id)
        return sandbox_id

    async def delete_sandbox(self, sandbox_id: str) -> None:
        self.deleted.append(sandbox_id)

    async def close(self) -> None:
        return None


class SandboxRouterTestDouble(SandboxRouterAgentClient):
    def __init__(
        self,
        sandbox_client: FakeSandboxClient,
        *,
        release_event: asyncio.Event | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._sandbox_client = sandbox_client
        self._release_event = release_event
        self.connected: list[tuple[str, str]] = []
        self.disconnected: list[str] = []
        self.registered: list[str] = []

    async def _register_sandbox_record(self, sandbox_id: str) -> dict[str, str]:
        self.registered.append(sandbox_id)
        return {
            "sandbox_id": sandbox_id,
            "api_key": "SK-TEST",
            "created_at": "2026-05-22 10:00:00",
        }

    async def _wait_agent_connected(
        self,
        sandbox_id: str,
        routing_key: str,
        metadata: dict[str, Any],
    ) -> AgentServerClient:
        self.connected.append((sandbox_id, routing_key))
        return FakeAgentClient(sandbox_id, release_event=self._release_event)

    async def _disconnect_agent_client(self, sandbox_id: str, agent_client: AgentServerClient | None) -> None:
        self.disconnected.append(sandbox_id)
        await super()._disconnect_agent_client(sandbox_id, agent_client)


def _env(request_id: str, *, user_id: str | None = None, session_id: str = "sess-1"):
    return e2a_from_agent_fields(
        request_id=request_id,
        channel_id="vibeskill",
        session_id=session_id,
        user_id=user_id,
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "hello"},
        is_stream=False,
    )


@pytest.mark.asyncio
async def test_router_reuses_one_sandbox_for_same_user_across_sessions():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterTestDouble(sandbox_client)

    resp1 = await router.send_request(_env("r1", user_id="user-1", session_id="sess-a"))
    resp2 = await router.send_request(_env("r2", user_id="user-1", session_id="sess-b"))

    assert len(sandbox_client.created) == 1
    assert router.registered == ["sb-1"]
    assert resp1.payload == {"sandbox_id": "sb-1"}
    assert resp2.payload == {"sandbox_id": "sb-1"}
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_creates_distinct_sandboxes_for_distinct_users():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterTestDouble(sandbox_client)

    resp1 = await router.send_request(_env("r1", user_id="user-1"))
    resp2 = await router.send_request(_env("r2", user_id="user-2"))

    assert len(sandbox_client.created) == 2
    assert resp1.payload == {"sandbox_id": "sb-1"}
    assert resp2.payload == {"sandbox_id": "sb-2"}
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_concurrent_same_user_creates_once():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterTestDouble(sandbox_client)

    await asyncio.gather(
        router.send_request(_env("r1", user_id="user-1")),
        router.send_request(_env("r2", user_id="user-1")),
    )

    assert len(sandbox_client.created) == 1
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_cleans_idle_runtime_when_capacity_is_full():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterTestDouble(
        sandbox_client,
        max_sandboxes=1,
        queue_timeout_seconds=1.0,
    )

    await router.send_request(_env("r1", user_id="user-1"))
    resp = await router.send_request(_env("r2", user_id="user-2"))

    assert resp.payload == {"sandbox_id": "sb-2"}
    assert sandbox_client.deleted == ["sb-1"]
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_cleans_runtime_when_it_becomes_idle_with_waiters():
    sandbox_client = FakeSandboxClient()
    release_event = asyncio.Event()
    router = SandboxRouterTestDouble(
        sandbox_client,
        release_event=release_event,
        max_sandboxes=1,
        queue_timeout_seconds=1.0,
    )

    first = asyncio.create_task(router.send_request(_env("r1", user_id="user-1")))
    await asyncio.sleep(0.05)
    pending = asyncio.create_task(router.send_request(_env("r2", user_id="user-2")))
    await asyncio.sleep(0.05)

    assert not pending.done()
    release_event.set()

    resp1 = await first
    resp2 = await pending

    assert resp1.payload == {"sandbox_id": "sb-1"}
    assert resp2.payload == {"sandbox_id": "sb-2"}
    assert sandbox_client.deleted == ["sb-1"]
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_idle_timeout_releases_runtime():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterTestDouble(
        sandbox_client,
        idle_timeout_seconds=0.01,
        idle_check_interval_seconds=0.01,
    )
    router._idle_check_interval_seconds = 0.01

    await router.send_request(_env("r1", user_id="user-1"))

    for _ in range(20):
        if sandbox_client.deleted == ["sb-1"]:
            break
        await asyncio.sleep(0.01)

    assert sandbox_client.deleted == ["sb-1"]
    await router.disconnect()


@pytest.mark.asyncio
async def test_router_missing_connector_returns_error_without_default_fallback():
    sandbox_client = FakeSandboxClient()
    router = SandboxRouterAgentClient()
    router._sandbox_client = sandbox_client

    resp = await router.send_request(_env("r1", user_id="user-1"))

    assert resp.ok is False
    assert resp.payload == {"error": "sandbox agent connection is not configured"}
    assert sandbox_client.created == ["sb-1"]
    assert sandbox_client.deleted == []
    await router.disconnect()
