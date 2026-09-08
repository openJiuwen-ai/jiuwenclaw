# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Integration test for the permission rail hot-reload bug.

Bug scenario (from jiuwenswarm):
1. User starts in default mode (permissions.enabled=true, tools.bash=ask)
2. build_permission_rail captures inline_permissions in the snapshot closure
3. User toggles to full_access (disk: enabled=false, defaults.*=allow)
4. _update_permission_rail hot-updates engine._static_config to {enabled:false}
5. User sends a tool call in full_access — first_check calls
   host.get_permissions_snapshot() which used to return the STALE build-time
   inline_permissions ({enabled:true, bash:ask}), overwriting engine._static_config
   back to the wrong state. After the fix, it must return the CURRENT disk config.
6. User toggles back to default — same path must use current disk config.

The test below calls the real build_permission_rail and inspects the snapshot
callback on the returned host, then verifies the callback reflects the CURRENT
disk state (mocked via load_global_permissions), not the build-time capture.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from jiuwenswarm.agents.harness.common.rails.interrupt import interrupt_helpers
from jiuwenswarm.agents.harness.common.rails.permissions import (
    permissions_layers as layers,
)


def _build_rail_with_mocked_sdk(disk_global: dict, user_path: Path, sess_path: Path):
    """Build a permission rail with a mocked SDK and return (rail, host)."""
    with patch.object(layers, "user_permissions_path", lambda: user_path), \
         patch.object(layers, "session_permissions_path", lambda sid: sess_path), \
         patch.object(layers, "load_global_permissions", lambda: dict(disk_global)):
        mock_rail = MagicMock()
        mock_host = MagicMock()
        mock_rail._host = mock_host
        with patch.object(
            interrupt_helpers,
            "build_permission_interrupt_rail",
            return_value=mock_rail,
        ) as mock_build:
            config = {
                "permissions": {
                    "enabled": True,
                    "tools": {"bash": "ask", "write_file": "allow"},
                }
            }
            rail = interrupt_helpers.build_permission_rail(
                config=config,
                llm=None,
                model_name="test-model",
                session_id="s1",
            )
            assert rail is mock_rail
            # The build_permission_interrupt_rail call receives a ToolPermissionHost
            # as a kwarg. Extract the host to get the snapshot callback.
            call_kwargs = mock_build.call_args.kwargs
            host = call_kwargs["host"]
            snapshot = host.get_permissions_snapshot
            assert snapshot is not None
            return snapshot


def test_snapshot_uses_current_disk_config_not_stale_build_time_capture(
    tmp_path: Path,
) -> None:
    """The snapshot callback must read the CURRENT global permissions from
    disk on every invocation, not the build-time capture. Otherwise toggling
    full_access ↔ default leaks stale decisions."""
    user_path = tmp_path / "user_permissions.yaml"
    sess_path = tmp_path / "s1" / "session_permissions.yaml"
    user_path.parent.mkdir(parents=True, exist_ok=True)
    sess_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate live disk: changes between rail build and snapshot call
    disk_state = {
        "enabled": True,
        "tools": {"bash": "ask", "write_file": "allow"},
    }

    # Patch load_global_permissions AFTER building the rail so it reads the
    # *current* disk_state dict on every call. The dict is mutated below to
    # simulate the user toggling full_access → default.
    disk_ref = disk_state

    # build_permission_interrupt_rail is imported locally inside the function
    # from openjiuwen.harness.security — patch at the source module.
    from openjiuwen.harness import security as security_mod

    with patch.object(layers, "user_permissions_path", lambda: user_path), \
         patch.object(layers, "session_permissions_path", lambda sid: sess_path), \
         patch.object(layers, "load_global_permissions", lambda: dict(disk_ref)), \
         patch.object(security_mod, "build_permission_interrupt_rail") as mock_build:
        mock_rail = MagicMock()
        mock_build.return_value = mock_rail
        config_at_build = {
            "permissions": {
                "enabled": True,
                "tools": {"bash": "ask", "write_file": "allow"},
            }
        }
        rail = interrupt_helpers.build_permission_rail(
            config=config_at_build,
            llm=None,
            model_name="test-model",
            session_id="s1",
        )
        assert rail is mock_rail
        host = mock_build.call_args.kwargs["host"]
        snapshot = host.get_permissions_snapshot

        # Step 1: snapshot at build time — bash=ask
        snap1 = snapshot("s1")
        assert snap1.get("enabled") is True, (
            f"expected enabled=True at build time, got {snap1}"
        )
        assert "bash" in (snap1.get("ask_tools") or []), (
            f"expected bash in ask_tools, got {snap1}"
        )

        # Step 2: user toggles to full_access — disk config changes
        disk_ref.clear()
        disk_ref.update({"enabled": False, "defaults": {"*": "allow"}})

        # Step 3: snapshot after toggle — must reflect the NEW disk state
        snap2 = snapshot("s1")
        assert snap2.get("enabled") is False, (
            f"BUG: snapshot still uses stale build-time config "
            f"(expected enabled=False after full_access toggle), got {snap2}"
        )
        # In full_access mode, bash should NOT be in ask_tools
        assert "bash" not in (snap2.get("ask_tools") or []), (
            f"BUG: bash still in ask_tools after full_access toggle, got {snap2}"
        )

        # Step 4: user toggles back to default — disk config changes again
        disk_ref.clear()
        disk_ref.update({"enabled": True, "tools": {"bash": "ask"}})

        # Step 5: snapshot after toggle back — must reflect the new state
        snap3 = snapshot("s1")
        assert snap3.get("enabled") is True, (
            f"BUG: snapshot did not reload disk after toggle back to default, "
            f"got {snap3}"
        )
        assert "bash" in (snap3.get("ask_tools") or []), (
            f"BUG: bash not in ask_tools after toggling back to default, "
            f"got {snap3}"
        )


def test_snapshot_disabled_rail_returns_none() -> None:
    """If permissions.enabled is false at build time, build_permission_rail
    returns None — this is the existing behavior and must be preserved."""
    from openjiuwen.harness import security as security_mod
    with patch.object(security_mod, "build_permission_interrupt_rail") as mock_build:
        config = {"permissions": {"enabled": False, "defaults": {"*": "allow"}}}
        rail = interrupt_helpers.build_permission_rail(
            config=config,
            llm=None,
            model_name="test-model",
            session_id="s1",
        )
        assert rail is None
        mock_build.assert_not_called()
