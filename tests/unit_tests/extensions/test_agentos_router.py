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
        self.function_version_urn = "urn:test:function:1"
        self.agent_namespace = "default"
        self.send_calls = 0
        self.create_calls = 0
        self.create_payloads: list[dict[str, Any]] = []
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
        namespace: str,
        name: str,
        workspace: str,
        runtime_spec: dict[str, Any],
        env_vars: dict[str, str] | None = None,
        mounts: list[dict[str, Any]] | None = None,
    ) -> SandboxInfo:
        self.create_calls += 1
        payload = {
            "namespace": namespace,
            "name": name,
            "workspace": workspace,
            "runtime_spec": dict(runtime_spec),
            "env_vars": dict(env_vars or {}),
            "mounts": list(mounts or []),
        }
        self.create_payloads.append(payload)
        return SandboxInfo(
            sandbox_id=f"sbx-{self.create_calls}",
            metadata=payload,
        )

    async def delete_sandbox(self, sandbox_id: str) -> None:
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
        self.list_user_images_calls: list[str] = []

    async def get_image_info(self, image_name: str) -> ImageInfo:
        self.image_lookups += 1
        imageurl = f"harbor.local/adapted/{image_name}:latest"
        runtime_spec = {
            "runtime": "python3.11",
            "sandbox_type": "docker",
            "rootfs": {
                "imageurl": imageurl,
                "user": "agentos",
                "ports": ["tcp:22"],
            },
            "cpu": 1000,
            "memory": 2048,
        }
        return ImageInfo(
            image_name=image_name,
            image_uri=imageurl,
            metadata={
                "agent_type": image_name,
                "runtime_spec": runtime_spec,
                "env_vars": {},
                "source": "local_stub",
            },
        )

    async def list_user_images(self, user_id: str) -> list[ImageInfo]:
        self.list_user_images_calls.append(user_id)
        return [
            ImageInfo(
                image_name="claude",
                image_uri="registry://claude:latest",
                metadata={"agent_type": "claude", "user_id": user_id},
            ),
            ImageInfo(
                image_name="opencode",
                metadata={"agent_type": "opencode", "user_id": user_id},
            ),
        ]

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
async def test_swarm_request_forwards_direct_yuanrong_without_create() -> None:
    """jiuwenswarm uses URN invoke like agent_client.type=yuanrong."""
    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, registry, agent_manager)
    envelope = _envelope()

    response = await client.send_request(envelope)
    await client.shutdown()

    assert response.ok
    assert registry.image_lookups == 0
    assert yuanrong.create_calls == 0
    assert yuanrong.send_calls == 1
    assert registry.registered == []
    assert await agent_manager.list_user_agents("u1") == []
    assert envelope.channel_context["agent_type"] == "jiuwenswarm"
    assert "agent_id" not in envelope.channel_context
    assert "sandbox_id" not in envelope.channel_context


@pytest.mark.asyncio
async def test_swarm_request_repeated_stays_direct() -> None:
    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    client = AgentOSRouterClient(yuanrong, registry, AgentManager())

    await client.send_request(_envelope())
    await client.send_request(_envelope())
    await client.shutdown()

    assert registry.image_lookups == 0
    assert yuanrong.create_calls == 0
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
async def test_agent_switch_creates_without_forwarding_chat() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    response = await client.thirdagent_switch(
        user_id="u1",
        agent_type="claude",
        session_id="sess-1",
    )
    await client.shutdown()

    assert response["ok"] is True
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 0
    assert response["payload"]["agent_type"] == "claude"
    assert response["payload"]["sandbox_id"] == "sbx-1"
    agents = await agent_manager.list_user_agents("u1")
    assert len(agents) == 1
    assert agents[0].info.agent_type == "claude"


@pytest.mark.asyncio
async def test_agent_list_returns_registry_images_without_creating() -> None:
    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, registry, agent_manager)

    response = await client.thirdagent_list(
        user_id="u1",
        current_agent_type="jiuwenswarm",
    )
    await client.shutdown()

    assert response["ok"] is True
    assert yuanrong.create_calls == 0
    assert yuanrong.send_calls == 0
    assert registry.list_user_images_calls == ["u1"]
    assert response["payload"]["current_agent_type"] == "jiuwenswarm"
    assert [item["agent_type"] for item in response["payload"]["agents"]] == [
        "claude",
        "opencode",
    ]
    assert response["payload"]["agents"][0]["image_uri"] == "registry://claude:latest"
    assert await agent_manager.list_user_agents("u1") == []


@pytest.mark.asyncio
async def test_agent_switch_reuses_existing_agent() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    first = await client.thirdagent_switch(
        user_id="u1",
        agent_type="claude",
        session_id="sess-1",
    )
    second = await client.thirdagent_switch(
        user_id="u1",
        agent_type="claude",
        session_id="sess-1",
    )
    await client.shutdown()

    assert first["ok"] and second["ok"]
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 0
    assert first["payload"]["agent_id"] == second["payload"]["agent_id"]


@pytest.mark.asyncio
async def test_chat_after_switch_reuses_agent() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    switch_resp = await client.thirdagent_switch(
        user_id="u1",
        agent_type="claude",
        session_id="sess-1",
    )
    chat_envelope = _envelope(agent_type="claude")
    chat_resp = await client.send_request(chat_envelope)
    await client.shutdown()

    assert switch_resp["ok"] and chat_resp.ok
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 1
    assert chat_envelope.channel_context["agent_id"] == switch_resp["payload"]["agent_id"]
    assert chat_envelope.channel_context["agent_type"] == "claude"


@pytest.mark.asyncio
async def test_switch_to_jiuwenswarm_is_direct_without_create() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    response = await client.thirdagent_switch(
        user_id="u1",
        agent_type="jiuwenswarm",
        session_id="sess-1",
    )
    await client.shutdown()

    assert response["ok"] is True
    assert yuanrong.create_calls == 0
    assert yuanrong.send_calls == 0
    assert response["payload"]["agent_type"] == "jiuwenswarm"
    assert response["payload"]["sandbox_id"] == ""
    assert client.get_current_agent_type("u1") == "jiuwenswarm"
    assert await agent_manager.list_user_agents("u1") == []


@pytest.mark.asyncio
async def test_delete_agent_releases_yuanrong_sandbox() -> None:
    yuanrong = FakeYuanRongClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, FakeRegistryClient(), agent_manager)

    await client.send_request(_envelope(agent_type="opencode"))
    agents = await agent_manager.list_user_agents("u1")
    assert agents[0].info.sandbox_id == "sbx-1"

    await client.delete_agent("u1", "opencode")

    assert yuanrong.delete_calls == ["sbx-1"]
    assert await agent_manager.list_user_agents("u1") == []


@pytest.mark.asyncio
async def test_unsupported_third_agent_returns_unsupported() -> None:
    from jiuwenswarm.gateway.routing.third_agent import get_unsupported_third_agent

    third = get_unsupported_third_agent()
    listed = await third.thirdagent_list(user_id="u1", current_agent_type="jiuwenswarm")
    switched = await third.thirdagent_switch(
        user_id="u1", agent_type="claude", session_id="s1"
    )

    assert listed["ok"] is False
    assert listed["code"] == "UNSUPPORTED"
    assert switched["ok"] is False
    assert switched["code"] == "UNSUPPORTED"


@pytest.mark.asyncio
async def test_agentos_third_agent_list_and_switch() -> None:
    from jiuwenswarm.extensions.agentos.agentos_router.third_agent import (
        AgentOSThirdAgent,
    )

    yuanrong = FakeYuanRongClient()
    registry = FakeRegistryClient()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(yuanrong, registry, agent_manager)
    third = AgentOSThirdAgent(client)

    listed = await third.thirdagent_list(user_id="u1", current_agent_type="jiuwenswarm")
    switched = await third.thirdagent_switch(
        user_id="u1", agent_type="claude", session_id="sess-1"
    )
    await client.shutdown()

    assert listed["ok"] is True
    assert [item["agent_type"] for item in listed["payload"]["agents"]] == [
        "claude",
        "opencode",
    ]
    assert switched["ok"] is True
    assert switched["payload"]["agent_type"] == "claude"
    assert yuanrong.create_calls == 1
    assert yuanrong.send_calls == 0


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
                "registry": {
                    "endpoint": "http://127.0.0.1:8000",
                    "node": "192.168.0.12",
                },
            },
        }
    }
    loaded = load_router_config(config)
    assert loaded.agent_key_fields == ("user_id", "agent_type", "session_id")
    assert loaded.registry.endpoint == "http://127.0.0.1:8000"
    assert loaded.registry.node == "192.168.0.12"

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
        third_agent = None

        def register_agent_server_client(self, extension) -> None:
            self.registered = extension

        def register_third_agent(self, extension) -> None:
            self.third_agent = extension

    registry = Registry()
    assert await plain_extension.register_extensions(registry) == []
    registered = await agentos_extension.register_extensions(registry)

    assert len(registered) == 1
    assert isinstance(registered[0], AgentOSRouter)
    assert registry.registered is registered[0]
    assert registry.third_agent is registered[0]
    assert registry.third_agent.get_third_agent() is registered[0].get_third_agent()
    await registered[0].shutdown()
