from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.gateway.channel_manager.tui.tui_connect import (
    CLI_FORWARD_NO_LOCAL_HANDLER_METHODS,
    CLI_FORWARD_REQ_METHODS,
    CliHandlersBindParams,
    register_cli_handlers,
)


class _TuiChannel:
    def __init__(self) -> None:
        self.local_handlers: dict[str, dict[str, object]] = {}
        self.responses: list[dict] = []

    def register_local_handler(self, path, method, handler) -> None:
        self.local_handlers.setdefault(path, {})[method] = handler

    async def send_response(self, _ws, req_id, *, ok, payload=None, error=None, code=None):
        self.responses.append(
            {"id": req_id, "ok": ok, "payload": payload or {}, "error": error, "code": code}
        )


class _SuccessfulAgentClient:
    def __init__(self) -> None:
        self.requests: list[object] = []

    async def send_request(self, request):
        self.requests.append(request)
        return SimpleNamespace(ok=True, payload={})


def test_session_switch_is_forwarded_without_a_tui_local_handler() -> None:
    channel = _TuiChannel()
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=None, path="/tui")
    )

    assert "session.switch" in CLI_FORWARD_REQ_METHODS
    assert "session.switch" in CLI_FORWARD_NO_LOCAL_HANDLER_METHODS
    assert "session.switch" not in channel.local_handlers.get("/tui", {})


@pytest.mark.asyncio
async def test_session_create_dispatches_previous_plan_root_offload(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    channel = _TuiChannel()
    calls: list[dict] = []

    monkeypatch.setattr("jiuwenswarm.common.utils.get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id: {"mode": "agent.plan"},
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.init_session_metadata",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.dispatch_offload_session_kv_cache",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.is_kv_cache_affinity_enabled",
        lambda: True,
    )
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=None, path="/tui")
    )

    await channel.local_handlers["/tui"]["session.create"](
        object(),
        "create-tui",
        {
            "session_id": "new-plan-root",
            "previous_session_id": "old-plan-root",
        },
        "old-plan-root",
    )

    assert calls == [
        {
            "session_id": "old-plan-root",
            "parent_session_id": "old-plan-root",
        }
    ]
    assert (sessions_root / "new-plan-root").is_dir()
    assert channel.responses[-1]["ok"] is True


@pytest.mark.asyncio
async def test_session_create_does_not_apply_plan_root_action_to_team_owner(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    channel = _TuiChannel()
    calls: list[dict] = []

    monkeypatch.setattr("jiuwenswarm.common.utils.get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id: {"mode": "team"},
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.init_session_metadata",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.dispatch_offload_session_kv_cache",
        lambda **kwargs: calls.append(kwargs),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.is_kv_cache_affinity_enabled",
        lambda: True,
    )
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=None, path="/tui")
    )

    await channel.local_handlers["/tui"]["session.create"](
        object(),
        "create-tui-team-owner",
        {
            "session_id": "new-plan-root",
            "previous_session_id": "old-team-root",
        },
        "old-team-root",
    )

    assert calls == []
    assert channel.responses[-1]["ok"] is True


@pytest.mark.asyncio
async def test_session_create_skips_kvc_metadata_when_affinity_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    channel = _TuiChannel()

    monkeypatch.setattr("jiuwenswarm.common.utils.get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.init_session_metadata",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id: pytest.fail("disabled affinity must not read previous metadata"),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.is_kv_cache_affinity_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.dispatch_offload_session_kv_cache",
        lambda **kwargs: pytest.fail("disabled affinity must not dispatch offload"),
    )
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=None, path="/tui")
    )

    await channel.local_handlers["/tui"]["session.create"](
        object(),
        "create-tui-disabled",
        {
            "session_id": "new-plan-root",
            "previous_session_id": "old-plan-root",
        },
        "old-plan-root",
    )

    assert (sessions_root / "new-plan-root").is_dir()
    assert channel.responses[-1]["ok"] is True


@pytest.mark.asyncio
async def test_session_create_contains_kvc_metadata_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    channel = _TuiChannel()

    monkeypatch.setattr("jiuwenswarm.common.utils.get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.init_session_metadata",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.get_session_metadata",
        lambda session_id: (_ for _ in ()).throw(RuntimeError("metadata broken")),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.is_kv_cache_affinity_enabled",
        lambda: True,
    )
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=None, path="/tui")
    )

    await channel.local_handlers["/tui"]["session.create"](
        object(),
        "create-tui-kvc-failure",
        {
            "session_id": "new-plan-root",
            "previous_session_id": "old-plan-root",
        },
        "old-plan-root",
    )

    assert (sessions_root / "new-plan-root").is_dir()
    assert channel.responses[-1]["ok"] is True


@pytest.mark.asyncio
async def test_session_create_prefers_canonical_switch_owner_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    sessions_root = tmp_path / "sessions"
    sessions_root.mkdir()
    channel = _TuiChannel()
    agent_client = _SuccessfulAgentClient()

    monkeypatch.setattr("jiuwenswarm.common.utils.get_agent_sessions_dir", lambda: sessions_root)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.session_metadata.init_session_metadata",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.kv_cache_affinity_lifecycle.dispatch_offload_session_kv_cache",
        lambda **kwargs: pytest.fail("forwarded lifecycle must not also run local fallback"),
    )
    register_cli_handlers(
        CliHandlersBindParams(channel=channel, agent_client=agent_client, path="/tui")
    )

    await channel.local_handlers["/tui"]["session.create"](
        object(),
        "create-tui-forwarded",
        {
            "session_id": "new-team-root",
            "previous_session_id": "old-team-root",
            "mode": "team",
            "previous_mode": "team",
        },
        "old-team-root",
    )

    assert len(agent_client.requests) == 1
    request = agent_client.requests[0]
    assert request.method == "session.switch"
    assert request.params["session_id"] == "new-team-root"
    assert request.params["previous_session_id"] == "old-team-root"
    assert request.params["mode"] == "team"
    assert request.params["previous_mode"] == "team"
    assert channel.responses[-1]["ok"] is True
