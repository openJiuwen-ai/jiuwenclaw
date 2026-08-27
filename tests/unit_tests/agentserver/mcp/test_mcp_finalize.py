# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for ``_finalize_cli``: CLI MCP stdio-entry registration policy.

Pure CLI connectors (feishu/dingtalk/...) have no ``mcp`` subcommand, so
``_finalize_cli`` must NOT register a stdio MCP entry for them — their
tools surface via bundled skills + the CLI binary. Hybrid packages
(cloudbase: cli.json for auth + mcp.json with ``command``) DO register a
stdio entry from that mcp.json's command field.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

# --- _finalize_cli end-to-end: pure CLI does NOT register a stdio MCP entry ---

def test_finalize_cli_no_mcp_entry_for_pure_cli(tmp_path: Path) -> None:
    """Pure CLI connectors (feishu: no mcp.json, no `mcp` subcommand) must NOT
    register a stdio MCP entry — the CLI exposes business subcommands
    (docs/im/...) that a SKILL.md teaches the agent to call via exec, not MCP.
    Registering a bogus ``lark-cli mcp`` stdio entry made stdio connect fail.

    Pure CLI still writes a state.json record (carrying skills, no MCP entry)
    so disconnect/enable/disable can find it — that's expected, not an MCP reg.
    """
    mp = tmp_path / "mcp" / "mcp_builtins" / "feishu"
    mp.mkdir(parents=True)
    (mp / "cli.json").write_text(
        '{"runtime":{"type":"node","version":">=18"},'
        '"init":{"win32":"npm install -g @larksuite/cli"},'
        '"versionCheck":{"command":{"win32":"lark-cli.cmd --version"},"minVersion":"1.0.77"},'
        '"auth":[{"command":{"win32":"lark-cli.cmd auth login --recommend"},'
        '"authWaitForExit":true,"authUrlDomain":"accounts.feishu.cn"}],'
        '"unAuth":{"win32":"lark-cli.cmd auth logout"},'
        '"status":{"win32":"lark-cli.cmd auth status"},'
        '"statusMatchJson":{"identity":"user"}}',
        encoding="utf-8",
    )
    # NOTE: no mcp.json — pure CLI.

    upserts: list[dict] = []
    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.cli_driver.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.skill_installer.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=lambda n, e, **kw: upserts.append({"name": n, **e})), \
         patch("jiuwenswarm.server.runtime.mcp.skill_installer.install_mcp_skills", return_value={"installed": ["lark-doc"]}):
        from jiuwenswarm.server.runtime.mcp.cli_driver import InstallResult
        from jiuwenswarm.server.runtime.mcp.registry import _finalize_cli

        inst = InstallResult(name="feishu", installed=True, version="1.0.80", min_version="1.0.77", version_ok=True)
        result = _finalize_cli("feishu", inst)

    # A state.json record IS written (carries skills, no MCP entry) — that's
    # the pure-CLI state record, not an MCP registration.
    assert len(upserts) == 1
    assert "command" not in upserts[0]  # no stdio command — not an MCP entry
    assert "server_id_scope" in upserts[0]
    # No stdio MCP entry registered (lark-cli has no `mcp` subcommand).
    assert result["mcp_entry"] is None
    assert result["installed_skills"] == ["lark-doc"]


def test_finalize_cli_registers_mcp_for_hybrid_package(tmp_path: Path) -> None:
    """Hybrid packages (cloudbase: cli.json for auth + mcp.json with command)
    DO register a stdio MCP entry from mcp.json's command field, written to
    state.json."""
    mp = tmp_path / "mcp" / "mcp_builtins" / "cloudbase"
    mp.mkdir(parents=True)
    (mp / "cli.json").write_text(
        '{"runtime":{"type":"node","version":">=18"},'
        '"init":{"win32":"npm install -g @cloudbase/cli@latest"},'
        '"versionCheck":{"command":{"win32":"tcb.cmd --version"},"minVersion":"3.6.4"}}',
        encoding="utf-8",
    )
    (mp / "mcp.json").write_text(
        '{"mcpServers":{"cloudbase":{"command":"npx","args":["-y","@cloudbase/cloudbase-mcp@latest"]}}}',
        encoding="utf-8",
    )

    upserts: list[dict] = []
    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.skill_installer.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=lambda n, e, **kw: upserts.append({"name": n, **e})), \
         patch("jiuwenswarm.server.runtime.mcp.skill_installer.install_mcp_skills", return_value={"installed": []}):
        from jiuwenswarm.server.runtime.mcp.cli_driver import InstallResult
        from jiuwenswarm.server.runtime.mcp.registry import _finalize_cli

        inst = InstallResult(name="cloudbase", installed=True, version="3.6.4", min_version="3.6.4", version_ok=True)
        result = _finalize_cli("cloudbase", inst)

    # mcp.json declares command=npx -> stdio entry written to state.json.
    assert len(upserts) == 1
    assert upserts[0]["command"] == "npx"
    assert result["mcp_entry"] is not None
