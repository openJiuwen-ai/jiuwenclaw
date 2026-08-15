# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for enterprise skill hot-refresh (SkillUseRail + resource_mgr)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.schema.message import ReqMethod

try:
    from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
except ImportError:  # Python < 3.11 (typing.Self)
    JiuWenClawDeepAdapter = None  # type: ignore[misc, assignment]


class _FakeResourceManager:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.removed: list[str] = []

    def remove_tool(self, tool_id: str) -> object | None:
        self.removed.append(tool_id)
        return self.tools.pop(tool_id, None)


class _FakeSkillUseRail:
    def __init__(self, owned_tool_ids: set[str]) -> None:
        self._owned_tool_ids = owned_tool_ids


def test_purge_skill_use_rail_owned_tools_removes_from_resource_mgr(monkeypatch) -> None:
    if JiuWenClawDeepAdapter is None:
        pytest.skip("JiuWenClawDeepAdapter requires Python 3.11+")
    resource_mgr = _FakeResourceManager()
    resource_mgr.tools["SkillTool_tenant"] = object()
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.Runner",
        SimpleNamespace(resource_mgr=resource_mgr),
    )

    JiuWenClawDeepAdapter._purge_skill_use_rail_owned_tools(
        _FakeSkillUseRail({"SkillTool_tenant", "SkillCompleteTool_tenant"}),
    )

    assert set(resource_mgr.removed) == {"SkillTool_tenant", "SkillCompleteTool_tenant"}
    assert resource_mgr.tools == {}


def test_purge_skill_use_rail_owned_tools_noop_for_none() -> None:
    if JiuWenClawDeepAdapter is None:
        pytest.skip("JiuWenClawDeepAdapter requires Python 3.11+")
    JiuWenClawDeepAdapter._purge_skill_use_rail_owned_tools(None)


@pytest.mark.asyncio
async def test_refresh_all_enabled_skills_from_db_calls_every_session_agent() -> None:
    agent_a = SimpleNamespace(refresh_enabled_skills_from_db=AsyncMock())
    agent_b = SimpleNamespace(refresh_enabled_skills_from_db=AsyncMock())
    manager = AgentManager(agent_id="agent-1", service_id="svc-1")
    manager.agents = {
        "web": {
            "agent": {
                "sess-a": agent_a,
                "sess-b": agent_b,
            }
        }
    }

    await manager.refresh_all_enabled_skills_from_db()

    agent_a.refresh_enabled_skills_from_db.assert_awaited_once()
    agent_b.refresh_enabled_skills_from_db.assert_awaited_once()


def test_should_refresh_enabled_skills_after_enterprise_install() -> None:
    request = SimpleNamespace(req_method=ReqMethod.SKILLS_ENTERPRISE_INSTALL)
    result = SimpleNamespace(ok=True, payload={"success": True})

    assert AgentManager._should_refresh_enabled_skills_after_request(request, result) is True


def test_should_not_refresh_when_install_failed() -> None:
    request = SimpleNamespace(req_method=ReqMethod.SKILLS_ENTERPRISE_INSTALL)
    result = SimpleNamespace(ok=True, payload={"success": False, "error_code": "x"})

    assert AgentManager._should_refresh_enabled_skills_after_request(request, result) is False
