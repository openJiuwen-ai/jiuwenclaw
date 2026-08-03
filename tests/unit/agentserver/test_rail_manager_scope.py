# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.extensions.rail_manager import RailManagerPool, get_rail_manager
from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.tenant_context import (
    bind_tenant_workspace_dirs,
    reset_tenant_workspace_dirs,
)


def setup_function() -> None:
    RailManagerPool.reset_for_tests()


def teardown_function() -> None:
    RailManagerPool.reset_for_tests()


def test_rail_managers_are_tenant_isolated(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.extensions.rail_manager.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"{sid}_{aid}",
    )

    a = get_rail_manager(RuntimeScopeKey.from_ids("svc1", "aid1"))
    b = get_rail_manager(RuntimeScopeKey.from_ids("svc2", "aid2"))
    a2 = get_rail_manager(RuntimeScopeKey.from_ids("svc1", "aid1"))

    assert a is a2
    assert a is not b
    assert a.service_id == "svc1"
    assert "svc1_aid1" in str(a.extensions_dir)
    assert "svc2_aid2" in str(b.extensions_dir)


def test_get_rail_manager_rejects_none_scope() -> None:
    with pytest.raises(TypeError, match="non-None scope"):
        get_rail_manager(None)


def test_get_rail_manager_rejects_unsupported_scope() -> None:
    with pytest.raises(TypeError, match="unsupported rail manager scope"):
        get_rail_manager(object())


def test_default_tenant_requires_explicit_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.extensions.rail_manager.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"{sid}_{aid}",
    )

    mgr = get_rail_manager(RuntimeScopeKey.from_ids("default", "default"))
    assert mgr.service_id == "default"
    assert mgr.agent_id == "default"
    assert "default_default" in str(mgr.extensions_dir)
    assert mgr is get_rail_manager(RuntimeScopeKey())


def test_extensions_dir_ignores_bound_workspace(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.extensions.rail_manager.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"{sid}_{aid}",
    )
    other = tmp_path / "bound_other_tenant" / "jiuwenclaw_workspace"
    other.mkdir(parents=True)
    tokens = bind_tenant_workspace_dirs(
        jiuwenclaw_workspace=str(other),
        agent_root=str(other.parent),
        tenant_root=str(other.parent.parent),
    )
    try:
        mgr = get_rail_manager(RuntimeScopeKey.from_ids("default", "default"))
        assert "default_default" in str(mgr.extensions_dir)
        assert "bound_other_tenant" not in str(mgr.extensions_dir)
    finally:
        reset_tenant_workspace_dirs(tokens)


def test_rail_manager_pool_remove_disposes(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.extensions.rail_manager.get_multi_tenant_user_workspace_dir",
        lambda sid, aid: tmp_path / f"{sid}_{aid}",
    )
    mgr = get_rail_manager(RuntimeScopeKey.from_ids("default", "office"))
    mgr._rail_instances["x"] = object()
    mgr._agent_instance = object()

    assert RailManagerPool.remove("default", "office") is True
    assert RailManagerPool.remove("default", "office") is False
    assert mgr._rail_instances == {}
    assert mgr._agent_instance is None

    again = get_rail_manager(RuntimeScopeKey.from_ids("default", "office"))
    assert again is not mgr
