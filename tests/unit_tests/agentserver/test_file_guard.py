# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for ``file_guard`` (phase 1)."""

from jiuwenclaw.agentserver.permissions.file_guard import (
    FileGuardChecker,
    apply_cli_trusted_to_permissions_dict,
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
    assert entry["read"] == "allow"
    assert entry["write"] == "allow"


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
    assert fg["global"]["/secret"]["read"] == "ask"
    assert fg["global"]["/secret"]["write"] == "ask"


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
                "global": {prefix: {"read": "allow", "write": "ask"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(f)}) is None


def test_file_guard_global_legacy_bool_compat(tmp_path):
    """旧 read_enable/write_enable 仍可读：true→allow，false→ask。"""
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
    res = checker.check_external_paths("write_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.ASK


def test_file_guard_global_deny_outside(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "d.txt"
    f.write_text("z", encoding="utf-8")
    prefix = str(outside.resolve())
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {"read": "deny", "write": "deny"}},
            },
        },
        workspace_root=ws,
    )
    res = checker.check_external_paths("read_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.DENY
    assert res.matched_rule == "file_guard:deny"
    assert res.file_operations is None


def test_file_guard_global_deny_pierces_workspace(tmp_path):
    """global deny 可穿透 workspace（需求 B）。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    secret = ws / "secret"
    secret.mkdir()
    f = secret / "x.txt"
    f.write_text("s", encoding="utf-8")
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {str(secret.resolve()): {"read": "deny", "write": "ask"}},
            },
        },
        workspace_root=ws,
    )
    res = checker.check_external_paths("read_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.DENY


def test_file_guard_global_ask_does_not_override_workspace(tmp_path):
    """global ask/allow 不覆盖 workspace 内放行。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    sub = ws / "sub"
    sub.mkdir()
    f = sub / "x.txt"
    f.write_text("s", encoding="utf-8")
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {str(sub.resolve()): {"read": "ask", "write": "ask"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(f)}) is None


def test_file_guard_missing_keys_default_ask(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "e.txt"
    f.write_text("z", encoding="utf-8")
    prefix = str(outside.resolve())
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {}},
            },
        },
        workspace_root=ws,
    )
    res = checker.check_external_paths("read_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.ASK


def test_file_guard_global_relative_path(tmp_path):
    """global 相对路径相对 workspace resolve。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    shared = ws / "shared"
    shared.mkdir()
    f = shared / "a.txt"
    f.write_text("z", encoding="utf-8")
    # 关闭 workspace 放行，迫使走 global
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": False},
                "global": {"shared": {"read": "allow", "write": "ask"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(f)}) is None


def test_file_guard_global_nonexistent_prefix_still_matches(tmp_path):
    """配置路径尚未落盘时，resolve(strict=False) 仍应命中规则，不可静默跳过。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    missing_root = tmp_path / "not_created_yet"
    target = missing_root / "file.txt"
    # 不创建 missing_root；仅构造逻辑路径做判定
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {str(missing_root): {"read": "allow", "write": "ask"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.check_external_paths("read_file", {"file_path": str(target)}) is None


def test_file_guard_invalid_mode_value_warns_and_asks(tmp_path, monkeypatch):
    """新字段值非法（如 bool）时 warning，并按 ask 处理。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    f = outside / "f.txt"
    f.write_text("z", encoding="utf-8")
    prefix = str(outside.resolve())
    warnings: list[str] = []

    def _capture_warning(msg, *args, **kwargs):
        warnings.append(msg % args if args else str(msg))

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.permissions.file_guard.logger.warning",
        _capture_warning,
    )
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {"read": True, "write": "allow"}},
            },
        },
        workspace_root=ws,
    )
    res = checker.check_external_paths("read_file", {"file_path": str(f)})
    assert res is not None
    assert res.permission == PermissionLevel.ASK
    assert any("invalid read=" in w for w in warnings)


def test_file_guard_global_exec_relative(tmp_path):
    """global.exec 相对路径相对 workspace resolve。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    scripts = ws / "scripts"
    scripts.mkdir()
    script = scripts / "x.py"
    script.write_text("print(1)\n", encoding="utf-8")
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {"scripts": {"exec": "allow"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.evaluate_accesses([(script.resolve(), "exec", "shlex")]) is None
    outside = tmp_path / "other" / "y.py"
    outside.parent.mkdir()
    outside.write_text("print(2)\n", encoding="utf-8")
    res = checker.evaluate_accesses([(outside.resolve(), "exec", "shlex")])
    assert res is not None
    assert res.permission == PermissionLevel.ASK


def test_file_guard_global_exec_allow_deny(tmp_path):
    ws = tmp_path / "repo"
    ws.mkdir()
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    script = scripts / "x.py"
    script.write_text("print(1)\n", encoding="utf-8")
    prefix = str(scripts.resolve())
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {"exec": "allow"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.evaluate_accesses([(script.resolve(), "exec", "shlex")]) is None

    deny_checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {prefix: {"exec": "deny"}},
            },
        },
        workspace_root=ws,
    )
    res = deny_checker.evaluate_accesses([(script.resolve(), "exec", "shlex")])
    assert res is not None
    assert res.permission == PermissionLevel.DENY


def test_file_guard_exec_not_allowed_by_workspace(tmp_path):
    """workspace 放行不覆盖 exec；缺省 exec 为 ask。"""
    ws = tmp_path / "repo"
    ws.mkdir()
    script = ws / "run.py"
    script.write_text("print(1)\n", encoding="utf-8")
    checker = FileGuardChecker(
        {
            "file_guard": {
                "workspace": {"rw_enabled": True},
                "global": {},
            },
        },
        workspace_root=ws,
    )
    res = checker.evaluate_accesses([(script.resolve(), "exec", "shlex")])
    assert res is not None
    assert res.permission == PermissionLevel.ASK


def test_merged_file_guard_drops_trusted_exec_directory():
    permissions = {
        "file_guard": {
            "global": {"/opt/tools": {"exec": "deny"}},
            "trusted_exec_directory": ["/opt/tools"],
        },
    }
    fg = merged_file_guard_config(permissions)
    assert "trusted_exec_directory" not in fg
    assert fg["global"]["/opt/tools"]["exec"] == "deny"


def test_apply_cli_trusted_writes_new_schema():
    permissions: dict = {}
    apply_cli_trusted_to_permissions_dict(permissions, "/opt/tools")
    entry = permissions["file_guard"]["global"]["/opt/tools"]
    assert entry == {"read": "allow", "write": "allow", "exec": "allow"}
    assert "trusted_exec_directory" not in permissions["file_guard"]


def test_persist_file_operations_writes_exec_except_llm(monkeypatch):
    """「总是允许」可将非 llm 的 exec 写入 global；llm+exec 仍跳过。"""
    from jiuwenclaw.agentserver.permissions.file_guard import (
        persist_file_operations_allow,
    )
    from jiuwenclaw.agentserver.permissions.models import FileOperation

    captured: list[dict] = []

    def _fake_persist(mutate_fn, *, session_id=None):
        perms: dict = {"file_guard": {"global": {}}}
        mutate_fn(perms)
        captured.append(perms)

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.permissions.file_guard._yaml_update_permissions",
        _fake_persist,
    )
    persist_file_operations_allow([
        FileOperation(action="read", path="/tmp/a.txt", source="tool_arg", prompt=""),
        FileOperation(action="exec", path="/tmp/run.py", source="shlex", prompt=""),
        FileOperation(action="exec", path="/tmp/llm.py", source="llm", prompt=""),
    ])
    assert len(captured) == 1
    gm = captured[0]["file_guard"]["global"]
    assert gm["/tmp/a.txt"] == {"read": "allow"}
    assert gm["/tmp/run.py"] == {"exec": "allow"}
    assert "/tmp/llm.py" not in gm


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


def test_file_guard_global_exec_absolute(tmp_path):
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
                "global": {str(scripts.resolve()): {"exec": "allow"}},
            },
        },
        workspace_root=ws,
    )
    assert checker.evaluate_accesses([(script.resolve(), "exec", "shlex")]) is None
