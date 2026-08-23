# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Request-installed permission config contracts for Auto Permission."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.auto_permission_rail import (
    AutoPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.owner_scopes import (
    TOOL_PERMISSION_CONTEXT,
    PermissionContext,
)
from tests.unit_tests.agentserver.permissions.auto_permission_test_support import (
    FakeBaseRail,
)


@pytest.mark.parametrize("native", [False, True])
def test_platform_root_reuses_core_trusted_dir_axes(tmp_path, native: bool) -> None:
    primary = tmp_path / "project"
    platform = tmp_path / "agent-workspace"
    permissions = {
        "enabled": True,
        "mode": "auto",
        "external_directory": {"*": "ask"},
    }
    if native:
        permissions["file_guard"] = {
            "enabled": True,
            "mode": "native",
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
        }
    rail = build_permission_rail(
        {"permissions": permissions},
        enable_auto_permission=True,
        workspace_root=primary,
        platform_trusted_root=platform,
    )
    guard = rail.base_rail._engine._file_guard
    target = platform / "skills" / "tool.py"

    assert guard._resolve_one(target, "read")[0].value == "allow"
    assert guard._resolve_one(target, "write")[0].value == "allow"
    assert guard._resolve_one(target, "exec")[0].value == "ask"
    if not native:
        assert guard._resolve_one(primary / "run.py", "exec")[0].value == "allow"


def test_platform_root_does_not_override_more_specific_path_rules(tmp_path) -> None:
    primary = tmp_path / "project"
    platform = tmp_path / "agent-workspace"
    ask_root = platform / "review-required"
    deny_root = platform / "blocked"
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "file_guard": {
                    "enabled": True,
                    "mode": "native",
                    "workspace": {
                        "read": "allow",
                        "write": "allow",
                        "exec": "ask",
                    },
                    "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
                    "paths": [
                        {
                            "path": ask_root.as_posix(),
                            "read": "ask",
                            "write": "ask",
                            "exec": "ask",
                            "match": "prefix",
                        },
                        {
                            "path": deny_root.as_posix(),
                            "read": "allow",
                            "write": "deny",
                            "exec": "deny",
                            "match": "prefix",
                        },
                    ],
                },
            }
        },
        enable_auto_permission=True,
        workspace_root=primary,
        platform_trusted_root=platform,
    )
    guard = rail.base_rail._engine._file_guard

    assert guard._resolve_one(ask_root / "input.md", "read")[0].value == "ask"
    assert guard._resolve_one(deny_root / "output.md", "write")[0].value == "deny"


def test_trusted_dirs_replace_and_clear_delegate_to_native_file_guard(tmp_path) -> None:
    base = FakeBaseRail()
    rail = AutoPermissionInterruptRail(
        base_rail=base,
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path,
    )
    trusted = [tmp_path.as_posix()]

    rail.set_trusted_dirs(trusted)
    rail.set_trusted_dirs([])
    rail.set_trusted_dirs(None)

    assert base.trusted_dirs_updates == [trusted, [], None]


def test_platform_root_survives_request_trusted_dir_replace_and_clear(tmp_path) -> None:
    base = FakeBaseRail()
    platform = tmp_path / "agent-workspace"
    request_root = tmp_path / "extra"
    rail = AutoPermissionInterruptRail(
        base_rail=base,
        permission_config={"enabled": True, "mode": "auto"},
        workspace_root=tmp_path / "project",
        platform_trusted_root=platform,
    )

    rail.set_trusted_dirs([request_root, platform])
    rail.set_trusted_dirs([])
    rail.set_trusted_dirs(None)

    assert base.trusted_dirs_updates == [
        (platform.resolve(), request_root.resolve()),
        (platform.resolve(),),
        (platform.resolve(),),
    ]


def test_auto_builder_binds_core_and_auto_to_same_primary_root(tmp_path) -> None:
    primary = tmp_path / "selected-project"
    platform = tmp_path / "agent-workspace"
    rail = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
        workspace_root=primary,
        platform_trusted_root=platform,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.workspace_root == primary.resolve()
    assert rail.platform_trusted_root == platform.resolve()
    assert rail.base_rail._host.resolve_workspace_dir() == primary
    assert rail.base_rail._engine._workspace_root == primary
    assert rail.base_rail._engine.trusted_dirs == [platform.resolve()]


def test_auto_builder_exposes_only_installed_host_snapshot(tmp_path) -> None:
    rail = build_permission_rail(
        {
            "permissions": {
                "enabled": True,
                "mode": "auto",
                "tools": {"read_file": "allow"},
            }
        },
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )

    assert isinstance(rail, AutoPermissionInterruptRail)
    assert rail.base_rail._host.get_permissions_snapshot() == rail.permission_config
    assert (
        rail.policy_evaluator._effective_permission_config() == rail.permission_config
    )


@pytest.mark.asyncio
async def test_auto_scene_hook_uses_installed_owner_scopes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    installed = {
        "enabled": True,
        "mode": "auto",
        "owner_scopes": {"web": {"principal-a": {"tools": {"read_file": "allow"}}}},
    }
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {
            "permissions": {
                **installed,
                "owner_scopes": {
                    "web": {"principal-a": {"tools": {"read_file": "deny"}}}
                },
            }
        },
    )
    rail = build_permission_rail(
        {"permissions": installed},
        enable_auto_permission=True,
        workspace_root=tmp_path,
    )
    assert isinstance(rail, AutoPermissionInterruptRail)
    hook = rail.base_rail._host.permission_scene_hook
    assert hook is not None
    token = TOOL_PERMISSION_CONTEXT.set(
        PermissionContext(channel_id="web", principal_user_id="principal-a")
    )
    try:
        result = await hook(
            SimpleNamespace(
                normalized_tool_name="read_file",
                tool_args={},
                user_input=None,
            )
        )
    finally:
        TOOL_PERMISSION_CONTEXT.reset(token)

    assert result == ("approve",)


def test_manual_builder_ignores_uninstalled_disk_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = {"enabled": True, "mode": "manual", "tools": {"bash": "deny"}}
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"permissions": latest},
    )

    installed = {"enabled": True, "mode": "manual", "tools": {"bash": "ask"}}
    rail = build_permission_rail({"permissions": installed})

    assert rail._host.get_permissions_snapshot() == installed
    assert rail._host.get_permissions_snapshot() is not installed


@pytest.mark.asyncio
async def test_group_avatar_uses_installed_permission_epoch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    installed = {
        "enabled": True,
        "mode": "manual",
        "tools": {"read_file": "allow"},
    }
    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {
            "permissions": {
                **installed,
                "tools": {"read_file": "deny"},
            }
        },
    )
    rail = build_permission_rail(
        {"permissions": installed},
        workspace_root=tmp_path,
    )
    hook = rail._host.permission_scene_hook
    assert hook is not None
    token = TOOL_PERMISSION_CONTEXT.set(
        PermissionContext(
            channel_id="web",
            principal_user_id="principal-a",
            group_digital_avatar=True,
        )
    )
    try:
        result = await hook(
            SimpleNamespace(
                normalized_tool_name="read_file",
                tool_args={"path": "README.md"},
                user_input=None,
            )
        )
    finally:
        TOOL_PERMISSION_CONTEXT.reset(token)

    assert result == ("approve",)
