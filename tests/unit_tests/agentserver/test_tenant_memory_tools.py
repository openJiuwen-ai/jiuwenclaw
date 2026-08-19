# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tenant memory workspace binding."""

from __future__ import annotations

from pathlib import Path

from jiuwenclaw.agentserver.tenant_context import (
    bind_tenant_workspace_dirs,
    reset_tenant_workspace_dirs,
)
from jiuwenclaw.agentserver.tools.memory_tools import (
    _resolve_workspace_dir,
    bind_memory_workspace_dir,
    reset_memory_workspace_dir,
)
import jiuwenclaw.utils as utils


def test_bind_memory_workspace_dir():
    office_ws = "/data/service_default/agent_office/agent/jiuwenclaw_workspace"
    token = bind_memory_workspace_dir(office_ws)
    try:
        assert _resolve_workspace_dir() == office_ws
    finally:
        reset_memory_workspace_dir(token)


def test_unbound_requires_tenant_scope():
    import pytest

    with pytest.raises(TypeError, match="tenant scope is required"):
        _resolve_workspace_dir()


def test_tenant_context_updates_utils_helpers():
    office_ws = Path("/data/service_default/agent_office/agent/jiuwenclaw_workspace")
    agent_root = office_ws.parent
    tenant_root = agent_root.parent
    tokens = bind_tenant_workspace_dirs(
        jiuwenclaw_workspace=str(office_ws),
        agent_root=str(agent_root),
        tenant_root=str(tenant_root),
    )
    try:
        assert utils.get_agent_workspace_dir() == office_ws
        assert utils.get_agent_workspace_dir().name == "jiuwenclaw_workspace"
    finally:
        reset_tenant_workspace_dirs(tokens)
