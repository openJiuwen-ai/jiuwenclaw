"""Stage-2 coverage for A2A outbound discovery and registration management."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpcore
import httpx
import pytest

from jiuwenswarm.gateway.a2a_manager.outbound import (
    A2ACompatibleInterface,
    A2ADiscoveredAgent,
    A2AOutboundCredentialStore,
    A2AOutboundDiscoveryService,
    A2AOutboundError,
    A2AOutboundErrorCode,
    A2AOutboundRegistry,
    A2AOutboundRepository,
    DiscoveredCard,
)
from jiuwenswarm.gateway.storage.backends.memory_persistent import (
    InMemoryPersistentBackend,
)
from jiuwenswarm.gateway.a2a_manager.outbound import discovery as discovery_module


class _SecretProbe:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str) -> str:
        return self.values.get(key, "")

    def set(self, key: str, value: str, *, algorithm=None) -> None:
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _FailingUpdateRepository(A2AOutboundRepository):
    fail_next_update = False

    async def update_agent(self, agent_id, updater):
        if self.fail_next_update:
            self.fail_next_update = False
            raise RuntimeError("persistence unavailable")
        return await super().update_agent(agent_id, updater)


def _card(
    endpoint: str = "https://agent.example.com/a2a", *, name: str = "Research Agent"
) -> DiscoveredCard:
    payload = {
        "name": name,
        "description": "Researches a topic",
        "version": "1.0.0",
        "supportedInterfaces": [
            {
                "url": endpoint,
                "protocolBinding": "JSONRPC",
                "protocolVersion": "1.0.0",
            }
        ],
        "skills": [
            {"id": "research", "name": "Research", "description": "", "tags": []}
        ],
    }
    return DiscoveredCard(
        source_url="https://agent.example.com",
        card_path="/.well-known/agent-card.json",
        card_url="https://agent.example.com/.well-known/agent-card.json",
        card_fingerprint=f"sha256:{name}:{endpoint}",
        agent=A2ADiscoveredAgent(
            name=name,
            description="Researches a topic",
            version="1.0.0",
            skills=(
                {"id": "research", "name": "Research", "description": "", "tags": []},
            ),
            compatible_interfaces=(
                A2ACompatibleInterface("JSONRPC", "1.0.0", endpoint),
            ),
        ),
        agent_card=payload,
        security_requirements=(),
        warnings=(),
    )


class _DiscoverySequence:
    def __init__(self, *cards: DiscoveredCard) -> None:
        self.cards = list(cards)

    async def discover(self, url: str, card_path: str | None = None) -> DiscoveredCard:
        return self.cards.pop(0)


def _registry(*cards: DiscoveredCard, now_factory=None):
    secret_probe = _SecretProbe()
    credentials = A2AOutboundCredentialStore(secret_probe)
    repository = A2AOutboundRepository(
        InMemoryPersistentBackend(), credential_store=credentials
    )
    return (
        A2AOutboundRegistry(
            repository,
            discovery_service=_DiscoverySequence(*cards),
            credential_store=credentials,
            now_factory=now_factory,
        ),
        repository,
        secret_probe,
    )


@pytest.mark.asyncio
async def test_discovery_preview_does_not_register_agent() -> None:
    registry, repository, _ = _registry(_card())

    preview = await registry.discover("https://agent.example.com")

    assert preview["discovery_id"].startswith("disc_")
    assert await repository.list_agents() == []


@pytest.mark.asyncio
async def test_registration_is_explicit_single_use_and_secret_is_not_returned() -> None:
    registry, repository, secrets = _registry(_card())
    preview = await registry.discover("https://agent.example.com")

    created = await registry.register(
        {
            "discovery_id": preview["discovery_id"],
            "display_name": "External Research",
            "credential": "must-not-return",
        }
    )

    assert created["display_name"] == "External Research"
    assert created["has_credential"] is True
    assert "credential" not in created and "credential_ref" not in created
    persisted = await repository.get_agent(created["agent_id"])
    assert persisted is not None
    assert secrets.values[persisted.credential_ref] == "must-not-return"
    with pytest.raises(A2AOutboundError) as error:
        await registry.register({"discovery_id": preview["discovery_id"]})
    assert error.value.code is A2AOutboundErrorCode.DISCOVERY_NOT_FOUND


@pytest.mark.asyncio
async def test_duplicate_source_cannot_be_registered_twice() -> None:
    registry, _, _ = _registry(_card(), _card())
    first = await registry.discover("https://agent.example.com")
    await registry.register({"discovery_id": first["discovery_id"]})
    second = await registry.discover("https://agent.example.com")

    with pytest.raises(A2AOutboundError) as error:
        await registry.register({"discovery_id": second["discovery_id"]})
    assert error.value.code is A2AOutboundErrorCode.AGENT_ALREADY_REGISTERED


@pytest.mark.asyncio
async def test_concurrent_discoveries_cannot_register_duplicate_agent() -> None:
    registry, repository, _ = _registry(_card(), _card())
    first, second = await asyncio.gather(
        registry.discover("https://agent.example.com"),
        registry.discover("https://agent.example.com"),
    )

    results = await asyncio.gather(
        registry.register({"discovery_id": first["discovery_id"]}),
        registry.register({"discovery_id": second["discovery_id"]}),
        return_exceptions=True,
    )

    assert len(await repository.list_agents()) == 1
    errors = [item for item in results if isinstance(item, A2AOutboundError)]
    assert len(errors) == 1
    assert errors[0].code is A2AOutboundErrorCode.AGENT_ALREADY_REGISTERED


@pytest.mark.asyncio
async def test_critical_refresh_requires_confirmation_before_switching_endpoint() -> (
    None
):
    registry, repository, _ = _registry(_card(), _card("https://new.example.com/a2a"))
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register({"discovery_id": preview["discovery_id"]})

    refreshed = await registry.refresh_agent(created["agent_id"])
    assert refreshed["availability"] == "review_required"
    assert refreshed["selected_interface"]["url"] == "https://agent.example.com/a2a"
    assert (
        refreshed["pending_revision"]["selected_interface"]["url"]
        == "https://new.example.com/a2a"
    )

    confirmed = await registry.confirm_revision(created["agent_id"], accept=True)
    assert confirmed["availability"] == "available"
    assert confirmed["card_revision"] == 2
    assert confirmed["selected_interface"]["url"] == "https://new.example.com/a2a"
    assert (await repository.get_agent(created["agent_id"])).pending_revision is None


@pytest.mark.asyncio
async def test_refresh_does_not_clear_an_unconfirmed_pending_revision() -> None:
    registry, _, _ = _registry(
        _card(),
        _card("https://new.example.com/a2a"),
        _card(name="Research Agent v2"),
    )
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register({"discovery_id": preview["discovery_id"]})
    await registry.refresh_agent(created["agent_id"])

    refreshed_again = await registry.refresh_agent(created["agent_id"])

    assert refreshed_again["availability"] == "review_required"
    assert refreshed_again["selected_interface"]["url"] == (
        "https://agent.example.com/a2a"
    )
    assert refreshed_again["pending_revision"] is not None
    assert refreshed_again["pending_revision"]["agent_card"]["name"] == (
        "Research Agent v2"
    )


@pytest.mark.asyncio
async def test_reject_revision_keeps_effective_card_and_clears_pending() -> None:
    registry, _, _ = _registry(_card(), _card("https://new.example.com/a2a"))
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register({"discovery_id": preview["discovery_id"]})
    await registry.refresh_agent(created["agent_id"])

    rejected = await registry.confirm_revision(created["agent_id"], accept=False)

    assert rejected["availability"] == "available"
    assert rejected["card_revision"] == 1
    assert rejected["selected_interface"]["url"] == "https://agent.example.com/a2a"
    assert rejected["pending_revision"] is None


@pytest.mark.asyncio
async def test_noncritical_refresh_applies_new_card_revision_immediately() -> None:
    registry, _, _ = _registry(_card(), _card(name="Research Agent v2"))
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register({"discovery_id": preview["discovery_id"]})

    refreshed = await registry.refresh_agent(created["agent_id"])

    assert refreshed["availability"] == "available"
    assert refreshed["card_revision"] == 2
    assert refreshed["agent_card"]["name"] == "Research Agent v2"


@pytest.mark.asyncio
async def test_discovery_expiry_uses_injected_clock() -> None:
    now = [datetime(2026, 8, 26, tzinfo=timezone.utc)]
    registry, _, _ = _registry(_card(), now_factory=lambda: now[0])
    preview = await registry.discover("https://agent.example.com")
    now[0] += timedelta(minutes=11)

    with pytest.raises(A2AOutboundError) as error:
        await registry.register({"discovery_id": preview["discovery_id"]})

    assert error.value.code is A2AOutboundErrorCode.DISCOVERY_EXPIRED


@pytest.mark.asyncio
async def test_credential_update_and_clear_are_persisted_together() -> None:
    registry, repository, secrets = _registry(_card())
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register({"discovery_id": preview["discovery_id"]})

    updated = await registry.update_agent(
        created["agent_id"], {"credential": "new-secret"}
    )
    persisted = await repository.get_agent(created["agent_id"])
    assert persisted is not None
    assert updated["has_credential"] is True
    assert secrets.values[persisted.credential_ref] == "new-secret"

    cleared = await registry.update_agent(
        created["agent_id"], {"clear_credential": True}
    )
    assert cleared["has_credential"] is False
    assert secrets.values == {}


@pytest.mark.asyncio
async def test_credential_change_rolls_back_when_agent_update_fails() -> None:
    secrets = _SecretProbe()
    credentials = A2AOutboundCredentialStore(secrets)
    repository = _FailingUpdateRepository(
        InMemoryPersistentBackend(), credential_store=credentials
    )
    registry = A2AOutboundRegistry(
        repository,
        discovery_service=_DiscoverySequence(_card()),
        credential_store=credentials,
    )
    preview = await registry.discover("https://agent.example.com")
    created = await registry.register(
        {"discovery_id": preview["discovery_id"], "credential": "old-secret"}
    )
    repository.fail_next_update = True

    with pytest.raises(RuntimeError, match="persistence unavailable"):
        await registry.update_agent(
            created["agent_id"], {"credential": "replacement-secret"}
        )

    persisted = await repository.get_agent(created["agent_id"])
    assert persisted is not None
    assert secrets.values[persisted.credential_ref] == "old-secret"


@pytest.mark.asyncio
async def test_discovery_blocks_private_and_plain_http_targets() -> None:
    async def private_resolver(host: str, port: int) -> list[str]:
        return ["10.0.0.8"]

    service = A2AOutboundDiscoveryService(address_resolver=private_resolver)
    with pytest.raises(A2AOutboundError) as private_error:
        await service.discover("https://private.example.com")
    assert private_error.value.code is A2AOutboundErrorCode.DISCOVERY_BLOCKED

    async def public_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    plain_service = A2AOutboundDiscoveryService(address_resolver=public_resolver)
    with pytest.raises(A2AOutboundError) as plain_error:
        await plain_service.discover("http://example.com")
    assert plain_error.value.code is A2AOutboundErrorCode.DISCOVERY_BLOCKED


@pytest.mark.asyncio
async def test_network_validation_accepts_case_insensitive_https_scheme() -> None:
    async def public_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    service = A2AOutboundDiscoveryService(address_resolver=public_resolver)

    target = await service._validate_network_target("HTTPS://agent.example.com/a2a")

    assert target.host == "agent.example.com"
    assert target.port == 443


@pytest.mark.asyncio
async def test_discovery_revalidates_and_rejects_cross_host_redirect() -> None:
    async def public_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    async def redirect(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://other.example/card.json"}
        )

    service = A2AOutboundDiscoveryService(
        address_resolver=public_resolver,
        transport_factory=lambda _: httpx.MockTransport(redirect),
    )
    with pytest.raises(A2AOutboundError) as error:
        await service.discover("https://agent.example.com")
    assert error.value.code is A2AOutboundErrorCode.DISCOVERY_BLOCKED


@pytest.mark.asyncio
async def test_validated_ip_is_pinned_for_tcp_connection() -> None:
    calls = []
    sentinel = object()

    class _NetworkProbe:
        async def connect_tcp(self, host, port, **kwargs):
            calls.append((host, port))
            return sentinel

        async def sleep(self, seconds):
            return None

    backend = discovery_module._PinnedNetworkBackend(
        {"agent.example.com": "93.184.216.34"}, backend=_NetworkProbe()
    )

    result = await backend.connect_tcp("agent.example.com", 443)

    assert result is sentinel
    assert calls == [("93.184.216.34", 443)]
    with pytest.raises(httpcore.ConnectError):
        await backend.connect_tcp("unvalidated.example.com", 443)


class _ChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.consumed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            self.consumed += 1
            yield chunk


@pytest.mark.asyncio
async def test_card_size_limit_stops_streaming_before_full_response() -> None:
    async def public_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    stream = _ChunkStream([b"x" * 600_000, b"y" * 600_000, b"z" * 600_000])

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    service = A2AOutboundDiscoveryService(
        address_resolver=public_resolver,
        transport_factory=lambda _: httpx.MockTransport(respond),
    )
    with pytest.raises(A2AOutboundError) as error:
        await service.discover("https://agent.example.com")

    assert error.value.code is A2AOutboundErrorCode.CARD_INVALID
    assert stream.consumed == 2


@pytest.mark.asyncio
async def test_valid_sdk_card_is_normalized_and_compatible(monkeypatch) -> None:
    async def public_resolver(host: str, port: int) -> list[str]:
        return ["93.184.216.34"]

    card = {
        "name": "Research Agent",
        "description": "Researches",
        "version": "1.0.0",
        "protocolVersion": "1.0.0",
        "url": "https://agent.example.com/a2a",
        "preferredTransport": "JSONRPC",
        "skills": [],
        "capabilities": {},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
    }

    async def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=card)

    pinned = []
    client_closed_states = []
    real_factory = discovery_module.ClientFactory

    class _ClientFactoryProbe:
        def __init__(self, config):
            client_closed_states.append(config.httpx_client.is_closed)
            self._delegate = real_factory(config)

        def create(self, parsed):
            return self._delegate.create(parsed)

    monkeypatch.setattr(discovery_module, "ClientFactory", _ClientFactoryProbe)

    def transport_factory(addresses):
        pinned.append(dict(addresses))
        return httpx.MockTransport(respond)

    service = A2AOutboundDiscoveryService(
        address_resolver=public_resolver,
        transport_factory=transport_factory,
    )
    result = await service.discover("https://agent.example.com")

    assert result.agent.name == "Research Agent"
    assert result.agent.compatible_interfaces[0].protocol_binding == "JSONRPC"
    assert result.card_fingerprint.startswith("sha256:")
    assert pinned == [{"agent.example.com": "93.184.216.34"}]
    assert client_closed_states == [False]
