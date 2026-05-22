# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""``channel_config`` 冷启动读库：经 manager_ws_client ``DBHandler``（与 WS 写库同栈）。"""

from __future__ import annotations

import importlib
import os
import sys
import types
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import logger

_LOG = "[channel_config_db]"
_PARENT_PKG = "jiuwenclaw.loaded_extension"
_EXT_PKG = f"{_PARENT_PKG}.manager_ws_client"


def _resolve_manager_ws_client_root() -> Path | None:
    for entry in os.getenv("EXTENSION_DIRS", "").split(os.pathsep):
        raw = entry.strip()
        if not raw:
            continue
        base = Path(raw).expanduser()
        for candidate in (base, base / "manager_ws_client"):
            if (candidate / "infrastructure" / "db.py").is_file():
                return candidate.resolve()

    repo_root = Path(__file__).resolve().parents[2]
    bundled = (
        repo_root
        / "packages"
        / "jiuwenclaw-ee"
        / "gateway"
        / "extensions"
        / "manager_ws_client"
    )
    if (bundled / "infrastructure" / "db.py").is_file():
        return bundled.resolve()
    return None


def _ensure_extension_package(ext_root: Path) -> None:
    """注册扩展包命名空间（对齐 ExtensionLoader），不修改 sys.path。"""
    root_str = str(ext_root.resolve())
    if _PARENT_PKG not in sys.modules:
        parent = types.ModuleType(_PARENT_PKG)
        parent.__path__ = []
        sys.modules[_PARENT_PKG] = parent

    existing = sys.modules.get(_EXT_PKG)
    if existing is not None:
        paths = list(getattr(existing, "__path__", []) or [])
        if root_str not in paths:
            paths.append(root_str)
            existing.__path__ = paths
        return

    ext_pkg = types.ModuleType(_EXT_PKG)
    ext_pkg.__path__ = [root_str]
    ext_pkg.__package__ = _EXT_PKG
    sys.modules[_EXT_PKG] = ext_pkg


def _load_manager_ws_client_modules() -> tuple[Any, Any]:
    ext_root = _resolve_manager_ws_client_root()
    if ext_root is None:
        raise ImportError("manager_ws_client extension not found")

    _ensure_extension_package(ext_root)
    db_mod = importlib.import_module(f"{_EXT_PKG}.infrastructure.db")
    channel_mod = importlib.import_module(
        f"{_EXT_PKG}.core.application_config.channel_config"
    )
    return db_mod.ensure_db_handler_ready, channel_mod.list_active_channel_config_rows


async def load_active_channel_config_rows() -> list[dict[str, Any]]:
    """连接 manager_ws_client 库并列出 active ``channel_config`` 行；失败时返回空列表。"""
    try:
        ensure_db_handler_ready, list_active = _load_manager_ws_client_modules()
        handler = await ensure_db_handler_ready()
        return await list_active(handler)
    except Exception as exc:  # noqa: BLE001
        logger.warning("%s channel_config read failed: %s", _LOG, exc, exc_info=True)
        return []
