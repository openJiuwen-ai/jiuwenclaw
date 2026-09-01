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
