# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""module_importer：按需加载 manager_config_receiver EE 扩展子模块。"""

from __future__ import annotations

import sys
import types

import pytest

from jiuwenswarm.infrastructure import module_importer as mi


def test_import_manager_config_receiver_module_uses_loaded_extension(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = types.ModuleType("jiuwenswarm.loaded_extension")
    parent.__path__ = []
    root = types.ModuleType("jiuwenswarm.loaded_extension.manager_config_receiver")
    root.__path__ = []
    stub = types.ModuleType(
        "jiuwenswarm.loaded_extension.manager_config_receiver.stubmod"
    )
    stub.VALUE = 7

    monkeypatch.setitem(sys.modules, "jiuwenswarm.loaded_extension", parent)
    monkeypatch.setitem(
        sys.modules, "jiuwenswarm.loaded_extension.manager_config_receiver", root
    )
    monkeypatch.setitem(
        sys.modules,
        "jiuwenswarm.loaded_extension.manager_config_receiver.stubmod",
        stub,
    )

    mod = mi.import_manager_config_receiver_module("stubmod")
    assert mod is stub
    assert mod.VALUE == 7


def test_import_manager_ws_client_module_is_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = types.ModuleType("jiuwenswarm.loaded_extension")
    parent.__path__ = []
    root = types.ModuleType("jiuwenswarm.loaded_extension.manager_config_receiver")
    root.__path__ = []
    stub = types.ModuleType(
        "jiuwenswarm.loaded_extension.manager_config_receiver.aliasmod"
    )

    monkeypatch.setitem(sys.modules, "jiuwenswarm.loaded_extension", parent)
    monkeypatch.setitem(
        sys.modules, "jiuwenswarm.loaded_extension.manager_config_receiver", root
    )
    monkeypatch.setitem(
        sys.modules,
        "jiuwenswarm.loaded_extension.manager_config_receiver.aliasmod",
        stub,
    )

    assert mi.import_manager_ws_client_module("aliasmod") is stub


def test_import_manager_config_receiver_module_falls_back_to_manager_ws_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = types.ModuleType("jiuwenswarm.loaded_extension")
    parent.__path__ = []
    legacy = types.ModuleType("jiuwenswarm.loaded_extension.manager_ws_client")
    legacy.__path__ = []
    stub = types.ModuleType(
        "jiuwenswarm.loaded_extension.manager_ws_client.legacy_stub"
    )
    stub.OK = True

    # 清掉可能已存在的新扩展缓存，强制走旧名回退。
    for key in list(sys.modules):
        if "loaded_extension.manager_config_receiver" in key:
            monkeypatch.delitem(sys.modules, key, raising=False)

    monkeypatch.setitem(sys.modules, "jiuwenswarm.loaded_extension", parent)
    monkeypatch.setitem(
        sys.modules, "jiuwenswarm.loaded_extension.manager_ws_client", legacy
    )
    monkeypatch.setitem(
        sys.modules,
        "jiuwenswarm.loaded_extension.manager_ws_client.legacy_stub",
        stub,
    )

    mod = mi.import_manager_config_receiver_module("legacy_stub")
    assert mod is stub
    assert mod.OK is True
