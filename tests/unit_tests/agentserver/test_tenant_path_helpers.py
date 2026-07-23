# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.local_env_config import bind_agent_env_ns, reset_agent_env_ns
from jiuwenclaw.utils import (
    resolve_tenant_agent_root_dir,
    resolve_tenant_agent_workspace_dir,
    resolve_tenant_env_ns,
)


def test_resolve_tenant_env_ns_explicit():
    assert resolve_tenant_env_ns("svc", "aid") == ("svc", "aid")


def test_resolve_tenant_env_ns_bound(tmp_path, monkeypatch):
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    token = bind_agent_env_ns("default", "office")
    try:
        assert resolve_tenant_env_ns() == ("default", "office")
        ws = resolve_tenant_agent_workspace_dir()
        assert ws == (
            tmp_path
            / "service_default"
            / "agent_office"
            / "agent"
            / "jiuwenclaw_workspace"
        )
        assert resolve_tenant_agent_root_dir() == (
            tmp_path / "service_default" / "agent_office" / "agent"
        )
    finally:
        reset_agent_env_ns(token)


def test_resolve_tenant_env_ns_unbound_raises():
    with pytest.raises(TypeError, match="tenant scope is required"):
        resolve_tenant_env_ns()
