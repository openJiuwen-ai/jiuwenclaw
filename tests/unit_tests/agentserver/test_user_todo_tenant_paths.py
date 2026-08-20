# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for user_todos lazy per-tenant workspace paths (方案 A)."""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.tools.user_todo_tool import (
    UserTodosParams,
    _get_todos_dir,
    _handle_user_todos,
    set_global_workspace_dir,
)
from jiuwenclaw.local_env_config import bind_agent_env_ns, reset_agent_env_ns


@pytest.fixture(autouse=True)
def _clear_workspace_override():
    set_global_workspace_dir("")
    yield
    set_global_workspace_dir("")


@pytest.mark.asyncio
async def test_todos_dir_follows_bound_tenant(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    token = bind_agent_env_ns("default", "office")
    try:
        todos_dir = Path(_get_todos_dir())
        assert todos_dir == (
            tmp_path
            / "service_default"
            / "agent_office"
            / "agent"
            / "jiuwenclaw_workspace"
            / "memory"
            / "user_todos"
        )
    finally:
        reset_agent_env_ns(token)


@pytest.mark.asyncio
async def test_create_isolates_tenants(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )

    token_office = bind_agent_env_ns("default", "office")
    try:
        result = await _handle_user_todos(
            UserTodosParams(action="create", title="office-todo", channel_id="web")
        )
        assert result["success"] is True
        office_file = (
            tmp_path
            / "service_default"
            / "agent_office"
            / "agent"
            / "jiuwenclaw_workspace"
            / "memory"
            / "user_todos"
            / "web.md"
        )
        assert office_file.exists()
    finally:
        reset_agent_env_ns(token_office)

    token_default = bind_agent_env_ns("default", "default")
    try:
        listed = await _handle_user_todos(
            UserTodosParams(action="list", channel_id="web")
        )
        assert listed["success"] is True
        assert listed["count"] == 0
        default_file = (
            tmp_path
            / "service_default"
            / "agent_default"
            / "agent"
            / "jiuwenclaw_workspace"
            / "memory"
            / "user_todos"
            / "web.md"
        )
        assert not default_file.exists()
    finally:
        reset_agent_env_ns(token_default)


def test_workspace_override_for_tests(tmp_path):
    set_global_workspace_dir(str(tmp_path / "custom_ws"))
    assert Path(_get_todos_dir()) == tmp_path / "custom_ws" / "memory" / "user_todos"


def test_unbound_requires_tenant_scope():
    with pytest.raises(TypeError, match="tenant scope is required"):
        _get_todos_dir()
