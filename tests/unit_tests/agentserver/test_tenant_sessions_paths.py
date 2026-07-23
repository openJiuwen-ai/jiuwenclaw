# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tenant-aware sessions helpers and WS path resolution."""

from __future__ import annotations

import json
from pathlib import Path

from jiuwenclaw.agentserver.agent_ws_server import (
    AgentWebSocketServer,
    _sessions_dir_for_request,
)
from jiuwenclaw.agentserver.diff_service import DiffService
from jiuwenclaw.agentserver.session_metadata import (
    get_session_metadata,
    init_session_metadata,
    update_session_metadata,
)
from jiuwenclaw.agentserver.session_rename import apply_session_rename
from jiuwenclaw.agentserver.tenant_context import (
    bind_tenant_workspace_dirs,
    reset_tenant_workspace_dirs,
)
from jiuwenclaw.agentserver.tools.todo_toolkits import TodoToolkit
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.utils import get_agent_sessions_dir, resolve_tenant_sessions_dir


def test_resolve_tenant_sessions_dir_uses_agent_id(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    path = resolve_tenant_sessions_dir("default", "office")
    assert path == tmp_path / "service_default" / "agent_office" / "agent" / "sessions"


def test_get_agent_sessions_dir_respects_bind(tmp_path):
    office_ws = tmp_path / "service_default" / "agent_office" / "agent" / "jiuwenclaw_workspace"
    agent_root = office_ws.parent
    tenant_root = agent_root.parent
    tokens = bind_tenant_workspace_dirs(
        jiuwenclaw_workspace=str(office_ws),
        agent_root=str(agent_root),
        tenant_root=str(tenant_root),
    )
    try:
        assert get_agent_sessions_dir() == agent_root / "sessions"
    finally:
        reset_tenant_workspace_dirs(tokens)


def test_todo_toolkit_uses_bound_sessions_dir(tmp_path):
    office_ws = tmp_path / "service_default" / "agent_office" / "agent" / "jiuwenclaw_workspace"
    agent_root = office_ws.parent
    tenant_root = agent_root.parent
    tokens = bind_tenant_workspace_dirs(
        jiuwenclaw_workspace=str(office_ws),
        agent_root=str(agent_root),
        tenant_root=str(tenant_root),
    )
    try:
        toolkit = TodoToolkit(session_id="sess_office")
        assert toolkit.todo_dir == agent_root / "sessions" / "sess_office"
        assert "agent_office" in str(toolkit.todo_dir)
        assert "agent_default" not in str(toolkit.todo_dir)
    finally:
        reset_tenant_workspace_dirs(tokens)


def test_todo_toolkit_uses_explicit_tenant_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    toolkit = TodoToolkit(
        session_id="sess_office",
        service_id="default",
        agent_id="office",
    )
    expected = (
        tmp_path / "service_default" / "agent_office" / "agent" / "sessions" / "sess_office"
    )
    assert toolkit.todo_dir == expected


def test_todo_toolkit_uses_bound_env_ns(tmp_path, monkeypatch):
    from jiuwenclaw.local_env_config import bind_agent_env_ns, reset_agent_env_ns

    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    token = bind_agent_env_ns("default", "office")
    try:
        toolkit = TodoToolkit(session_id="sess_office")
        expected = (
            tmp_path
            / "service_default"
            / "agent_office"
            / "agent"
            / "sessions"
            / "sess_office"
        )
        assert toolkit.todo_dir == expected
    finally:
        reset_agent_env_ns(token)


def test_sessions_dir_for_request_officeclaw():
    request = AgentRequest(
        request_id="r1",
        channel_id="officeclaw",
        session_id="s1",
        agent_id="office",
        service_id="default",
        params={},
    )
    path = _sessions_dir_for_request(request)
    assert "agent_office" in str(path)
    assert path.name == "sessions"


def test_get_conversation_history_reads_tenant_root(tmp_path):
    session_id = "sess_tenant"
    sessions_dir = tmp_path / "service_default" / "agent_office" / "agent" / "sessions"
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True)
    history_path = session_dir / "history.json"
    records = [
        {"id": "req1:user", "role": "user", "request_id": "req1", "content": "hi"},
        {
            "id": "req1:assistant",
            "role": "assistant",
            "request_id": "req1",
            "event_type": "chat.delta",
            "content": "yo",
        },
    ]
    history_path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    # default path has nothing — would fail without sessions_root
    assert (
        AgentWebSocketServer.get_conversation_history(session_id=session_id, page_idx=1)
        is None
    )

    result = AgentWebSocketServer.get_conversation_history(
        session_id=session_id,
        page_idx=1,
        sessions_root=sessions_dir,
    )
    assert result is not None
    assert len(result["messages"]) == 2


def test_session_metadata_isolated_by_sessions_root(tmp_path):
    root_a = tmp_path / "agent_office" / "sessions"
    root_b = tmp_path / "agent_assistant" / "sessions"
    root_a.mkdir(parents=True)
    root_b.mkdir(parents=True)

    init_session_metadata(
        session_id="same_sid",
        channel_id="officeclaw",
        title="office-title",
        sessions_root=root_a,
    )
    init_session_metadata(
        session_id="same_sid",
        channel_id="officeclaw",
        title="assistant-title",
        sessions_root=root_b,
    )
    update_session_metadata(
        session_id="same_sid",
        title="office-title-updated",
        sessions_root=root_a,
    )

    meta_a = get_session_metadata("same_sid", sessions_root=root_a)
    meta_b = get_session_metadata("same_sid", sessions_root=root_b)
    assert meta_a.get("title") == "office-title-updated"
    assert meta_b.get("title") == "assistant-title"


def test_apply_session_rename_uses_sessions_root(tmp_path):
    root = tmp_path / "agent_office" / "sessions"
    root.mkdir(parents=True)
    ok, payload, err, code = apply_session_rename(
        {"session_id": "s1", "title": "renamed"},
        "s1",
        init_channel_id="officeclaw",
        sessions_root=root,
    )
    assert ok and err is None and code is None
    assert payload["title"] == "renamed"
    assert (root / "s1" / "metadata.json").exists()


def test_diff_service_reads_tenant_history(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    sessions_dir = (
        tmp_path / "service_default" / "agent_office" / "agent" / "sessions"
    )
    session_id = "s_diff"
    session_dir = sessions_dir / session_id
    session_dir.mkdir(parents=True)
    history = [
        {"role": "user", "content": "edit file", "timestamp": 100.0},
        {
            "role": "assistant",
            "event_type": "chat.final",
            "content": "done",
            "timestamp": 101.0,
        },
    ]
    (session_dir / "history.json").write_text(
        json.dumps(history),
        encoding="utf-8",
    )

    service = DiffService()
    assert service._read_history(session_id) == []
    loaded = service._read_history(
        session_id,
        service_id="default",
        agent_id="office",
    )
    assert len(loaded) == 2
    assert loaded[0]["content"] == "edit file"
    # path override still works without identity reverse-engineering
    loaded_override = service._read_history(session_id, sessions_root=sessions_dir)
    assert len(loaded_override) == 2


def test_diff_service_agent_history_defaults_to_default_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.diff_service.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    hist_dir = (
        tmp_path
        / "service_default"
        / "agent_default"
        / "agent"
        / "jiuwenclaw_workspace"
        / ".agent_history"
    )
    hist_dir.mkdir(parents=True)
    payload = {
        "/tmp/a.py": [
            {
                "timestamp": 100.0,
                "action": "write",
                "old_content": None,
                "new_content": "x",
            }
        ]
    }
    (hist_dir / "file_ops_jiuwenclaw.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    service = DiffService()
    # No sid/aid → normalize to default/default tenant tree (not global .agent_history)
    loaded = service._read_agent_history()
    assert "/tmp/a.py" in loaded
    assert len(loaded["/tmp/a.py"]) == 1

    office_dir = (
        tmp_path
        / "service_default"
        / "agent_office"
        / "agent"
        / "jiuwenclaw_workspace"
        / ".agent_history"
    )
    office_dir.mkdir(parents=True)
    (office_dir / "file_ops_jiuwenclaw.json").write_text(
        json.dumps({"/tmp/b.py": [{"timestamp": 1.0, "action": "write", "old_content": None, "new_content": "y"}]}),
        encoding="utf-8",
    )
    office_loaded = service._read_agent_history(service_id="default", agent_id="office")
    assert "/tmp/b.py" in office_loaded
    assert "/tmp/a.py" not in office_loaded
