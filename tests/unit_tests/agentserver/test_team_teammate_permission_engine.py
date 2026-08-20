# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Teammate permission rail uses jiuwenclaw engine via adapter (not bare harness)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.team.rails.permission_engine_adapter import (
    JiuwenclawPermissionEngineAdapter,
)
from jiuwenclaw.agentserver.team.team_runtime_inheritance import build_team_permission_rails
from jiuwenclaw.agentserver.permissions.core import PermissionEngine as JiuwenclawPermissionEngine
from openjiuwen.harness.security.models import PermissionLevel as HarnessPermissionLevel


@pytest.mark.asyncio
async def test_adapter_maps_jiuwenclaw_allow_for_bash() -> None:
    """tools.bash=allow must stay ALLOW (not harness shell-subcommand fallback ASK)."""
    inner = JiuwenclawPermissionEngine(
        config={"enabled": True, "tools": {"bash": "allow"}},
    )
    adapter = JiuwenclawPermissionEngineAdapter(inner)
    result = await adapter.check_permission("bash", {})
    assert result.permission == HarnessPermissionLevel.ALLOW


def test_teammate_uses_jiuwenclaw_engine_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    created: dict[str, object] = {}

    class _FakeTeamPermissionRail:
        def __init__(self, *, config, engine=None, host=None):
            created["config"] = config
            created["engine"] = engine
            created["host"] = host

    class _FakeOrchestrator:
        def __init__(self, **kwargs):
            pass

        async def handle_approval_request(self, request):
            return "interrupt"

    monkeypatch.setattr(
        "openjiuwen.agent_teams.rails.team_permission_rail.TeamPermissionRail",
        _FakeTeamPermissionRail,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.rails.team_permission_rail.TeamApprovalOrchestrator",
        _FakeOrchestrator,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.tools.message_manager.TeamMessageManager",
        lambda *a, **k: MagicMock(),
    )

    rails = build_team_permission_rails(
        role="teammate",
        language="cn",
        permissions_config={
            "enabled": True,
            "tools": {"bash": "ask", "read_file": "allow"},
        },
        team_backend=MagicMock(team_name="t", db=MagicMock()),
        messager=MagicMock(),
        member_name="worker",
        leader_member_name="office",
    )

    assert len(rails) == 1
    assert isinstance(created["engine"], JiuwenclawPermissionEngineAdapter)
    assert created["host"] is not None
    assert created["host"].request_permission_confirmation is not None


def test_teammate_skips_when_permissions_disabled() -> None:
    rails = build_team_permission_rails(
        role="teammate",
        language="cn",
        permissions_config={"enabled": False, "tools": {"bash": "ask"}},
        team_backend=MagicMock(),
        messager=MagicMock(),
        member_name="worker",
        leader_member_name="office",
    )
    assert rails == []
