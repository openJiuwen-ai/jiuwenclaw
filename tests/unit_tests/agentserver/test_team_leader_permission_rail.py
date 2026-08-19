# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team permission rails must mount plan-style guardrail on the leader."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.team.team_runtime_inheritance import build_team_permission_rails


def test_leader_mounts_permission_interrupt_rail_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    policy = MagicMock(name="TeamPermissionPolicyRail")
    perm = MagicMock(name="PermissionInterruptRail")

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.rails.team_permission_policy_rail.TeamPermissionPolicyRail",
        lambda **kwargs: policy,
    )

    def _fake_build_permission_rail(*, config):
        assert config.get("permissions", {}).get("enabled") is True
        assert config["permissions"]["tools"]["deepresearch_stream"] == "ask"
        return perm

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers.build_permission_rail",
        _fake_build_permission_rail,
    )

    rails = build_team_permission_rails(
        role="leader",
        language="cn",
        permissions_config={
            "enabled": True,
            "tools": {"deepresearch_stream": "ask", "ask_user_question": "ask"},
        },
        team_backend=None,
        messager=None,
        member_name="office",
        leader_member_name="office",
    )

    assert policy in rails
    assert perm in rails
    assert rails.index(policy) < rails.index(perm)


def test_leader_skips_rails_when_permissions_disabled() -> None:
    rails = build_team_permission_rails(
        role="leader",
        language="cn",
        permissions_config={"enabled": False, "tools": {"bash": "ask"}},
        team_backend=None,
        messager=None,
        member_name="office",
        leader_member_name="office",
    )
    assert rails == []
