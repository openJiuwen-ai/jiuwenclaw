# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""动态加载 Gateway EE 扩展 ``manager_config_receiver`` 子模块。"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType

_EXTENSION_NAME = "manager_config_receiver"


def _loaded_extension_module() -> ModuleType:
    full = f"jiuwenswarm.loaded_extension.{_EXTENSION_NAME}"
    if full in sys.modules:
        return sys.modules[full]
    return importlib.import_module(full)


def import_manager_config_receiver_module(submodule: str) -> ModuleType:
    """导入 ``manager_config_receiver`` 扩展内子模块。"""
    full = f"jiuwenswarm.loaded_extension.{_EXTENSION_NAME}.{submodule}"
    if full in sys.modules:
        return sys.modules[full]
    parent = f"jiuwenswarm.loaded_extension.{_EXTENSION_NAME}"
    if parent in sys.modules:
        return importlib.import_module(f".{submodule}", parent)
    root = _loaded_extension_module()
    return importlib.import_module(f".{submodule}", root.__name__)
