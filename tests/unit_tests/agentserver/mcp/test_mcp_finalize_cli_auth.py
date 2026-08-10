# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``_finalize_cli_auth`` post-auth side-effects (form C).

After CLI OAuth completes, ``_await_cli_auth`` calls ``_finalize_cli_auth``
which runs ``apply_mcp_change(add)`` (the real MCP server register for hybrid
CLI+MCP) and then promotes state connecting→connected. This pins the state
contract:

* apply succeeds → state promoted connecting→connected, returns ``connected``.
* apply raises → state rolled back (marketplace: remove record + uninstall
  skills; custom: flip to registered), returns ``auth_failed`` (NOT a phantom
  ``connected``). This is the fix for "CLI connect failed but showed
  connected".
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _new_server():
    """A bare AgentWebSocketServer with the agent_manager dep mocked."""
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    server._agent_manager = MagicMock()
    server._agent_manager.apply_mcp_change = AsyncMock()
    server._agent_manager.sync_mcp_credentials = MagicMock(return_value=True)
    server._agent_manager.refresh_skill_rails = AsyncMock()
    server._mask_sensitive_fields = lambda d: d
    return server


@pytest.mark.asyncio
async def test_finalize_cli_auth_apply_ok_promotes_to_connected() -> None:
    """apply_mcp_change(add) succeeds → state promoted connecting→connected,
    returns the ``connected`` payload."""
    server = _new_server()
    server._agent_manager.apply_mcp_change = AsyncMock(return_value=True)

    set_state_calls: list[tuple[str, str]] = []

    def fake_set_state(name, *, state):
        set_state_calls.append((name, state))

    with patch(
        "jiuwenswarm.server.runtime.mcp.state_store.set_mcp_state",
        side_effect=fake_set_state,
    ):
        result = await server._finalize_cli_auth("cloudbase", {
            "name": "cloudbase", "integration_type": "cli",
            "auth_required": False, "installed_skills": ["cloudbase"],
        })

    assert result["type"] == "connected"
    assert result["name"] == "cloudbase"
    # promoted connecting → connected
    assert ("cloudbase", "connected") in set_state_calls


@pytest.mark.asyncio
async def test_finalize_cli_auth_apply_fails_rolls_back_and_returns_auth_failed(tmp_path) -> None:
    """apply_mcp_change(add) raises → roll back state.json (marketplace:
    uninstall skills + remove record) and return ``auth_failed``, NOT a
    phantom ``connected``. The rollback prevents a failed CLI connect from
    leaving a connecting record that would re-trigger on every restart."""
    server = _new_server()
    server._agent_manager.apply_mcp_change = AsyncMock(
        side_effect=RuntimeError("register rejected"),
    )

    removed: list[str] = []
    uninstalled: list[str] = []
    set_state_calls: list[tuple[str, str]] = []

    def fake_set_state(name, *, state):
        set_state_calls.append((name, state))

    with patch(
        "jiuwenswarm.server.runtime.mcp.registry._packages_dir",
        return_value=tmp_path,
    ), patch(
        "jiuwenswarm.server.runtime.mcp.state_store.set_mcp_state",
        side_effect=fake_set_state,
    ), patch(
        "jiuwenswarm.server.runtime.mcp.state_store.remove_mcp_record",
        side_effect=lambda n: removed.append(n) or {},
    ), patch(
        "jiuwenswarm.server.runtime.mcp.skill_installer.uninstall_mcp_skills",
        side_effect=lambda n: uninstalled.append(n),
    ):
        # Make the package dir exist so the marketplace rollback branch runs.
        (tmp_path / "cloudbase").mkdir()
        result = await server._finalize_cli_auth("cloudbase", {
            "name": "cloudbase", "integration_type": "cli",
            "auth_required": False, "installed_skills": ["cloudbase"],
        })

    assert result["type"] == "auth_failed"
    assert result["name"] == "cloudbase"
    assert "register rejected" in str(result["error"])
    # marketplace rollback: uninstall skills + remove record (entry rebuildable).
    assert uninstalled == ["cloudbase"]
    assert removed == ["cloudbase"]
    # No phantom connected flip happened.
    assert ("cloudbase", "connected") not in set_state_calls


@pytest.mark.asyncio
async def test_finalize_cli_auth_apply_fails_custom_flips_to_registered(tmp_path) -> None:
    """apply fails for a custom CLI MCP (no package dir) → flip state back to
    registered (keep the user-edited definition for retry), return auth_failed."""
    server = _new_server()
    server._agent_manager.apply_mcp_change = AsyncMock(
        side_effect=RuntimeError("register rejected"),
    )

    set_state_calls: list[tuple[str, str]] = []

    def fake_set_state(name, *, state):
        set_state_calls.append((name, state))

    # _packages_dir / "mycli" does NOT exist → custom branch (flip to registered).
    with patch(
        "jiuwenswarm.server.runtime.mcp.registry._packages_dir",
        return_value=tmp_path,  # tmp_path / "mycli" not a dir
    ), patch(
        "jiuwenswarm.server.runtime.mcp.state_store.set_mcp_state",
        side_effect=fake_set_state,
    ):
        result = await server._finalize_cli_auth("mycli", {
            "name": "mycli", "integration_type": "cli",
            "auth_required": False, "installed_skills": [],
        })

    assert result["type"] == "auth_failed"
    # custom rollback: flip to registered (definition preserved for retry).
    assert ("mycli", "registered") in set_state_calls
