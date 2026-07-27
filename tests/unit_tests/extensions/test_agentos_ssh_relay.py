# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the AgentOS Router southbound SSH relay into YuanRong."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.extensions.agentos.agentos_router.agent_manager import AgentManager
from jiuwenswarm.extensions.agentos.agentos_router.config import (
    SshChannelEndpoint,
    load_router_config,
)
from jiuwenswarm.extensions.agentos.agentos_router.router_client import AgentOSRouterClient
from jiuwenswarm.extensions.agentos.agentos_router.ssh_relay import (
    DEFAULT_CLIENT_KEYS_DIR,
    DEFAULT_SSH_USER_TEMPLATE,
    YuanrongSshRelay,
    YuanrongSshSettings,
    list_client_key_paths,
    load_yuanrong_ssh_settings,
    resolve_client_keys_dir,
)
from jiuwenswarm.gateway.channel_manager.protocol.ssh.ssh_connect import SshRelaySession

from tests.unit_tests.extensions.test_agentos_router import (
    FakeRegistryClient,
    FakeYuanRongClient,
)


def _relay_session(session_id: str = "ssh_alice_1234") -> SshRelaySession:
    return SshRelaySession(
        session_id=session_id,
        process=None,
    )


def _ssh_envelope(
    session: SshRelaySession | None = None,
    *,
    agent_type: str | None = "jiuwenswarm",
    session_id: str | None = None,
) -> E2AEnvelope:
    params: dict[str, Any] = {}
    if session is not None:
        params["relay_session"] = session
    if agent_type is not None:
        params["agent_type"] = agent_type
    return E2AEnvelope(
        request_id="req-ssh-1",
        channel="ssh",
        user_id="alice",
        session_id=session_id or (session.session_id if session else "ssh_missing"),
        method=ReqMethod.SSH_RELAY.value,
        params=params,
    )


class StubSshRelay:
    """Records relay invocations and resolves the session like the real relay."""

    def __init__(
        self,
        *,
        backend_host: str = "frontend.yuanrong.test",
        backend_port: int = 2222,
    ) -> None:
        self.ran: list[tuple[str, str, str]] = []
        self.failed: list[tuple[str, str]] = []
        self.backend_host = backend_host
        self.backend_port = backend_port

    def backend_username(self, instance_id: str) -> str:
        return DEFAULT_SSH_USER_TEMPLATE.format(instance=instance_id)

    async def run(
        self,
        session: Any,
        instance_id: str,
        *,
        user_id: str = "",
    ) -> int:
        self.ran.append((session.session_id, instance_id, user_id))
        session.exit_code = 0
        session.done.set()
        return 0

    def fail_session(self, session: Any, reason: str) -> None:
        self.failed.append((session.session_id, reason))
        session.exit_code = 1
        session.done.set()


# ---------- settings / username ----------


def test_backend_username_uses_yr_instance_template() -> None:
    relay = YuanrongSshRelay(
        YuanrongSshSettings(),
        frontend_endpoint="http://frontend.yuanrong.test:31220",
    )
    assert (
        relay.backend_username("inst-42")
        == "yr:instance:inst-42"
    )
    assert relay.backend_host == "frontend.yuanrong.test"
    assert relay.backend_port == 2222


def test_backend_username_requires_instance_id() -> None:
    relay = YuanrongSshRelay(YuanrongSshSettings())
    with pytest.raises(ValueError):
        relay.backend_username("  ")


def test_backend_host_from_frontend_endpoint() -> None:
    relay = YuanrongSshRelay(
        YuanrongSshSettings(port=2200),
        frontend_endpoint="http://frontend.yuanrong.test:31220",
    )
    assert relay.backend_host == "frontend.yuanrong.test"
    assert relay.backend_port == 2200


def test_load_yuanrong_ssh_settings_defaults() -> None:
    settings = load_yuanrong_ssh_settings(None)
    assert settings.port == 2222
    assert settings.user_template == DEFAULT_SSH_USER_TEMPLATE
    assert settings.connect_timeout_s == 30.0
    assert settings.client_keys_dir == DEFAULT_CLIENT_KEYS_DIR

    custom = load_yuanrong_ssh_settings(
        {
            "port": 2223,
            "user_template": "yr:{instance}",
            "client_keys_dir": "/data/{user_id}/keys",
        }
    )
    assert custom.port == 2223
    assert custom.user_template == "yr:{instance}"
    assert custom.client_keys_dir == "/data/{user_id}/keys"


def test_resolve_client_keys_dir_defaults_to_root_ssh() -> None:
    assert resolve_client_keys_dir(DEFAULT_CLIENT_KEYS_DIR, "alice") == Path(
        "/root/.ssh"
    )
    assert resolve_client_keys_dir(
        "/home/{user_id}/.ssh", "alice/../bob"
    ) == Path("/home/alice_.._bob/.ssh")


def test_list_client_key_paths_reads_default_names(tmp_path: Path) -> None:
    (tmp_path / "id_ed25519").write_text("key-ed", encoding="utf-8")
    (tmp_path / "id_rsa").write_text("key-rsa", encoding="utf-8")
    (tmp_path / "id_rsa.pub").write_text("pub", encoding="utf-8")
    (tmp_path / "known_hosts").write_text("h", encoding="utf-8")
    assert list_client_key_paths(tmp_path) == [
        str(tmp_path / "id_ed25519"),
        str(tmp_path / "id_rsa"),
    ]
    assert list_client_key_paths(tmp_path / "missing") == []


def test_resolve_client_keys_requires_private_key(tmp_path: Path) -> None:
    relay = YuanrongSshRelay(
        YuanrongSshSettings(client_keys_dir=str(tmp_path / "{user_id}")),
    )
    with pytest.raises(ValueError, match="no SSH private keys found"):
        relay._resolve_client_keys("alice")

    key_dir = tmp_path / "alice"
    key_dir.mkdir()
    (key_dir / "id_ed25519").write_text("k", encoding="utf-8")
    assert relay._resolve_client_keys("alice") == [str(key_dir / "id_ed25519")]


def test_import_asyncssh_raises_actionable_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    import builtins
    import jiuwenswarm.extensions.agentos.agentos_router.ssh_relay as relay_mod

    real_import = builtins.__import__

    def _block_asyncssh(name: str, *args: Any, **kwargs: Any):
        if name == "asyncssh" or name.startswith("asyncssh."):
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_asyncssh)
    with pytest.raises(RuntimeError, match=r"jiuwenswarm\[ssh\]"):
        relay_mod._import_asyncssh()


def test_load_router_config_parses_ssh_block() -> None:
    config = {
        "gateway": {
            "agent_client": {
                "type": "agentos_router",
                "frontend_endpoint": "http://yuanrong.test",
                "function_version_urn": "urn:test",
            },
            "agentos": {
                "ssh": {"port": 2222},
            },
        },
        "channels": {
            "ssh": {
                "enabled": True,
                "listen_host": "192.168.1.10",
                "listen_port": 2222,
            }
        },
    }
    loaded = load_router_config(config)
    assert loaded.ssh.port == 2222
    assert loaded.ssh.user_template == DEFAULT_SSH_USER_TEMPLATE
    assert loaded.ssh_channel == SshChannelEndpoint(ip="192.168.1.10", port=2222)
    assert loaded.ssh.client_keys_dir == DEFAULT_CLIENT_KEYS_DIR
    assert loaded.workspace_root == "/home/agentos/users"


def test_load_router_config_ssh_channel_requires_enabled() -> None:
    config = {
        "gateway": {
            "agent_client": {
                "type": "agentos_router",
                "frontend_endpoint": "http://yuanrong.test",
                "function_version_urn": "urn:test",
            },
        },
        "channels": {
            "ssh": {
                "enabled": False,
                "listen_host": "0.0.0.0",
                "listen_port": 2222,
            }
        },
    }
    loaded = load_router_config(config)
    assert loaded.ssh_channel is None


# ---------- router dispatch ----------


@pytest.mark.asyncio
async def test_ssh_relay_creates_instance_and_starts_relay() -> None:
    session = _relay_session()
    stub_relay = StubSshRelay()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(
        FakeYuanRongClient(),
        FakeRegistryClient(),
        agent_manager,
        ssh_relay=stub_relay,
    )
    try:
        response = await client.send_request(
            _ssh_envelope(session, agent_type="opencode")
        )
        assert response.ok
        assert response.payload["status"] == "relay_started"

        await asyncio.wait_for(session.done.wait(), timeout=5)
        assert stub_relay.ran == [("ssh_alice_1234", "sbx-1", "alice")]
        assert stub_relay.failed == []
        assert session.exit_code == 0

        agents = await agent_manager.list_user_agents("alice")
        assert len(agents) == 1
        assert agents[0].info.sandbox_id == "sbx-1"
        assert agents[0].info.agent_type == "opencode"
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_reuses_existing_instance() -> None:
    yuanrong = FakeYuanRongClient()
    stub_relay = StubSshRelay()
    client = AgentOSRouterClient(
        yuanrong,
        FakeRegistryClient(),
        AgentManager(),
        ssh_relay=stub_relay,
    )
    first = _relay_session("ssh_alice_a")
    second = _relay_session("ssh_alice_b")
    try:
        await client.send_request(_ssh_envelope(first, agent_type="opencode"))
        await asyncio.wait_for(first.done.wait(), timeout=5)
        await client.send_request(_ssh_envelope(second, agent_type="opencode"))
        await asyncio.wait_for(second.done.wait(), timeout=5)

        assert yuanrong.create_calls == 1
        assert [item[1] for item in stub_relay.ran] == ["sbx-1", "sbx-1"]
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_follows_user_current_agent_type() -> None:
    """SSH 未显式指定 agent_type 时，跟随 3rdagent.switch 切换后的用户当前值。"""
    yuanrong = FakeYuanRongClient()
    stub_relay = StubSshRelay()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(
        yuanrong,
        FakeRegistryClient(),
        agent_manager,
        ssh_relay=stub_relay,
        ssh_channel_endpoint=SshChannelEndpoint(ip="0.0.0.0", port=2222),
    )
    session = _relay_session("ssh_alice_switch")
    try:
        # 用户切换到 opencode
        result = await client.thirdagent_switch(user_id="alice", agent_type="opencode")
        assert result["ok"]
        assert result["payload"]["ssh_ip"] == "0.0.0.0"
        assert result["payload"]["ssh_port"] == 2222
        assert "ssh_user" not in result["payload"]
        assert client.get_current_agent_type("alice") == "opencode"

        # SSH 接入不带 agent_type -> 复用 opencode 实例（不新建）
        await client.send_request(_ssh_envelope(session, agent_type=None))
        await asyncio.wait_for(session.done.wait(), timeout=5)

        assert yuanrong.create_calls == 1  # switch 已创建，SSH 复用
        assert stub_relay.ran == [("ssh_alice_switch", "sbx-1", "alice")]
        agents = await agent_manager.list_user_agents("alice")
        assert [a.info.agent_type for a in agents] == ["opencode"]
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_defaults_to_jiuwenswarm_without_switch() -> None:
    """未切换过的用户，SSH 默认 jiuwenswarm：无 AgentOS sandbox，应失败。"""
    yuanrong = FakeYuanRongClient()
    stub_relay = StubSshRelay()
    agent_manager = AgentManager()
    client = AgentOSRouterClient(
        yuanrong,
        FakeRegistryClient(),
        agent_manager,
        ssh_relay=stub_relay,
    )
    assert client.get_current_agent_type("alice") == "jiuwenswarm"
    session = _relay_session("ssh_alice_default")
    try:
        await client.send_request(_ssh_envelope(session, agent_type=None))
        await asyncio.wait_for(session.done.wait(), timeout=5)

        assert yuanrong.create_calls == 0
        assert stub_relay.ran == []
        assert len(stub_relay.failed) == 1
        assert "no AgentOS sandbox for SSH" in stub_relay.failed[0][1]
        assert await agent_manager.list_user_agents("alice") == []
        assert session.exit_code == 1
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_missing_session_returns_error() -> None:
    client = AgentOSRouterClient(
        FakeYuanRongClient(),
        FakeRegistryClient(),
        AgentManager(),
        ssh_relay=StubSshRelay(),
    )
    try:
        response = await client.send_request(_ssh_envelope(session_id="ssh_missing"))
        assert not response.ok
        assert "ssh relay session not found" in response.payload["error"]
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_without_relay_configured_fails_session() -> None:
    session = _relay_session("ssh_norelay")
    client = AgentOSRouterClient(
        FakeYuanRongClient(),
        FakeRegistryClient(),
        AgentManager(),
    )
    try:
        response = await client.send_request(_ssh_envelope(session))
        assert not response.ok
        assert session.done.is_set()
        assert session.exit_code == 1
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_ssh_relay_agent_creation_failure_releases_session() -> None:
    class FailingYuanRong(FakeYuanRongClient):
        async def create_sandbox(self, **kwargs: Any):
            raise RuntimeError("create failed")

    session = _relay_session("ssh_fail")
    stub_relay = StubSshRelay()
    client = AgentOSRouterClient(
        FailingYuanRong(),
        FakeRegistryClient(),
        AgentManager(),
        ssh_relay=stub_relay,
    )
    try:
        response = await client.send_request(
            _ssh_envelope(session, agent_type="opencode")
        )
        assert response.ok  # relay task started; failure is reported via session
        await asyncio.wait_for(session.done.wait(), timeout=5)
        assert session.exit_code == 1
        assert stub_relay.ran == []
        assert len(stub_relay.failed) == 1
        assert "create failed" in stub_relay.failed[0][1]
    finally:
        await client.shutdown()
