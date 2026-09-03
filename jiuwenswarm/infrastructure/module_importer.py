# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""从 ``manager_config_receiver`` EE 扩展按命名空间加载子模块（不修改 ``sys.path``）。

AgentServer 不一定会走扩展动态加载把 ``packages/jiuwenclaw-ee/gateway/extensions``
注册进 ``jiuwenswarm.loaded_extension``，因此这里按磁盘路径解析扩展根并挂到
``jiuwenswarm.loaded_extension.manager_config_receiver`` 命名空间后再 import。
"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any


LOADED_EXTENSION_PARENT_PKG = "jiuwenswarm.loaded_extension"
MANAGER_CONFIG_RECEIVER_EXT_PKG = f"{LOADED_EXTENSION_PARENT_PKG}.manager_config_receiver"


def is_manager_config_receiver_available() -> bool:
    """``manager_config_receiver`` 扩展目录是否可解析（不执行子模块 import）。"""
    return resolve_manager_config_receiver_root() is not None


def resolve_manager_config_receiver_root() -> Path | None:
    """解析 ``manager_config_receiver`` 扩展根目录（``EXTENSION_DIRS`` 或仓库内置路径）。"""
    for entry in os.getenv("EXTENSION_DIRS", "").split(os.pathsep):
        raw = entry.strip()
        if not raw:
            continue
        base = Path(raw).expanduser()
        for candidate in (base, base / "manager_config_receiver"):
            if (candidate / "infrastructure" / "db.py").is_file():
                return candidate.resolve()

    # jiuwenswarm/infrastructure/module_importer.py → parents[2] = 仓库根
    repo_root = Path(__file__).resolve().parents[2]
    bundled = (
        repo_root
        / "packages"
        / "jiuwenclaw-ee"
        / "gateway"
        / "extensions"
        / "manager_config_receiver"
    )
    if (bundled / "infrastructure" / "db.py").is_file():
        return bundled.resolve()
    return None


def ensure_manager_config_receiver_package(ext_root: Path | None = None) -> Path:
    """注册 ``jiuwenswarm.loaded_extension.manager_config_receiver`` 包命名空间。"""
    root = ext_root if ext_root is not None else resolve_manager_config_receiver_root()
    if root is None:
        raise ImportError("manager_config_receiver extension not found")

    root_str = str(root.resolve())
    if LOADED_EXTENSION_PARENT_PKG not in sys.modules:
        parent = types.ModuleType(LOADED_EXTENSION_PARENT_PKG)
        parent.__path__ = []
        sys.modules[LOADED_EXTENSION_PARENT_PKG] = parent

    existing = sys.modules.get(MANAGER_CONFIG_RECEIVER_EXT_PKG)
    if existing is not None:
        paths = list(getattr(existing, "__path__", []) or [])
        if root_str not in paths:
            paths.append(root_str)
            existing.__path__ = paths
        return root

    ext_pkg = types.ModuleType(MANAGER_CONFIG_RECEIVER_EXT_PKG)
    ext_pkg.__path__ = [root_str]
    ext_pkg.__package__ = MANAGER_CONFIG_RECEIVER_EXT_PKG
    sys.modules[MANAGER_CONFIG_RECEIVER_EXT_PKG] = ext_pkg
    return root


def import_manager_config_receiver_module(module_suffix: str) -> Any:
    """导入扩展内子模块，``module_suffix`` 为点分路径（相对扩展根包）。

    示例::

        import_manager_config_receiver_module("infrastructure.db")
        import_manager_config_receiver_module("core.enterprise_config.gateway_db")
    """
    suffix = str(module_suffix or "").strip().lstrip(".")
    if not suffix:
        raise ValueError("module_suffix is required")
    ensure_manager_config_receiver_package()
    return importlib.import_module(f"{MANAGER_CONFIG_RECEIVER_EXT_PKG}.{suffix}")
