# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""permissions_persist 写入 ``file_guard.paths``（对齐 agent-core §5.5.6）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


def _load_permissions_persist():
    """直接加载模块，避免 rails/__init__ 拉起 team 依赖。"""
    root = Path(__file__).resolve().parents[4]  # jiuwenswarm package root's parent
    # tests/unit_tests/agents/harness -> 4 up = repo root? 
    # __file__ = .../tests/unit_tests/agents/harness/test_....py
    # parents[0]=harness, [1]=agents, [2]=unit_tests, [3]=tests, [4]=repo
    mod_path = (
        Path(__file__).resolve().parents[4]
        / "jiuwenswarm"
        / "agents"
        / "harness"
        / "common"
        / "rails"
        / "permissions"
        / "permissions_persist.py"
    )
    spec = importlib.util.spec_from_file_location(
        "permissions_persist_under_test", mod_path
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def config_yaml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "permissions:\n  enabled: true\n  external_directory:\n    '*': ask\n",
        encoding="utf-8",
    )
    from jiuwenswarm.common import config as cfg_mod

    monkeypatch.setattr(cfg_mod, "_CONFIG_YAML_PATH", cfg)
    if hasattr(cfg_mod, "CONFIG_YAML_PATH"):
        monkeypatch.setattr(cfg_mod, "CONFIG_YAML_PATH", cfg)
    return cfg


def test_persist_cli_trusted_directory_writes_file_guard(config_yaml, tmp_path):
    pp = _load_permissions_persist()
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    result = pp.persist_cli_trusted_directory(str(trusted))
    assert result.get("ok") is True

    from jiuwenswarm.common.config import _load_yaml_round_trip

    data = _load_yaml_round_trip(config_yaml)
    perms = data["permissions"]
    fg = perms["file_guard"]
    assert fg["enabled"] is True
    dir_norm = trusted.resolve().as_posix().rstrip("/")
    assert any(
        isinstance(p, dict)
        and str(p.get("path", "")).replace("\\", "/").rstrip("/") == dir_norm
        and p.get("read") == "allow"
        and p.get("write") == "allow"
        and p.get("exec") == "ask"
        for p in fg["paths"]
    )
    ext = perms.get("external_directory") or {}
    assert ext.get(dir_norm) != "allow"
    assert ext.get(str(trusted.resolve())) != "allow"


def test_persist_cli_trusted_directory_with_overrides_keeps_shell_only(config_yaml, tmp_path):
    pp = _load_permissions_persist()
    from jiuwenswarm.common.config import _load_yaml_round_trip

    trusted = tmp_path / "trusted2"
    trusted.mkdir()
    result = pp.persist_cli_trusted_directory_with_overrides(str(trusted))
    assert result.get("ok") is True

    data = _load_yaml_round_trip(config_yaml)
    perms = data["permissions"]
    overrides = perms.get("approval_overrides") or []
    assert not any(isinstance(o, dict) and o.get("match_type") == "path" for o in overrides)
    assert any(isinstance(o, dict) and o.get("match_type") == "command" for o in overrides)


def test_persist_external_directory_allow_writes_file_guard(config_yaml, tmp_path):
    pp = _load_permissions_persist()
    from jiuwenswarm.common.config import _load_yaml_round_trip

    dir_path = tmp_path / "outside" / "projects"
    dir_path.mkdir(parents=True)
    pp.persist_external_directory_allow([str(dir_path)])
    data = _load_yaml_round_trip(config_yaml)
    fg = data["permissions"]["file_guard"]
    assert isinstance(fg.get("paths"), list) and fg["paths"]
    dir_norm = str(dir_path).replace("\\", "/").rstrip("/")
    parent_norm = str(dir_path.parent).replace("\\", "/").rstrip("/")
    assert any(
        isinstance(p, dict)
        and str(p.get("path", "")).replace("\\", "/").rstrip("/") == dir_norm
        and p.get("read") == "allow"
        and p.get("write") == "ask"
        and p.get("exec") == "ask"
        for p in fg["paths"]
    )
    assert not any(
        isinstance(p, dict)
        and str(p.get("path", "")).replace("\\", "/").rstrip("/") == parent_norm
        for p in fg["paths"]
    )


def test_exact_persist_merges_tool_delta_into_latest_config(config_yaml):
    pp = _load_permissions_persist()
    config_yaml.write_text(
        """permissions:
  enabled: true
  defaults:
    '*': ask
  tools:
    bash: ask
    protected_tool: deny
  deny_guidance_message: keep-me
""",
        encoding="utf-8",
    )

    assert pp.persist_exact_permission_allow_rule(
        "bash",
        {"command": "git status"},
    )

    from jiuwenswarm.common.config import _load_yaml_round_trip

    permissions = _load_yaml_round_trip(config_yaml)["permissions"]
    assert permissions["tools"]["protected_tool"] == "deny"
    assert "removed_tool" not in permissions["tools"]
    assert permissions["deny_guidance_message"] == "keep-me"
    assert permissions.get("approval_overrides")


def test_exact_persist_is_idempotent_when_delta_already_present(config_yaml):
    pp = _load_permissions_persist()
    config_yaml.write_text(
        """permissions:
  enabled: true
  defaults:
    '*': ask
  tools:
    bash: ask
""",
        encoding="utf-8",
    )
    args = {"command": "git status"}

    assert pp.persist_exact_permission_allow_rule("bash", args)
    first = config_yaml.read_text(encoding="utf-8")
    assert pp.persist_exact_permission_allow_rule("bash", args)
    assert config_yaml.read_text(encoding="utf-8") == first


def test_exact_persist_preserves_latest_tool_deny(config_yaml):
    pp = _load_permissions_persist()
    config_yaml.write_text(
        """permissions:
  enabled: true
  defaults:
    '*': ask
  tools:
    bash: deny
""",
        encoding="utf-8",
    )
    before = config_yaml.read_text(encoding="utf-8")

    assert not pp.persist_exact_permission_allow_rule(
        "bash",
        {"command": "git status"},
    )
    assert config_yaml.read_text(encoding="utf-8") == before


def test_exact_persist_updates_only_approved_file_axis(
    config_yaml,
    tmp_path,
    monkeypatch,
):
    pp = _load_permissions_persist()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    approved = tmp_path / "approved.txt"
    denied = tmp_path / "denied.txt"
    config_yaml.write_text(
        f"""permissions:
  enabled: true
  defaults:
    '*': allow
  tools:
    write_file: allow
  file_guard:
    enabled: true
    defaults:
      read: ask
      write: ask
      exec: ask
    workspace:
      read: allow
      write: allow
      exec: ask
    paths:
      - path: {denied.as_posix()}
        read: deny
        write: deny
        exec: deny
        match: prefix
""",
        encoding="utf-8",
    )
    from jiuwenswarm.common import utils as utils_module
    from jiuwenswarm.common.config import _load_yaml_round_trip

    monkeypatch.setattr(utils_module, "get_workspace_dir", lambda: workspace)
    args = {"path": approved.as_posix(), "content": "ok"}

    assert pp.persist_exact_permission_allow_rule(
        "write_file",
        args,
        ((approved.as_posix(), "write"),),
    )

    paths = _load_yaml_round_trip(config_yaml)["permissions"]["file_guard"]["paths"]
    by_path = {str(entry["path"]): entry for entry in paths}
    assert by_path[approved.as_posix()]["read"] == "allow"
    assert by_path[approved.as_posix()]["write"] == "allow"
    assert by_path[approved.as_posix()]["exec"] == "ask"
    assert by_path[denied.as_posix()]["read"] == "deny"
    assert by_path[denied.as_posix()]["write"] == "deny"


def test_exact_persist_rejects_malformed_accesses(config_yaml):
    pp = _load_permissions_persist()

    assert not pp.persist_exact_permission_allow_rule(
        "write_file",
        {"path": "/tmp/a"},
        (("/tmp/a", "delete"),),
    )
