# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for per-tenant CronTools path + AgentCronRegistry."""

from __future__ import annotations

from jiuwenswarm.agents.harness.common.tools.cron.cron_tools import (
    CronTools,
    resolve_cron_jobs_path,
)
from jiuwenswarm.server.runtime.cron_local_runtime import AgentCronRegistry
from tests.unit_tests.tenant_workspace_test_helpers import (
    patch_multi_tenant_workspace_dirs,
    tenant_workspace_key,
    tenant_workspace_root,
)


def test_resolve_cron_jobs_path_isolates_tenants(tmp_path, monkeypatch):
    patch_multi_tenant_workspace_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.cron.cron_tools.get_multi_tenant_user_workspace_dir",
        lambda wk: tenant_workspace_root(tmp_path, wk),
    )
    office = resolve_cron_jobs_path("default", "office")
    default = resolve_cron_jobs_path("default", "default")
    assert office != default
    assert office.name == "cron_jobs.json"
    assert tenant_workspace_key("default", "office") in str(office)
    assert tenant_workspace_key("default", "default") in str(default)


def test_cron_tools_store_path_follows_tenant(tmp_path, monkeypatch):
    patch_multi_tenant_workspace_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.cron.cron_tools.get_multi_tenant_user_workspace_dir",
        lambda wk: tenant_workspace_root(tmp_path, wk),
    )
    AgentCronRegistry.reset_for_tests()
    tools = CronTools(service_id="svc", agent_id="office")
    assert tenant_workspace_key("svc", "office") in str(tools._local_store.path)
    assert tools._service_id == "svc"
    assert tools._agent_id == "office"


def test_agent_cron_registry_get_or_create_shares_instance():
    AgentCronRegistry.reset_for_tests()
    a = AgentCronRegistry.get_or_create(
        "s1", "a1", factory=lambda: CronTools(service_id="s1", agent_id="a1")
    )
    b = AgentCronRegistry.get_or_create(
        "s1", "a1", factory=lambda: CronTools(service_id="s1", agent_id="a1")
    )
    assert a is b
    assert AgentCronRegistry.is_current("s1", "a1", a)
    AgentCronRegistry.reset_for_tests()
