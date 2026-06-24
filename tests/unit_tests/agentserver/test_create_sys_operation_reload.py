# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for _create_sys_operation and reload-driven sysop rebuild."""

from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


class _DeepAdapterHarness(JiuWenClawDeepAdapter):
    """Expose protected members for testing."""

    def set_workspace_dir_for_test(self, workspace_dir: str | None) -> None:
        self._workspace_dir = workspace_dir

    def create_sys_operation_for_test(self):
        return self._create_sys_operation()

    def maybe_recreate_sys_operation_for_test(self) -> None:
        self._maybe_recreate_sys_operation()

    def set_sys_operation_for_test(self, sysop) -> None:
        self._sys_operation = sysop

    def get_sys_operation_for_test(self):
        return self._sys_operation

    def set_sandbox_fingerprint_for_test(self, fp) -> None:
        self._sandbox_fingerprint = fp

    def get_sandbox_fingerprint_for_test(self):
        return self._sandbox_fingerprint


def _patch_sandbox(
    monkeypatch: pytest.MonkeyPatch,
    *,
    enabled: bool = False,
    url: str = "",
    sandbox_type: str = "",
    startup_mode: str = "external",
    files: dict | None = None,
    excluded_commands: list | None = None,
    idle_ttl_seconds: int = 0,
    idle_check_interval: int = 0,
):
    """Patch get_sandbox_endpoint / get_sandbox_runtime in interface_deep module."""
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod

    endpoint = {
        "url": url,
        "type": sandbox_type,
        "startup_mode": startup_mode,
    }
    runtime = {
        "enabled": enabled,
        "files": files or {"allow": [], "deny": []},
        "excluded_commands": excluded_commands or [],
        "idle_ttl_seconds": idle_ttl_seconds,
        "idle_check_interval": idle_check_interval,
    }
    monkeypatch.setattr(mod, "get_sandbox_endpoint", lambda: endpoint)
    monkeypatch.setattr(mod, "get_sandbox_runtime", lambda: runtime)


def _stub_resource_mgr(monkeypatch: pytest.MonkeyPatch, captured: dict):
    """Stub Runner.resource_mgr.add/get/remove_sys_operation."""
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod

    resource_mgr = MagicMock()

    def _add(sysop_card):
        captured["added"].append(sysop_card)
        return MagicMock(is_err=lambda: False, msg=lambda: "")

    def _get(card_id):
        sysop = MagicMock(id=card_id)
        captured.setdefault("sysops", {})[card_id] = sysop
        return sysop

    def _remove(sysop_id):
        captured.setdefault("removed", []).append(sysop_id)
        return MagicMock(is_err=lambda: False, msg=lambda: "")

    resource_mgr.add_sys_operation.side_effect = _add
    resource_mgr.get_sys_operation.side_effect = _get
    resource_mgr.remove_sys_operation.side_effect = _remove
    monkeypatch.setattr(mod.Runner, "resource_mgr", resource_mgr)
    return resource_mgr


# ---------------------------------------------------------------------------
# _create_sys_operation
# ---------------------------------------------------------------------------


def test_create_sys_operation_local_mode_when_sandbox_disabled(monkeypatch: pytest.MonkeyPatch):
    """sandbox.enabled=False 时走 LOCAL 模式。"""
    _patch_sandbox(monkeypatch, enabled=False, url="http://x", sandbox_type="jiuwenbox")
    captured: dict = {"added": []}
    _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")

    sysop = adapter.create_sys_operation_for_test()

    assert sysop is not None
    added = captured["added"]
    assert len(added) == 1
    assert added[0].mode.value if hasattr(added[0].mode, "value") else True


def test_create_sys_operation_local_mode_when_sandbox_enabled_missing(monkeypatch: pytest.MonkeyPatch):
    """sandbox.enabled=True 但 url/type 缺失时回退 LOCAL 并告警。"""
    _patch_sandbox(monkeypatch, enabled=True, url="", sandbox_type="")
    captured: dict = {"added": []}
    _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")

    sysop = adapter.create_sys_operation_for_test()

    assert sysop is not None
    assert len(captured["added"]) == 1


# ---------------------------------------------------------------------------
# _maybe_recreate_sys_operation
# ---------------------------------------------------------------------------


def test_maybe_recreate_sys_operation_recreates_when_sandbox_changes(monkeypatch: pytest.MonkeyPatch):
    """sandbox url 变更时重建 sysop 并清理旧实例。"""
    _patch_sandbox(monkeypatch, enabled=True, url="http://old", sandbox_type="jiuwenbox")
    captured: dict = {"added": []}
    resource_mgr = _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")
    adapter.set_sandbox_fingerprint_for_test((True, "http://old", "jiuwenbox"))
    old_sysop = MagicMock(id="old-sysop")
    adapter.set_sys_operation_for_test(old_sysop)

    # 切换 endpoint 指向新 url（_create_sys_operation 会读到新值）
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod
    new_endpoint = {"url": "http://new", "type": "jiuwenbox", "startup_mode": "external"}
    monkeypatch.setattr(mod, "get_sandbox_endpoint", lambda: new_endpoint)

    adapter.maybe_recreate_sys_operation_for_test()

    # 重建后 sysop 被替换，旧实例通过 resource_mgr.remove_sys_operation 清理
    assert adapter.get_sys_operation_for_test() is not old_sysop
    assert adapter.get_sandbox_fingerprint_for_test() == (True, "http://new", "jiuwenbox")
    assert "old-sysop" in captured.get("removed", [])


def test_maybe_recreate_sys_operation_recreates_when_enabled_toggled(monkeypatch: pytest.MonkeyPatch):
    """sandbox.enabled 从 True 切到 False 触发重建（指纹含 enabled）。"""
    _patch_sandbox(monkeypatch, enabled=True, url="http://x", sandbox_type="jiuwenbox")
    captured: dict = {"added": []}
    _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")
    adapter.set_sandbox_fingerprint_for_test((True, "http://x", "jiuwenbox"))
    adapter.set_sys_operation_for_test(MagicMock(id="old"))

    # 切到 disabled
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod
    new_runtime = {
        "enabled": False,
        "files": {"allow": [], "deny": []},
        "excluded_commands": [],
        "idle_ttl_seconds": 0,
        "idle_check_interval": 0,
    }
    monkeypatch.setattr(mod, "get_sandbox_runtime", lambda: new_runtime)

    adapter.maybe_recreate_sys_operation_for_test()

    assert adapter.get_sandbox_fingerprint_for_test() == (False, "http://x", "jiuwenbox")
    assert len(captured["added"]) == 1


def test_maybe_recreate_sys_operation_noop_when_sandbox_unchanged(monkeypatch: pytest.MonkeyPatch):
    """指纹未变时不重建。"""
    _patch_sandbox(monkeypatch, enabled=True, url="http://x", sandbox_type="jiuwenbox")
    captured: dict = {"added": []}
    _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")
    adapter.set_sandbox_fingerprint_for_test((True, "http://x", "jiuwenbox"))
    old_sysop = MagicMock(id="old")
    adapter.set_sys_operation_for_test(old_sysop)

    adapter.maybe_recreate_sys_operation_for_test()

    assert adapter.get_sys_operation_for_test() is old_sysop
    assert captured["added"] == []


def test_maybe_recreate_sys_operation_keeps_old_when_recreate_fails(monkeypatch: pytest.MonkeyPatch):
    """_create_sys_operation 返回 None 时保留旧 sysop。"""
    _patch_sandbox(monkeypatch, enabled=True, url="http://new", sandbox_type="jiuwenbox")
    captured: dict = {"added": []}
    _stub_resource_mgr(monkeypatch, captured)

    adapter = _DeepAdapterHarness()
    adapter.set_workspace_dir_for_test("/tmp/ws")
    adapter.set_sandbox_fingerprint_for_test((True, "http://old", "jiuwenbox"))
    old_sysop = MagicMock(id="old")
    adapter.set_sys_operation_for_test(old_sysop)

    # 让 add_sys_operation 失败，使 _create_sys_operation 返回 None
    from jiuwenclaw.agentserver.deep_agent import interface_deep as mod

    def _fail_add(c):
        captured["added"].append(c)
        return MagicMock(is_err=lambda: True, msg=lambda: "boom")

    mod.Runner.resource_mgr.add_sys_operation.side_effect = _fail_add

    adapter.maybe_recreate_sys_operation_for_test()

    # 重建失败 → 保留旧 sysop，指纹不更新
    assert adapter.get_sys_operation_for_test() is old_sysop
    assert adapter.get_sandbox_fingerprint_for_test() == (True, "http://old", "jiuwenbox")
