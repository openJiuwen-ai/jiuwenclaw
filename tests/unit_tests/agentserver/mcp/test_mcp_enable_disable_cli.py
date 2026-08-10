# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests: enable/disable writes state.json enabled (single source of truth)
and additionally toggles bundled skills for cli / skill-only MCPs.

state.json is the single source of truth for MCP enabled state (config.yaml
is not touched by the MCP path; command.mcp's config.yaml CRUD is a separate
TUI path). For cli / skill-only MCPs, enable/disable also flips each
bundled skill's enabled flag so the agent stops seeing the MCP's skills
when disabled.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from jiuwenswarm.server.runtime.mcp import registry


def _mk_cli_pkg(workspace: Path, name: str, skills: list[str]) -> Path:
    """Build a CLI MCP package (cli.json + skills/<name>/SKILL.md)."""
    import json
    pkg = workspace / "mcp" / "mcp_builtins" / name
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "cli.json").write_text(json.dumps({
        "runtime": {"type": "node", "version": ">=18"},
        "init": {"win32": f"npm install -g {name}-cli"},
        "versionCheck": {"command": {"win32": f"{name}-cli --version"}, "minVersion": "1.0.0"},
    }), encoding="utf-8")
    sdir = pkg / "skills"
    sdir.mkdir(parents=True, exist_ok=True)
    for s in skills:
        sp = sdir / s
        sp.mkdir(parents=True, exist_ok=True)
        (sp / "SKILL.md").write_text(f"---\nname: {s}\ndescription: x\n---\nbody", encoding="utf-8")
    return pkg


def _state_enabled_result(name: str, enabled: bool) -> dict:
    """The dict set_connector_enabled returns for a successful flip."""
    return {"name": name, "enabled": enabled}


def test_disable_cli_writes_state_and_toggles_skills_off(tmp_path: Path) -> None:
    """disable on a CLI MCP writes state.json enabled=False AND flips
    each bundled skill enabled=False."""
    _mk_cli_pkg(tmp_path, "feishu", ["lark-doc", "lark-im"])
    enabled_calls: list[tuple[str, bool]] = []
    mgr = MagicMock()
    mgr.set_skill_enabled = lambda name, en: enabled_calls.append((name, en))

    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.skill.skill_manager.SkillManager", return_value=mgr), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.set_mcp_enabled",
               return_value=_state_enabled_result("feishu", False)) as state_set:
        result = registry.disable_mcp("feishu")

    # state.json is the single source — set_mcp_enabled called.
    state_set.assert_called_once_with("feishu", False)
    # skills also toggled off (cli/skill-only extra step).
    assert ("lark-doc", False) in enabled_calls
    assert ("lark-im", False) in enabled_calls
    assert result["name"] == "feishu"


def test_enable_cli_writes_state_and_toggles_skills_on(tmp_path: Path) -> None:
    """enable on a CLI MCP writes state.json enabled=True AND flips
    each bundled skill enabled=True."""
    _mk_cli_pkg(tmp_path, "feishu", ["lark-doc", "lark-im"])
    enabled_calls: list[tuple[str, bool]] = []
    mgr = MagicMock()
    mgr.set_skill_enabled = lambda name, en: enabled_calls.append((name, en))

    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.skill.skill_manager.SkillManager", return_value=mgr), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.set_mcp_enabled",
               return_value=_state_enabled_result("feishu", True)) as state_set:
        result = registry.enable_mcp("feishu")

    state_set.assert_called_once_with("feishu", True)
    assert ("lark-doc", True) in enabled_calls
    assert ("lark-im", True) in enabled_calls
    assert result["name"] == "feishu"


def test_enable_remote_mcp_writes_state(tmp_path: Path) -> None:
    """enable on a remote-mcp MCP flips state.json enabled (single source).
    No skill toggle (remote-mcp has no bundled skills path here)."""
    import json
    pkg = tmp_path / "mcp" / "mcp_builtins" / "notion"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "mcp.json").write_text(json.dumps({"mcpServers": {"notion": {"url": "https://mcp.notion.com/mcp"}}}), encoding="utf-8")
    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.set_mcp_enabled",
               return_value=_state_enabled_result("notion", True)) as state_set, \
         patch("jiuwenswarm.server.runtime.skill.skill_manager.SkillManager") as SM:
        result = registry.enable_mcp("notion")
    state_set.assert_called_once_with("notion", True)
    SM.return_value.set_skill_enabled.assert_not_called()
    assert result["name"] == "notion"


def test_disable_cli_no_skills_writes_state(tmp_path: Path) -> None:
    """disable on a CLI MCP with no bundled skills still writes state.json
    enabled (skill toggle list is empty, no crash)."""
    _mk_cli_pkg(tmp_path, "bare-cli", [])
    mgr = MagicMock()
    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.skill.skill_manager.SkillManager", return_value=mgr), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.set_mcp_enabled",
               return_value=_state_enabled_result("bare-cli", False)) as state_set:
        result = registry.disable_mcp("bare-cli")
    state_set.assert_called_once_with("bare-cli", False)
    mgr.set_skill_enabled.assert_not_called()
    assert result["name"] == "bare-cli"


def test_enable_unconnected_connector_raises_keyerror(tmp_path: Path) -> None:
    """enable on an MCP NOT in state.json raises KeyError (MCP_NOT_FOUND)
    — state.json is authoritative, no config.yaml fallback."""
    _mk_cli_pkg(tmp_path, "ghost-cli", ["s1"])
    with patch("jiuwenswarm.server.runtime.mcp.registry.get_workspace_dir", return_value=tmp_path), \
         patch("jiuwenswarm.server.runtime.mcp.state_store.set_mcp_enabled",
               side_effect=KeyError("mcp 'ghost-cli' not found in state")):
        with pytest.raises(KeyError):
            registry.enable_mcp("ghost-cli")
