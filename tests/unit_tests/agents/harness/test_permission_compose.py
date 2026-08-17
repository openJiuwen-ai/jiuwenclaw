# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Product-mode compose lives in jiuwenswarm, not agent-core."""

from __future__ import annotations

from jiuwenswarm.agents.harness.common.rails.permissions.mode_presets import MODE_PRESETS
from jiuwenswarm.agents.harness.common.rails.permissions.permission_compose import (
    PermissionModeController,
)


def test_presets_exist() -> None:
    assert set(MODE_PRESETS) == {"full_access", "auto", "strict"}
    assert MODE_PRESETS["full_access"]["sandbox_intent"] == "optional"
    assert MODE_PRESETS["full_access"]["file_guard"]["enabled"] is False
    assert MODE_PRESETS["full_access"]["findings_escalate"] is False
    assert MODE_PRESETS["auto"]["sandbox_intent"] == "required"
    assert MODE_PRESETS["auto"]["defaults"]["*"] == "allow"
    assert MODE_PRESETS["auto"]["file_guard"]["workspace"]["write"] == "allow"
    assert MODE_PRESETS["strict"]["defaults"]["*"] == "ask"
    assert MODE_PRESETS["strict"]["file_guard"]["workspace"]["write"] == "ask"


def test_migrate_enabled_false_to_full_access() -> None:
    out = PermissionModeController.migrate_legacy({"enabled": False, "tools": {"bash": "ask"}})
    assert out["enabled"] is True
    assert out["mode"] == "full_access"
    assert out["ask_tools"] == ["bash"]


def test_migrate_permission_mode_strict() -> None:
    out = PermissionModeController.migrate_legacy({"enabled": True, "permission_mode": "strict"})
    assert out["mode"] == "strict"
    assert "permission_mode" not in out


def test_compose_default_mode_auto() -> None:
    eff = PermissionModeController().compose({"enabled": True})
    assert eff.mode == "auto"
    assert eff.sandbox_intent == "required"
    assert eff.permissions["defaults"]["*"] == "allow"
    assert eff.permissions["file_guard"]["enabled"] is True
    assert eff.permissions["permission_mode"] == "normal"
    assert eff.permissions["findings_escalate"] is True


def test_compose_full_access_forces_file_guard_off() -> None:
    eff = PermissionModeController().compose(
        {
            "enabled": True,
            "mode": "full_access",
            "file_guard": {
                "enabled": True,
                "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            },
        },
    )
    assert eff.mode == "full_access"
    assert eff.sandbox_intent == "optional"
    assert eff.permissions["file_guard"]["enabled"] is False
    assert eff.permissions["defaults"]["*"] == "allow"
    assert eff.permissions["findings_escalate"] is False
    assert eff.permissions["network"]["ignore_user_host_rules"] is True


def test_compose_ignores_product_defaults() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "auto", "defaults": {"*": "ask"}},
    )
    assert eff.permissions["defaults"]["*"] == "allow"


def test_compose_unknown_mode_falls_back_auto() -> None:
    eff = PermissionModeController().compose({"enabled": True, "mode": "weird"})
    assert eff.mode == "auto"


def test_compose_user_ask_tools_under_full_access() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "full_access"},
        {"ask_tools": ["bash"]},
    )
    assert eff.permissions["tools"]["bash"] == "ask"


def test_compose_deny_wins_over_ask() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "auto"},
        {"ask_tools": ["bash"], "deny_tools": ["bash"]},
    )
    assert eff.permissions["tools"]["bash"] == "deny"


def test_compose_session_ignores_tool_lists() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "auto"},
        None,
        {"deny_tools": ["bash"], "ask_tools": ["read_file"]},
    )
    tools = eff.permissions.get("tools") or {}
    assert "bash" not in tools
    assert "read_file" not in tools


def test_compose_strict_path_tools_and_sensitive_paths() -> None:
    eff = PermissionModeController().compose({"enabled": True, "mode": "strict"})
    paths = eff.permissions["file_guard"]["paths"]
    patterns = {p["path"] for p in paths if isinstance(p, dict)}
    assert "**/.ssh/**" in patterns or any(".ssh" in p for p in patterns)
    assert any(".env" in p for p in patterns)
    assert eff.permissions["permission_mode"] == "strict"
    assert eff.permissions["tools"]["read_file"] == "allow"
    assert eff.permissions["tools"]["write_file"] == "allow"


def test_compose_strict_user_ask_path_tool_not_overridden() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "strict"},
        {"ask_tools": ["read_file"]},
    )
    assert eff.permissions["tools"]["read_file"] == "ask"


def test_compose_session_allow_tools_merges_under_strict() -> None:
    eff = PermissionModeController().compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={"allow_tools": ["todo_list"]},
        session_cfg={"allow_tools": ["memory_get"], "ask_tools": ["bash"]},
    )
    tools = eff.permissions.get("tools") or {}
    assert tools.get("todo_list") == "allow"
    assert tools.get("memory_get") == "allow"
    assert "bash" not in tools


def test_compose_refeed_effective_keeps_allow_tools() -> None:
    ctrl = PermissionModeController()
    first = ctrl.compose(
        {"enabled": True, "mode": "strict"},
        user_cfg={"allow_tools": ["write_file", "todo_list"]},
    )
    second = ctrl.compose(first.permissions)
    assert (second.permissions.get("tools") or {}).get("write_file") == "allow"
    assert (second.permissions.get("tools") or {}).get("todo_list") == "allow"


def test_compose_yaml_allow_cannot_widen_builtin_deny() -> None:
    eff = PermissionModeController().compose(
        {
            "enabled": True,
            "mode": "auto",
            "file_guard": {
                "paths": [
                    {
                        "path": "**/.ssh/**",
                        "match": "glob",
                        "read": "allow",
                        "write": "allow",
                        "exec": "allow",
                    }
                ]
            },
        }
    )
    paths = (eff.permissions.get("file_guard") or {}).get("paths") or []
    ssh_entries = [
        p
        for p in paths
        if isinstance(p, dict) and str(p.get("path", "")).replace("\\", "/") == "**/.ssh/**"
    ]
    assert ssh_entries
    assert all(p.get("read") == p.get("write") == p.get("exec") == "deny" for p in ssh_entries)
    assert any(p.get("layer") == "builtin" for p in ssh_entries)


def test_compose_yaml_allow_cannot_widen_when_core_builtins_empty(monkeypatch) -> None:
    """agent-core 未带 sensitive_paths 时，产品 fallback 仍要挡住 YAML allow。"""
    import sys

    # pytest.setattr(dotted path) imports the target first and fails when
    # older agent-core has no fileguard.sensitive_paths module. Hide it in
    # sys.modules so compose hits ImportError and uses the product fallback.
    monkeypatch.setitem(
        sys.modules, "openjiuwen.harness.security.fileguard.sensitive_paths", None
    )

    eff = PermissionModeController().compose(
        {
            "enabled": True,
            "mode": "auto",
            "file_guard": {
                "paths": [
                    {
                        "path": "**/.ssh/**",
                        "match": "glob",
                        "read": "allow",
                        "write": "allow",
                        "exec": "allow",
                    }
                ]
            },
        }
    )
    paths = (eff.permissions.get("file_guard") or {}).get("paths") or []
    ssh_entries = [
        p
        for p in paths
        if isinstance(p, dict) and str(p.get("path", "")).replace("\\", "/") == "**/.ssh/**"
    ]
    assert ssh_entries
    assert all(p.get("read") == p.get("write") == p.get("exec") == "deny" for p in ssh_entries)
    assert any(p.get("layer") == "builtin" for p in ssh_entries)


def test_migrate_then_compose_enabled_false() -> None:
    ctrl = PermissionModeController()
    raw = ctrl.migrate_legacy({"enabled": False})
    eff = ctrl.compose(raw)
    assert eff.permissions["enabled"] is True
    assert eff.mode == "full_access"
    assert eff.sandbox_intent == "optional"
    assert eff.permissions["file_guard"]["enabled"] is False
