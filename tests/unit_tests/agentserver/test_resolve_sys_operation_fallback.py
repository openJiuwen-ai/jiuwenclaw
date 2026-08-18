# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""_resolve_sys_operation must not crash when installed openjiuwen lacks resolve_sandbox."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


class _OkResult:
    def is_err(self) -> bool:
        return False


class _FakeResourceMgr:
    def add_sys_operation(self, _card: object) -> _OkResult:
        return _OkResult()

    def get_sys_operation(self, sys_operation_id: str) -> object:
        return SimpleNamespace(id=sys_operation_id)


def _make_adapter() -> JiuWenSwarmDeepAdapter:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._sys_operation_card = None
    adapter._is_code_agent = False
    adapter._project_dir = None
    adapter._instance_overrides = {}
    return adapter


def test_resolve_sys_operation_falls_back_to_host_without_resolve_sandbox(
    monkeypatch,
) -> None:
    adapter = _make_adapter()
    fake_card = MagicMock()
    fake_card.id = "local-sysop"
    fake_op = SimpleNamespace(id="local-sysop")

    monkeypatch.setattr(interface_deep, "_import_resolve_sandbox", lambda: None)
    monkeypatch.setattr(interface_deep, "get_sandbox_endpoint", lambda: {"url": "", "type": ""})
    monkeypatch.setattr(interface_deep, "get_sandbox_runtime", lambda: {"enabled": True})
    monkeypatch.setattr(interface_deep, "create_local_sysop_card", lambda: fake_card)
    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_sys_operation_isolation_key",
        staticmethod(lambda _card: None),
    )
    monkeypatch.setattr(
        JiuWenSwarmDeepAdapter,
        "_get_registered_sys_operation_by_isolation_key",
        staticmethod(lambda _key: None),
    )
    mgr = _FakeResourceMgr()
    monkeypatch.setattr(mgr, "get_sys_operation", lambda _op_id: fake_op)
    monkeypatch.setattr(interface_deep.Runner, "resource_mgr", mgr)

    result = adapter._resolve_sys_operation()

    assert result is fake_op
    assert adapter._sys_operation_card is fake_card
