from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk
from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import AgentManager
from jiuwenswarm.extensions.agentos.agentos_router.config import (
    agentos_router_selected,
    load_router_config,
)
from jiuwenswarm.extensions.agentos.agentos_router.extension import AgentOSRouter
from jiuwenswarm.extensions.agentos.agentos_router.models import (
    AgentInfo,
    AgentStatus,
    ImageInfo,
)
from jiuwenswarm.extensions.agentos.agentos_router.router_client import AgentOSRouterClient
from jiuwenswarm.extensions.yuanrong_frontend_client import SandboxInfo


class FakeYuanRongClient:
    def __init__(self) -> None:
        self.server_ready = True
        self.send_calls = 0
        self.create_calls = 0
        self.delete_calls: list[str] = []
        self.config: dict[str, Any] = {}
        self.push_handler = None

    async def connect(self, uri: str) -> None:
        del uri
        return None

    async def disconnect(self) -> None:
        return None

    async def create_sandbox(
        self,
        *,
        user_id: str,
        agent_type: str,
        agent_id: str | None = None,
        image_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxInfo:
        self.create_calls += 1
        return SandboxInfo(
            sandbox_id=f"sbx-{self.create_calls}",
            user_id=user_id,
            agent_type=agent_type,
            metadata={
                **dict(metadata or {}),
                "agent_id": agent_id,
                "image_name": image_name,
            },
        )

    async def delete_sandbox(
        self,
        sandbox_id: str,
        *,
        user_id: str | None = None,
        agent_type: str | None = None,
    ) -> None:
        del user_id, agent_type
        self.delete_calls.append(sandbox_id)

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        self.send_calls += 1
        return AgentResponse(
            request_id=str(envelope.request_id or ""),
            channel_id=str(envelope.channel or ""),
        )

    async def send_request_stream(
        self, envelope: E2AEnvelope
    ) -> AsyncIterator[AgentResponseChunk]:
        self.send_calls += 1
        yield AgentResponseChunk(
            request_id=str(envelope.request_id or ""),
            channel_id=str(envelope.channel or ""),
            is_complete=True,
        )

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        del env
        self.config = config

    def set_server_push_handler(self, handler) -> None:
        self.push_handler = handler


class FakeRegistryClient:
    def __init__(self) -> None:
        self.registered: list[AgentInfo] = []
        self.image_lookups = 0

    async def get_image_info(self, image_name: str) -> ImageInfo:
        self.image_lookups += 1
        return ImageInfo(image_name=image_name)

    async def register_agent(self, agent_info: AgentInfo) -> None:
        self.registered.append(agent_info)

    async def close(self) -> None:
        return None


def _envelope(*, agent_type: str | None = None) -> E2AEnvelope:
    params = {"query": "hello"}
    if agent_type is not None:
        params["agent_type"] = agent_type
    return E2AEnvelope(
        request_id="req-1",
        channel="web",
        user_id="u1",
        session_id="sess-1",
        params=params,
    )


@pytest.mark.asyncio
async def test_swarm_request_creates_mapping_then_forwards() -> None:
    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, registry, agent_manager)
    envelope = _envelope()

    response = await client.send_request(envelope)
    await client.shutdown()

    assert response.ok
    assert registry.image_lookups == 1
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 1
    agents = await agent_manager.list_user_agents("u1")
    assert len(agents) == 1
    assert agents[0].info.status is AgentStatus.READY
    assert agents[0].info.sandbox_id == "sbx-1"
    assert envelope.channel_context["agent_id"] == agents[0].info.agent_id
    assert envelope.channel_context["agent_type"] == "jiuwenswarm"
    assert envelope.channel_context["sandbox_id"] == "sbx-1"
    assert [item.agent_id for item in registry.registered] == [agents[0].info.agent_id]


@pytest.mark.asyncio
async def test_existing_swarm_agent_is_reused() -> None:
    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    client = AgentOSRouterClient(yuanrong, registry, AgentManager())

    await client.send_request(_envelope())
    await client.send_request(_envelope())
    await client.shutdown()

    assert registry.image_lookups == 1
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 2


@pytest.mark.asyncio
async def test_third_party_type_creates_via_yuanrong() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    response = await client.send_request(_envelope(agent_type="opencode"))

    assert response.ok
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 1
    agents = await agent_manager.list_user_agents("u1")
    assert agents[0].info.agent_type == "opencode"
    assert agents[0].info.status is AgentStatus.READY
    assert agents[0].info.sandbox_id == "sbx-1"


@pytest.mark.asyncio
async def test_delete_agent_releases_yuanrong_sandbox() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    await client.send_request(_envelope())
    agents = await agent_manager.list_user_agents("u1")
    assert agents[0].info.sandbox_id == "sbx-1"

    await client.delete_agent("u1", "jiuwenswarm")

    assert yuanrong.delete_calls == ["sbx-1"]
    assert await agent_manager.list_user_agents("u1") == []


def test_agentos_selected_by_agent_client_type() -> None:
    assert agentos_router_selected(
        {
            "gateway": {
                "agent_client": {"type": "agentos_router"},
            }
        }
    )
    assert not agentos_router_selected(
        {
            "gateway": {
                "agent_client": {"type": "websocket"},
            }
        }
    )
    assert not agentos_router_selected(
        {
            "gateway": {
                "agent_client": {"type": "yuanrong"},
            }
        }
    )


def test_load_router_config_agent_key_fields() -> None:
    config = {
        "gateway": {
            "agent_client": {
                "type": "agentos_router",
                "frontend_endpoint": "http://yuanrong.test",
                "function_version_urn": "urn:test",
            },
            "agentos": {
                "agent_key_fields": ["user_id", "agent_type", "session_id"],
            },
        }
    }
    loaded = load_router_config(config)
    assert loaded.agent_key_fields == ("user_id", "agent_type", "session_id")

    default_loaded = load_router_config(
        {
            "gateway": {
                "agent_client": {
                    "frontend_endpoint": "http://yuanrong.test",
                    "function_version_urn": "urn:test",
                }
            }
        }
    )
    assert default_loaded.agent_key_fields == ("user_id", "agent_type")


@pytest.mark.asyncio
async def test_agentos_extension_is_selected_independently(
    monkeypatch,
) -> None:
    from jiuwenswarm.extensions.agent_client import extension as plain_extension
    from jiuwenswarm.extensions.agentos import extension as agentos_extension
    from jiuwenswarm.extensions.agentos.agentos_router import (
        extension as agentos_router_impl,
    )

    config = {
        "gateway": {
            "agent_client": {
                "type": "agentos_router",
                "frontend_endpoint": "http://yuanrong.test",
                "function_version_urn": "urn:test",
            },
        }
    }
    monkeypatch.setattr(agentos_router_impl, "get_config", lambda: config)
    monkeypatch.setattr(plain_extension, "get_config", lambda: config)

    class Registry:
        registered = None

        def register_agent_server_client(self, extension) -> None:
            self.registered = extension

    registry = Registry()
    assert await plain_extension.register_extensions(registry) == []
    registered = await agentos_extension.register_extensions(registry)

    assert len(registered) == 1
    assert isinstance(registered[0], AgentOSRouter)
    assert registry.registered is registered[0]
    await registered[0].shutdown()
