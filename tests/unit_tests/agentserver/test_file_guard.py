# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``file_guard`` (phase 1)."""
import os
from jiuwenclaw.agentserver.permissions.file_guard import (
    FileGuardChecker,
    merged_file_guard_config,
    report_legacy_path_rules_at_load,
)
from jiuwenclaw.agentserver.permissions.models import PermissionLevel


def test_merged_file_guard_migrates_external_directory_allow():
    permissions = {
        "external_directory": {
            "*": "ask",
            "/data/shared": "allow",
        },
        "file_guard": {
            "global": {},
            "workspace": {"rw_enabled": True},
        },
    }
    fg = merged_file_guard_config(permissions)
    entry = fg["global"]["/data/shared"]
    assert entry["read_enable"] is True
    assert entry["write_enable"] is True


def test_merged_file_guard_does_not_mutate_input():
    """merged_file_guard_config 须视入参为只读，禁止就地写回 file_guard.global。"""
    permissions = {
        "external_directory": {"/data/shared": "allow"},
        "file_guard": {"global": {}, "workspace": {"rw_enabled": True}},
    }
    original_global = permissions["file_guard"]["global"]
    snapshot = dict(original_global)

    merged = merged_file_guard_config(permissions)

    assert "/data/shared" in merged["global"]
    assert original_global == snapshot
    assert "/data/shared" not in original_global
    assert permissions["file_guard"]["global"] is original_global


def test_merged_file_guard_migrates_external_ask():
    permissions = {
        "external_directory": {"*": "ask", "/secret": "ask"},
        "file_guard": {"global": {}},
    }
    fg = merged_file_guard_config(permissions)
    assert fg["global"]["/secret"]["read_enable"] is False


def test_file_guard_inside_workspace_noop(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    f = ws / "a.txt"
    f.write_text("x", encoding="utf-8")
    checker = FileGuardChecker(
        {"file_guard": {"workspace": {"rw_enabled": True}, "global": {}}},
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(f)}) is None


def test_file_guard_outside_workspace_asks(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "b.txt"
    f.write_text("y", encoding="utf-8")
    checker = FileGuardChecker(
        {"file_guard": {"workspace": {"rw_enabled": True}, "global": {}}},
        workspace_root=ws,
    )
    res = checker.check_external_paths("read_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.ASK
    assert "file_guard" in (res.matched_rule or "")
    assert res.file_operations


def test_file_guard_global_read_allow(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "c.txt"
    f.write_text("z", encoding="utf-8")
    prefix = str(outside.resolve())
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {"read_enable": True, "write_enable": False}},
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(f)}) is None


def test_report_legacy_path_rules_flags_path_class_rules(caplog):
    """Phase-1：``rules[*]`` 残留 ``read_file + **/.ssh/**`` 这类 path 类条目，应被 ERROR 标出。"""
    permissions = {
        "rules": [
            {
                "id": "legacy_block_ssh",
                "tools": ["read_file", "Read"],
                "pattern": "**/.ssh/**",
                "action": "deny",
            },
            {
                "id": "shell_rule_keep",
                "tools": ["mcp_exec_command"],
                "pattern": "rm -rf *",
                "action": "deny",
            },
        ],
    }

    with caplog.at_level("ERROR", logger="jiuwenclaw.agentserver.permissions.file_guard"):
        flagged = report_legacy_path_rules_at_load(permissions)

    assert "legacy_block_ssh" in flagged
    assert "shell_rule_keep" not in flagged


def test_file_guard_trusted_exec(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "x.py"
    script.write_text("print(1)\n", encoding="utf-8")
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {},
                "trusted_exec_directory": [str(scripts.resolve())],
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths(
        "mcp_exec_command",
        {"command": f'python "{script}"', "workdir": str(ws)},
    ) is None


def test_persist_file_operations_allow_writes_to_live_path_not_frozen_constant(monkeypatch, tmp_path):
    """回归：persist_file_operations_allow（经 _yaml_update_permissions）用实时路径，不写冻结常量诱饵文件。

    独立持久化文件操作权限段（``permissions.file_guard.global``）。若该 writer 被误改回
    ``_CONFIG_YAML_PATH``，本测试应捕获。
    """
    import importlib
    import yaml as _yaml
    from jiuwenclaw.agentserver.permissions.file_guard import persist_file_operations_allow
    from jiuwenclaw.agentserver.permissions.models import FileOperation
    from jiuwenclaw.agentserver.permissions.core import PermissionEngine, set_permission_engine

    live_config = tmp_path / "config.yaml"
    live_config.write_text(
        _yaml.safe_dump({"permissions": {"enabled": True, "rules": []}}, allow_unicode=True),
        encoding="utf-8",
    )
    frozen_config = tmp_path / "frozen_resources_config.yaml"
    frozen_config.write_text(
        _yaml.safe_dump({"permissions": {"enabled": True, "rules": []}}, allow_unicode=True),
        encoding="utf-8",
    )

    config_mod = importlib.import_module("jiuwenclaw.config")
    monkeypatch.setattr(config_mod, "_CONFIG_YAML_PATH", frozen_config)
    monkeypatch.setattr(config_mod, "_current_config_yaml_path", lambda: live_config)
    set_permission_engine(PermissionEngine({"enabled": True, "rules": []}))

    persist_file_operations_allow([
        FileOperation(action="write", path=str(tmp_path / "outside.txt"), source="tool_arg", prompt=""),
    ])

    live_saved = _yaml.safe_load(live_config.read_text(encoding="utf-8"))
    frozen_saved = _yaml.safe_load(frozen_config.read_text(encoding="utf-8"))
    live_global = live_saved["permissions"].get("file_guard", {}).get("global", {})
    assert live_global, "文件操作权限应写入实时配置的 file_guard.global"
    assert not frozen_saved["permissions"].get("file_guard", {}).get("global"), "诱饵文件不应被写入"


# ---------- _inject_system_default_trust ----------


def _patch_runtime_venv(monkeypatch, venv_dir):
    """Patch ``jiuwenclaw.runtime.get_runtime_venv_dir`` 返回固定路径。"""
    import importlib
    runtime_mod = importlib.import_module("jiuwenclaw.runtime")
    monkeypatch.setattr(runtime_mod, "get_runtime_venv_dir", lambda: venv_dir)


def test_system_default_trust_injects_isolation_venv(monkeypatch, tmp_path):
    """isolation_venv/Scripts（或 bin）在缺省配置时应被注入到 trusted_exec_directory。"""
    venv_dir = tmp_path / "isolation_venv"
    venv_dir.mkdir()
    _patch_runtime_venv(monkeypatch, venv_dir)

    fg = merged_file_guard_config({"file_guard": {"workspace": {"rw_enabled": True}}})
    ted = fg["trusted_exec_directory"]
    expected = (venv_dir / ("Scripts" if os.name == "nt" else "bin")).resolve().as_posix()
    assert expected in ted, f"期望注入 {expected}, 实际 {ted}"


def test_system_default_trust_skips_when_already_configured(monkeypatch, tmp_path):
    """用户已显式配置 isolation_venv 路径时，不重复注入。"""
    venv_dir = tmp_path / "isolation_venv"
    venv_dir.mkdir()
    _patch_runtime_venv(monkeypatch, venv_dir)
    expected = (venv_dir / ("Scripts" if os.name == "nt" else "bin")).resolve().as_posix()

    fg = merged_file_guard_config({
        "file_guard": {
            "workspace": {"rw_enabled": True},
            "trusted_exec_directory": [expected],
        },
    })
    ted = fg["trusted_exec_directory"]
    assert ted.count(expected) == 1, f"已配置路径不应重复注入, 实际 {ted}"


def test_system_default_trust_uses_exact_path_not_substring(monkeypatch, tmp_path):
    """回归：去重检查用精确 posix 路径比较，不能用 substring matching。

    用户配置了超集路径（如 isolation_venv/Scripts-backup），scripts_dir
    （isolation_venv/Scripts）不应被 substring 误判为已配置而跳过注入。
    """
    venv_dir = tmp_path / "isolation_venv"
    venv_dir.mkdir()
    _patch_runtime_venv(monkeypatch, venv_dir)
    expected = (venv_dir / ("Scripts" if os.name == "nt" else "bin")).resolve().as_posix()
    # 构造超集路径：expected + "-backup"（不是 expected 的父目录，是兄弟路径）
    superset = expected + "-backup"

    fg = merged_file_guard_config({
        "file_guard": {
            "workspace": {"rw_enabled": True},
            "trusted_exec_directory": [superset],
        },
    })
    ted = fg["trusted_exec_directory"]
    assert expected in ted, f"超集路径不应阻止注入, 实际 {ted}"
    assert superset in ted, "用户配置的超集路径应保留"


def test_system_default_trust_silent_on_runtime_error(monkeypatch):
    """get_runtime_venv_dir 抛异常时静默跳过，不影响其他配置。"""
    def _raise():
        raise RuntimeError("venv unavailable")
    import importlib
    runtime_mod = importlib.import_module("jiuwenclaw.runtime")
    monkeypatch.setattr(runtime_mod, "get_runtime_venv_dir", _raise)

    fg = merged_file_guard_config({
        "file_guard": {
            "workspace": {"rw_enabled": True},
            "trusted_exec_directory": ["/user/explicit/path"],
        },
    })
    # 异常时不注入，但已配置路径保留，workspace 等其他配置不受影响
    assert fg["trusted_exec_directory"] == ["/user/explicit/path"]
    assert fg["workspace"]["rw_enabled"] is True


def test_system_default_trust_exec_axis_only(monkeypatch, tmp_path):
    """isolation_venv 只信任 exec 轴，不扩散到 read/write（不写入 global）。"""
    venv_dir = tmp_path / "isolation_venv"
    venv_dir.mkdir()
    _patch_runtime_venv(monkeypatch, venv_dir)

    fg = merged_file_guard_config({"file_guard": {"workspace": {"rw_enabled": True}}})
    # exec 轴被注入
    assert fg["trusted_exec_directory"]
    # read/write 轴（global）不应因 isolation_venv 而新增条目
    assert not fg.get("global"), f"isolation_venv 不应扩散到 global, 实际 {fg.get('global')}"
