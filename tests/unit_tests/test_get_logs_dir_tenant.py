# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for get_logs_dir service_id resolution (方案 A)."""

from __future__ import annotations

from jiuwenclaw.local_env_config import bind_agent_env_ns, reset_agent_env_ns
from jiuwenclaw.utils import get_logs_dir, get_service_root_dir


def test_get_logs_dir_default_without_bind():
    path = get_logs_dir()
    assert path == get_service_root_dir("default") / ".logs"
    assert path.name == ".logs"
    assert "service_default" in str(path)


def test_get_logs_dir_explicit_service_id():
    path = get_logs_dir("office_svc")
    assert path == get_service_root_dir("office_svc") / ".logs"
    assert "service_office_svc" in str(path)


def test_get_logs_dir_follows_bound_env_ns():
    token = bind_agent_env_ns("tenant_a", "agent_x")
    try:
        path = get_logs_dir()
        assert path == get_service_root_dir("tenant_a") / ".logs"
        assert "service_tenant_a" in str(path)
        assert "agent_" not in path.name
    finally:
        reset_agent_env_ns(token)


def test_get_logs_dir_explicit_overrides_bind():
    token = bind_agent_env_ns("tenant_a", "agent_x")
    try:
        path = get_logs_dir("other")
        assert path == get_service_root_dir("other") / ".logs"
    finally:
        reset_agent_env_ns(token)
