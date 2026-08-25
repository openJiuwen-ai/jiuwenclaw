# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""module_importer：按需加载 manager_ws_client EE 扩展子模块。"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from jiuwenswarm.infrastructure.module_importer import (
    MANAGER_WS_CLIENT_EXT_PKG,
    ensure_manager_ws_client_package,
    import_manager_ws_client_module,
    is_manager_ws_client_available,
    resolve_manager_ws_client_root,
)


def test_resolve_manager_ws_client_root_finds_bundled_extension() -> None:
    root = resolve_manager_ws_client_root()
    assert root is not None
    assert (root / "infrastructure" / "db.py").is_file()
    assert is_manager_ws_client_available()


def test_import_manager_ws_client_module_requires_suffix() -> None:
    with pytest.raises(ValueError, match="module_suffix is required"):
        import_manager_ws_client_module("")


def test_resolve_manager_ws_client_root_honors_extension_dirs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ext_root = tmp_path / "manager_ws_client"
    (ext_root / "infrastructure").mkdir(parents=True)
    (ext_root / "infrastructure" / "db.py").write_text("# stub\n", encoding="utf-8")
    monkeypatch.setenv("EXTENSION_DIRS", str(tmp_path))
    assert resolve_manager_ws_client_root() == ext_root.resolve()


def test_import_manager_ws_client_module_loads_stub_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ext_root = tmp_path / "manager_ws_client"
    (ext_root / "infrastructure").mkdir(parents=True)
    (ext_root / "infrastructure" / "db.py").write_text("# stub\n", encoding="utf-8")
    (ext_root / "stubmod.py").write_text("VALUE = 42\n", encoding="utf-8")
    monkeypatch.setenv("EXTENSION_DIRS", str(tmp_path))

    ensure_manager_ws_client_package()
    mod = import_manager_ws_client_module("stubmod")
    assert mod.VALUE == 42
    assert mod.__name__ == f"{MANAGER_WS_CLIENT_EXT_PKG}.stubmod"


def test_import_manager_ws_client_module_table_init() -> None:
    openjiuwen_runtime = pytest.importorskip("openjiuwen_runtime")
    _ = openjiuwen_runtime
    mod = import_manager_ws_client_module("models.table_init")
    assert hasattr(mod, "init_all_tables")
    assert hasattr(mod, "ALL_TABLE_DEFINITIONS")
    table_names = {table.table_name for table in mod.ALL_TABLE_DEFINITIONS}
    assert "session_map" in table_names


def test_import_manager_ws_client_module_gateway_db() -> None:
    openjiuwen_runtime = pytest.importorskip("openjiuwen_runtime")
    _ = openjiuwen_runtime
    mod = import_manager_ws_client_module("core.enterprise_config.gateway_db")
    assert hasattr(mod, "GatewayDb")
