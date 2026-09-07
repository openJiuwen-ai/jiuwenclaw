# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parked compose tests that need agent-core to ship ``builtin_rules.yaml``.

CI ``pip install openjiuwen`` currently omits ``harness/resources/*.yaml``,
so package catalog inlining is empty. After agent-core package-data is
fixed and jiuwenswarm's openjiuwen pin is bumped, copy these functions
back into ``test_permission_compose_p1.py``.

This filename does not start with ``test_``, so pytest will not collect it.
"""

from __future__ import annotations

from openjiuwen.harness.security import PermissionLevel
from openjiuwen.harness.security.permission_engine.toolguard.tool_policy import (
    evaluate_tiered_policy,
)

from jiuwenswarm.agents.harness.common.rails.permissions.permission_compose import (
    compose_host_effective_permissions,
)


def test_missing_user_and_session_are_empty() -> None:
    effective = compose_host_effective_permissions(
        global_permissions={
            "enabled": True,
            "tools": {"bash": "ask"},
            "defaults": {"*": "allow"},
        },
        user_permissions=None,
        session_permissions=None,
    )
    assert effective["enabled"] is True
    assert effective["tools"]["bash"] == "ask"
    assert any(r.get("layer") == "builtin" for r in effective.get("rules") or [])


def test_effective_critical_still_denies() -> None:
    effective = compose_host_effective_permissions(
        global_permissions={
            "enabled": True,
            "tools": {"bash": "allow"},
            "defaults": {"*": "allow"},
        }
    )
    level, matched = evaluate_tiered_policy(
        effective, "bash", {"command": "shutdown -h now"},
    )
    assert level == PermissionLevel.DENY
    assert "builtin" in matched


def test_compose_fills_net_guard_and_package_urls() -> None:
    effective = compose_host_effective_permissions(
        global_permissions={"enabled": True, "defaults": {"*": "allow"}},
    )
    ng = effective["net_guard"]
    assert ng["enabled"] is True
    assert ng["defaults"] == "allow"
    assert ng["urls"]["localhost"] == "deny"
    assert ng["urls"]["169.254.169.254"] == "deny"


def test_user_cannot_disable_net_guard_or_widen_deny() -> None:
    effective = compose_host_effective_permissions(
        global_permissions={
            "enabled": True,
            "defaults": {"*": "allow"},
            "net_guard": {
                "enabled": True,
                "defaults": "deny",
                "urls": {"evil.example": "deny"},
            },
        },
        user_permissions={
            "net_guard": {
                "enabled": False,
                "defaults": "allow",
                "urls": {"evil.example": "allow", "corp.example": "deny"},
            }
        },
    )
    ng = effective["net_guard"]
    assert ng["enabled"] is True
    assert ng["defaults"] == "deny"
    assert ng["urls"]["evil.example"] == "deny"
    assert ng["urls"]["corp.example"] == "deny"
    assert ng["urls"]["localhost"] == "deny"
