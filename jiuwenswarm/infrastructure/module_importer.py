# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""动态加载 Gateway EE 扩展 ``manager_config_receiver`` 子模块。"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

_EXTENSION_NAMES = (
    "manager_config_receiver",
    "manager_ws_client",
)


def _loaded_extension_module(name: str) -> ModuleType:
    full = f"jiuwenswarm.loaded_extension.{name}"
    if full in sys.modules:
        return sys.modules[full]
    for ext_name in _EXTENSION_NAMES:
        candidate = f"jiuwenswarm.loaded_extension.{ext_name}"
        if candidate in sys.modules:
            root = sys.modules[candidate]
            pkg = ModuleType(full)
            pkg.__path__ = list(getattr(root, "__path__", []))
            sys.modules[full] = pkg
            return pkg
    return importlib.import_module(full)


def import_manager_config_receiver_module(submodule: str) -> ModuleType:
    """导入 ``manager_config_receiver`` 扩展内子模块。"""
    for ext_name in _EXTENSION_NAMES:
        full = f"jiuwenswarm.loaded_extension.{ext_name}.{submodule}"
        if full in sys.modules:
            return sys.modules[full]
        parent = f"jiuwenswarm.loaded_extension.{ext_name}"
        if parent in sys.modules:
            return importlib.import_module(f".{submodule}", parent)
    parent = _loaded_extension_module("manager_config_receiver")
    return importlib.import_module(f".{submodule}", parent.__name__)


def import_manager_ws_client_module(submodule: str) -> ModuleType:
    """兼容旧名：等价于 ``import_manager_config_receiver_module``。"""
    return import_manager_config_receiver_module(submodule)
