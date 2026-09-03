from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from jiuwenswarm.agents.harness.team import kv_cache_hooks as team_kv_cache_hooks
from jiuwenswarm.agents.harness.team.team_manager import TeamManager
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server import agent_ws_server as agent_ws_server_module
from jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle import (
    KVCacheLifecycleResult,
)


class _WireWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class _AgentServer(agent_ws_server_module.AgentWebSocketServer):
    def __init__(self) -> None:
        super().__init__()
        self.team_session_ids: list[str] = []
        self._agent_manager = SimpleNamespace(
            get_agent_nowait=lambda *args, **kwargs: None,
            release_subagent_runtime_for_session=AsyncMock(return_value=False),
            cleanup_session_runtime=AsyncMock(return_value=False),
        )

    async def _ensure_persistent_checkpointer_response(self, _request):
        return None

    async def _find_team_session_ids(self, _team_name: str) -> list[str]:
        return list(self.team_session_ids)


def _wire_response(response, *, response_id):
    return {"response_id": response_id, "payload": response.payload, "ok": response.ok}


@pytest.mark.asyncio
@pytest.mark.parametrize("hook_raises", [False, True])
async def test_plan_agentserver_delete_evicts_self_parent_and_preserves_release(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    hook_raises: bool,
) -> None:
    sessions_root = tmp_path / "sessions"
    (sessions_root / "plan-root").mkdir(parents=True)
    server = _AgentServer()
    ws = _WireWebSocket()
    evict_calls: list[dict] = []
    release_calls: list[str] = []

    async def fake_evict(**kwargs):
        evict_calls.append(kwargs)
        if hook_raises:
            raise RuntimeError("hook broken")
        return KVCacheLifecycleResult(status="ok")

    async def fake_release(session_id: str) -> None:
        release_calls.append(session_id)

    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_sessions_dir",
        lambda: sessions_root,
    )
    monkeypatch.setattr(agent_ws_server_module, "encode_agent_response_for_wire", _wire_response)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.remove_session_metadata_cache",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda _sid: {"mode": "agent.plan", "channel_id": "web"},
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.evict_session_kv_cache",
        fake_evict,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle."
        "is_kv_cache_affinity_enabled",
        lambda: True,
    )
    monkeypatch.setattr("openjiuwen.core.runner.Runner.release", fake_release)

    request = AgentRequest(
        request_id="delete-plan",
        channel_id="web",
        req_method=ReqMethod.SESSION_DELETE,
        params={"session_id": "plan-root"},
    )
    await server._handle_session_delete(ws, request, asyncio.Lock())

    assert len(evict_calls) == 1
    assert evict_calls[0]["session_id"] == "plan-root"
    assert evict_calls[0]["parent_session_id"] == "plan-root"
    assert release_calls == ["plan-root"]
    assert not (sessions_root / "plan-root").exists()
    assert ws.sent[-1]["ok"] is True


@pytest.mark.asyncio
async def test_team_session_delete_delegates_terminal_kvc_to_agent_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    (sessions_root / "team-root").mkdir(parents=True)
    manager = TeamManager()
    server = _AgentServer()
    ws = _WireWebSocket()
    calls: list[dict] = []
    deleted_teams: list[dict] = []

    async def fake_evict(**kwargs):
        calls.append(kwargs)
        return KVCacheLifecycleResult(status="ok")

    async def fake_delete_agent_team(**kwargs):
        deleted_teams.append(kwargs)
        return True

    monkeypatch.setattr(manager, "_resolve_delete_session_team_name", lambda _sid: "demo-team")
    monkeypatch.setattr(manager, "stop_session_runtime", AsyncMock(return_value=True))
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_sessions_dir",
        lambda: sessions_root,
    )
    monkeypatch.setattr(agent_ws_server_module, "encode_agent_response_for_wire", _wire_response)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.remove_session_metadata_cache",
        lambda _sid: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda _sid: {"mode": "team", "channel_id": "web"},
    )
    monkeypatch.setattr("jiuwenswarm.agents.harness.team.get_team_manager", lambda _cid: manager)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.evict_session_kv_cache",
        fake_evict,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.is_kv_cache_affinity_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.Runner.delete_agent_team",
        fake_delete_agent_team,
    )

    request = AgentRequest(
        request_id="delete-team-session",
        channel_id="web",
        req_method=ReqMethod.SESSION_DELETE,
        params={"session_id": "team-root"},
    )
    await server._handle_session_delete(ws, request, asyncio.Lock())

    assert calls == []
    assert deleted_teams == [
        {"team_name": "demo-team", "session_ids": ["team-root"], "force": True}
    ]
    assert not (sessions_root / "team-root").exists()
    assert ws.sent[-1]["ok"] is True


@pytest.mark.asyncio
async def test_team_delete_delegates_terminal_kvc_to_agent_core(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    for session_id in ("team-root-1", "team-root-2"):
        (sessions_root / session_id).mkdir(parents=True)
    server = _AgentServer()
    server.team_session_ids = ["team-root-1", "team-root-2"]
    ws = _WireWebSocket()
    calls: list[dict] = []
    stop_calls: list[str] = []

    async def fake_evict(**kwargs):
        calls.append(kwargs)
        return KVCacheLifecycleResult(status="ok")

    async def fake_stop(session_id: str, reason: str = "", **kwargs):
        stop_calls.append(session_id)
        assert kwargs == {"stop_runner": False}
        return True

    monkeypatch.setattr(agent_ws_server_module, "get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(agent_ws_server_module, "encode_agent_response_for_wire", _wire_response)
    monkeypatch.setattr(agent_ws_server_module, "remove_session_metadata_cache", lambda _sid: None)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.evict_session_kv_cache",
        fake_evict,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.is_kv_cache_affinity_enabled",
        lambda: True,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.stop_team_session_runtime_across_managers",
        fake_stop,
    )
    monkeypatch.setattr(
        "openjiuwen.core.runner.Runner.delete_agent_team",
        AsyncMock(return_value=True),
    )

    request = AgentRequest(
        request_id="delete-team",
        channel_id="web",
        req_method=ReqMethod.TEAM_DELETE,
        params={"mode": "team", "team_name": "demo-team"},
    )
    await server._handle_team_delete(ws, request, asyncio.Lock())

    assert calls == []
    assert stop_calls == ["team-root-1", "team-root-2"]
    assert ws.sent[-1]["ok"] is True


@pytest.mark.asyncio
async def test_team_delete_keeps_original_stop_order_when_affinity_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    (sessions_root / "team-root").mkdir(parents=True)
    server = _AgentServer()
    server.team_session_ids = ["team-root"]
    ws = _WireWebSocket()
    stop_kwargs: list[dict] = []

    async def fake_stop(_session_id: str, reason: str = "", **kwargs):
        stop_kwargs.append(kwargs)
        return True

    monkeypatch.setattr(agent_ws_server_module, "get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(agent_ws_server_module, "encode_agent_response_for_wire", _wire_response)
    monkeypatch.setattr(agent_ws_server_module, "remove_session_metadata_cache", lambda _sid: None)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.is_kv_cache_affinity_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.stop_team_session_runtime_across_managers",
        fake_stop,
    )
    monkeypatch.setattr(
        "openjiuwen.core.runner.Runner.delete_agent_team",
        AsyncMock(return_value=True),
    )

    request = AgentRequest(
        request_id="delete-team",
        channel_id="web",
        req_method=ReqMethod.TEAM_DELETE,
        params={"mode": "team", "team_name": "demo-team"},
    )
    await server._handle_team_delete(ws, request, asyncio.Lock())

    assert stop_kwargs == [{}]
    assert ws.sent[-1]["ok"] is True


@pytest.mark.asyncio
async def test_plain_disconnect_does_not_emit_root_evict(monkeypatch: pytest.MonkeyPatch) -> None:
    server = agent_ws_server_module.AgentWebSocketServer()
    server._agent_manager = SimpleNamespace(
        cancel_all_inflight_work=AsyncMock(return_value=None),
    )
    server._stop_scheduler = AsyncMock(return_value=None)
    calls: list[dict] = []

    async def fake_evict(**kwargs):
        calls.append(kwargs)
        return KVCacheLifecycleResult(status="ok")

    class EmptyWebSocket:
        remote_address = ("127.0.0.1", 10000)

        def __init__(self) -> None:
            self.sent: list[str] = []

        async def send(self, payload: str) -> None:
            self.sent.append(payload)

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle.evict_session_kv_cache",
        fake_evict,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.cancel_all_team_stream_tasks_across_managers",
        AsyncMock(return_value=None),
    )

    await server._connection_handler(EmptyWebSocket())

    assert calls == []


@pytest.mark.asyncio
async def test_team_switch_leaves_offload_to_product_task_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    events: list[str] = []

    async def _dispatch(*_args, **_kwargs) -> bool:
        events.append("offload")
        return True

    async def _stop(*_args, **_kwargs) -> bool:
        events.append("baseline-stop")
        return True

    manager._active_team_names["old-session"] = "demo-team"
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.kv_cache_hooks.dispatch_for_session",
        _dispatch,
    )
    monkeypatch.setattr(manager, "_is_distributed_mode", lambda _cfg: True)
    monkeypatch.setattr(manager, "stop_session_runtime", _stop)

    await manager.prepare_session_switch(
        "new-session",
        reason="test: ",
        previous_session_id="old-session",
    )

    assert events == ["baseline-stop"]


@pytest.mark.asyncio
async def test_local_team_switch_does_not_drive_kvc_or_stop_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    events: list[str] = []

    async def _dispatch(*_args, **_kwargs) -> bool:
        events.append("offload")
        return True

    async def _stop(*_args, **_kwargs) -> bool:
        events.append("baseline-stop")
        return True

    manager._active_team_names["old-session"] = "demo-team"
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.kv_cache_hooks.dispatch_for_session",
        _dispatch,
    )
    monkeypatch.setattr(manager, "_is_distributed_mode", lambda _cfg: False)
    monkeypatch.setattr(manager, "stop_session_runtime", _stop)

    await manager.prepare_session_switch(
        "new-session",
        reason="test: ",
        previous_session_id="old-session",
    )

    assert events == []


@pytest.mark.asyncio
async def test_team_session_delete_keeps_baseline_stop_before_runner_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    events: list[str] = []

    async def _stop(*_args, **_kwargs) -> bool:
        events.append("baseline-stop")
        return True

    async def _delete(**_kwargs) -> bool:
        events.append("baseline-delete")
        return True

    monkeypatch.setattr(manager, "_resolve_delete_session_team_name", lambda _sid: "demo-team")
    monkeypatch.setattr(manager, "stop_session_runtime", _stop)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.Runner.delete_agent_team",
        _delete,
    )
    assert await manager.delete_session_runtime("team-session", reason="test: ")
    assert events == [
        "baseline-stop",
        "baseline-delete",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("gate_raises", [False, True])
async def test_team_kvc_owner_hook_contains_disabled_or_failed_affinity_gate(
    monkeypatch: pytest.MonkeyPatch,
    gate_raises: bool,
) -> None:
    manager = TeamManager()
    dispatch = AsyncMock(return_value=True)
    binding_lookup = Mock(
        side_effect=AssertionError("disabled affinity must not resolve Team binding")
    )

    def affinity_gate() -> bool:
        if gate_raises:
            raise RuntimeError("config broken")
        return False

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache.kv_cache_lifecycle."
        "is_kv_cache_affinity_enabled",
        affinity_gate,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.kv_cache_hooks.Runner."
        "dispatch_agent_team_kv_cache",
        dispatch,
    )
    monkeypatch.setattr(manager, "_lookup_session_team_name", binding_lookup)

    assert await manager.offload_session_kv_cache("team-session", reason="test") is False
    assert await manager.prefetch_session_kv_cache("team-session", reason="test") is False
    assert (
        await team_kv_cache_hooks.dispatch_signal(
            "offload",
            session_id="team-session",
            team_name="demo-team",
            reason="test",
        )
        is False
    )
    binding_lookup.assert_not_called()
    dispatch.assert_not_awaited()
