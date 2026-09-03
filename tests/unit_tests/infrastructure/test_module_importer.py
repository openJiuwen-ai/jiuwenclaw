# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""module_importer：按磁盘路径挂载 manager_config_receiver EE 扩展子模块。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from jiuwenswarm.infrastructure import module_importer as mi


def test_resolve_manager_config_receiver_root_bundled() -> None:
    root = mi.resolve_manager_config_receiver_root()
    assert root is not None
    assert (root / "infrastructure" / "db.py").is_file()
    assert root.name == "manager_config_receiver"


def test_import_manager_config_receiver_module_from_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    ext = tmp_path / "manager_config_receiver"
    (ext / "infrastructure").mkdir(parents=True)
    (ext / "infrastructure" / "db.py").write_text("# marker\n", encoding="utf-8")
    (ext / "stubmod.py").write_text("VALUE = 7\n", encoding="utf-8")

    monkeypatch.delenv("EXTENSION_DIRS", raising=False)
    monkeypatch.setattr(mi, "resolve_manager_config_receiver_root", lambda: ext.resolve())
    # 清掉可能已存在的命名空间，强制按临时目录重新挂载。
    sys.modules.pop(mi.MANAGER_CONFIG_RECEIVER_EXT_PKG, None)
    sys.modules.pop(f"{mi.MANAGER_CONFIG_RECEIVER_EXT_PKG}.stubmod", None)

    mod = mi.import_manager_config_receiver_module("stubmod")
    assert mod.VALUE == 7


def test_import_manager_config_receiver_module_reuses_sys_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = types.ModuleType(mi.LOADED_EXTENSION_PARENT_PKG)
    parent.__path__ = []
    root = types.ModuleType(mi.MANAGER_CONFIG_RECEIVER_EXT_PKG)
    root.__path__ = []
    stub = types.ModuleType(f"{mi.MANAGER_CONFIG_RECEIVER_EXT_PKG}.stubmod")
    stub.VALUE = 9

    monkeypatch.setitem(sys.modules, mi.LOADED_EXTENSION_PARENT_PKG, parent)
    monkeypatch.setitem(sys.modules, mi.MANAGER_CONFIG_RECEIVER_EXT_PKG, root)
    monkeypatch.setitem(sys.modules, f"{mi.MANAGER_CONFIG_RECEIVER_EXT_PKG}.stubmod", stub)
    monkeypatch.setattr(
        mi,
        "ensure_manager_config_receiver_package",
        lambda ext_root=None: Path("."),
    )

    mod = mi.import_manager_config_receiver_module("stubmod")
    assert mod is stub
    assert mod.VALUE == 9
