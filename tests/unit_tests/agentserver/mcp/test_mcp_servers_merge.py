# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests: get_mcp_servers merges config.yaml + mcp/state.json.

MCP connection/enabled state lives in state.json; get_mcp_servers remains
the single read entry for the adapter / handlers / registry. It merges both
sources (hand-written config.yaml MCPs + state.json MCPs with state==connected).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import jiuwenswarm.common.config as cfg_mod
from jiuwenswarm.common import utils as common_utils
from jiuwenswarm.server.runtime.mcp import state_store as ss


def _write_config_yaml(tmp_path: Path, servers: list) -> None:
    (tmp_path / "config.yaml").write_text(
        json.dumps({"mcp": {"servers": servers}}) if servers else "{}",
        encoding="utf-8",
    )


def _write_state_json(tmp_path: Path, connectors: dict) -> None:
    d = tmp_path / "mcp"
    d.mkdir(exist_ok=True)
    (d / "state.json").write_text(
        json.dumps({"version": 1, "mcp": connectors, "mounts": {}}),
        encoding="utf-8",
    )


def test_get_mcp_servers_merges_config_yaml_and_state(tmp_path: Path) -> None:
    _write_config_yaml(tmp_path, [
        {"name": "manual-mcp", "transport": "stdio", "command": "npx",
         "args": ["-y", "x"], "enabled": True}
    ])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "url": "https://x/sse",
                  "headers": {"Authorization": "Bearer ${T}"},
                  "state": "connected", "enabled": True,
                  "server_id_scope": "mcp:baidu"}
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    names = {s["name"] for s in servers}
    assert names == {"manual-mcp", "baidu"}


def test_state_disconnected_excluded(tmp_path: Path) -> None:
    """Only state==connected connectors surface; disconnected ones don't."""
    _write_config_yaml(tmp_path, [])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "state": "connected", "enabled": True},
        "github": {"transport": "http", "state": "disconnected", "enabled": True}
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    names = {s["name"] for s in servers}
    assert names == {"baidu"}


def test_skill_only_connector_excluded_from_mcp_servers(tmp_path: Path) -> None:
    """Skill-only connectors (no transport/url/command — only skills) do NOT
    appear in get_mcp_servers. They have no MCP server to register; surfacing
    via the mcp.servers list would make the adapter try to build a fake
    streamable-http entry with no url and log spurious "invalid entry"
    warnings. Skill-only connectors surface via SkillManager instead."""
    _write_config_yaml(tmp_path, [])
    _write_state_json(tmp_path, {
        "ctrip-wendao": {
            "state": "connected", "enabled": True,
            "integration_type": "skill-only",
            "server_id_scope": "mcp:ctrip-wendao",
            "skills": ["ctrip-wendao"],
        },
        "baidu": {"transport": "sse", "url": "https://x/sse",
                  "state": "connected", "enabled": True,
                  "server_id_scope": "mcp:baidu"},
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    names = {s["name"] for s in servers}
    # ctrip-wendao (skill-only) excluded; baidu (sse) included.
    assert names == {"baidu"}


def test_state_disabled_not_enabled_field(tmp_path: Path) -> None:
    """A connected-but-disabled MCP surfaces but with enabled=False,
    so extract_enabled_mcp_server_entries skips it."""
    _write_config_yaml(tmp_path, [])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "state": "connected", "enabled": False,
                  "url": "https://x"}
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    baidu = next(s for s in servers if s["name"] == "baidu")
    assert baidu["enabled"] is False


def test_state_overrides_config_yaml_on_name_conflict(tmp_path: Path) -> None:
    """During migration overlap, an MCP may exist in both config.yaml
    (stale) and state.json (authoritative). state.json wins on conflict."""
    _write_config_yaml(tmp_path, [
        {"name": "baidu", "transport": "http", "url": "https://stale",
         "enabled": True}
    ])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "url": "https://fresh/sse",
                  "state": "connected", "enabled": True}
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    baidu = [s for s in servers if s["name"] == "baidu"]
    assert len(baidu) == 1  # deduped, not duplicated
    assert baidu[0]["transport"] == "sse"  # state.json value wins
    assert baidu[0]["url"] == "https://fresh/sse"


def test_get_mcp_server_config_finds_in_state(tmp_path: Path) -> None:
    _write_config_yaml(tmp_path, [])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "url": "https://x", "state": "connected", "enabled": True}
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        item = cfg_mod.get_mcp_server_config("baidu")
    assert item is not None
    assert item["name"] == "baidu"
    assert cfg_mod.get_mcp_server_config("nope") is None


def test_no_state_file_returns_config_yaml_only(tmp_path: Path) -> None:
    _write_config_yaml(tmp_path, [
        {"name": "manual", "transport": "stdio", "command": "x", "enabled": True}
    ])
    # no state.json
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        servers = cfg_mod.get_mcp_servers()
    assert [s["name"] for s in servers] == ["manual"]


def test_disabled_connector_still_connected_in_list(tmp_path: Path) -> None:
    """A disabled MCP (state=connected, enabled=false) stays "connected"
    in _connected_server_names — disable is a soft switch that keeps the
    connection; only disconnect removes it. Regression: switching tabs re-fetched
    mcp.list, which used to report disabled MCPs as disconnected,
    so the user couldn't tell "disable then enable" from "disconnect then connect"
    and re-connect was the only way back."""
    from jiuwenswarm.server.runtime.mcp.registry import _connected_server_names
    _write_config_yaml(tmp_path, [])
    _write_state_json(tmp_path, {
        "baidu": {"transport": "sse", "url": "https://x", "state": "connected",
                  "enabled": False, "server_id_scope": "mcp:baidu"},
    })
    with patch.object(cfg_mod, "CONFIG_YAML_PATH", tmp_path / "config.yaml"), \
         patch.object(common_utils, "get_workspace_dir", return_value=tmp_path), \
         patch.object(ss, "get_workspace_dir", return_value=tmp_path):
        names = _connected_server_names()
    assert "baidu" in names

