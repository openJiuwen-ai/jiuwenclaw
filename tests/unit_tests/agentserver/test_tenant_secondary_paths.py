# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tenant secondary path resolution."""

from __future__ import annotations

from pathlib import Path

from jiuwenclaw.agentserver.extensions.rail_manager import RailManager
from jiuwenclaw.agentserver.tenant_context import (
    bind_tenant_workspace_dirs,
    reset_tenant_workspace_dirs,
)
import jiuwenclaw.utils as utils


def _bind_office_workspace() -> object:
    office_ws = Path("/data/service_default/agent_office/agent/jiuwenclaw_workspace")
    agent_root = office_ws.parent
    tenant_root = agent_root.parent
    return bind_tenant_workspace_dirs(
        jiuwenclaw_workspace=str(office_ws),
        agent_root=str(agent_root),
        tenant_root=str(tenant_root),
    )


def test_get_agent_workspace_dir_with_bind():
    tokens = _bind_office_workspace()
    try:
        ws = utils.get_agent_workspace_dir()
        assert "agent_office" in str(ws)
        assert ws.name == "jiuwenclaw_workspace"
    finally:
        reset_tenant_workspace_dirs(tokens)


def test_rail_manager_extensions_dir_follows_tenant_not_bind(tmp_path, monkeypatch):
    """Rail extensions dir is keyed by manager tenant, not ContextVar bind."""
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.extensions.rail_manager.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"service_{sid}" / f"agent_{aid}",
    )
    tokens = _bind_office_workspace()
    try:
        # Direct RailManager() is default/default; bind must not redirect it.
        mgr = RailManager()
        ext_dir = mgr.extensions_dir
        assert "service_default" in str(ext_dir)
        assert "agent_default" in str(ext_dir)
        assert "agent_office" not in str(ext_dir)
        assert ext_dir.name == "extensions"
    finally:
        reset_tenant_workspace_dirs(tokens)


def test_unbound_fallback_still_default():
    ws = utils.get_agent_workspace_dir()
    assert "agent_default" in str(ws) or "jiuwenclaw_workspace" in str(ws)
