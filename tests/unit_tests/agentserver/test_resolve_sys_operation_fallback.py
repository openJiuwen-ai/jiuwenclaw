# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""HOST vs sandbox placement is decided locally, not via core resolve_sandbox."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ce21a9b7 worktree/git.py has an invalid docstring escape. Import the adapter
# inside tests so pytest.ini ``filterwarnings = error`` does not fail collection.
pytestmark = pytest.mark.filterwarnings("ignore::SyntaxWarning")


class _OkResult:
    def is_err(self) -> bool:
        return False


class _FakeResourceMgr:
    def add_sys_operation(self, _card: object) -> _OkResult:
        return _OkResult()

    def get_sys_operation(self, sys_operation_id: str) -> object:
        return SimpleNamespace(id=sys_operation_id)


def test_interface_deep_does_not_import_core_resolve_sandbox() -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

    assert not hasattr(interface_deep, "_import_resolve_sandbox")
    source = inspect.getsource(JiuWenSwarmDeepAdapter._resolve_sys_operation)
    assert "resolve_sandbox" not in source
    assert "resolve_sysop_placement" in source


def test_resolve_sys_operation_uses_host_when_sandbox_unavailable(monkeypatch) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._sys_operation_card = None
    adapter._is_code_agent = False
    adapter._project_dir = None
    adapter._instance_overrides = {}
    fake_card = MagicMock()
    fake_card.id = "local-sysop"
    fake_op = SimpleNamespace(id="local-sysop")

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
