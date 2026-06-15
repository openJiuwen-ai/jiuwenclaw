# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""在未 pip 安装 ``jiuwenclaw`` 时，将主仓包注册进 ``sys.modules``（不修改 ``sys.path``）。"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT_ENV_KEYS = (
    "JIUWENCLAW_REPO_ROOT",
    "CLAWMANAGER_PROVISION_REPO_ROOT",
)


def _is_jiuwenclaw_repo_root(path: Path) -> bool:
    return (path / "jiuwenclaw" / "__init__.py").is_file()


def resolve_jiuwenclaw_repo_root() -> Path | None:
    """解析含 ``jiuwenclaw/`` 包的仓库根目录。"""
    for key in _REPO_ROOT_ENV_KEYS:
        raw = os.getenv(key, "").strip()
        if not raw:
            continue
        root = Path(raw).expanduser().resolve()
        if _is_jiuwenclaw_repo_root(root):
            return root

    candidate = Path(__file__).resolve().parents[6]
    if _is_jiuwenclaw_repo_root(candidate):
        return candidate
    return None


def _register_jiuwenclaw_from_root(root: Path) -> None:
    """将 ``{root}/jiuwenclaw`` 注册为可导入包（追加 ``__path__``，不 prepend ``sys.path``）。"""
    init_py = root / "jiuwenclaw" / "__init__.py"
    if not init_py.is_file():
        raise ImportError(f"jiuwenclaw package not found under {root}")

    pkg_dir = str((root / "jiuwenclaw").resolve())
    existing = sys.modules.get("jiuwenclaw")
    if existing is not None:
        paths = list(getattr(existing, "__path__", []) or [])
        if pkg_dir not in paths:
            paths.append(pkg_dir)
            existing.__path__ = paths
        return

    spec = importlib.util.spec_from_file_location(
        "jiuwenclaw",
        init_py,
        submodule_search_locations=[pkg_dir],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"failed to load jiuwenclaw from {init_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["jiuwenclaw"] = module
    spec.loader.exec_module(module)


def ensure_jiuwenclaw_importable(repo_root: Path | None = None) -> Path:
    """保证 ``import jiuwenclaw...`` 可用；返回仓库根目录。"""
    try:
        importlib.import_module("jiuwenclaw")
    except ImportError:
        root = repo_root if repo_root is not None else resolve_jiuwenclaw_repo_root()
        if root is None:
            raise ImportError(
                "jiuwenclaw package not found; set JIUWENCLAW_REPO_ROOT or "
                "CLAWMANAGER_PROVISION_REPO_ROOT, or add the repo root to PYTHONPATH"
            ) from None
        _register_jiuwenclaw_from_root(root)
        importlib.import_module("jiuwenclaw")
        return root.resolve()

    resolved = repo_root if repo_root is not None else resolve_jiuwenclaw_repo_root()
    if resolved is not None:
        return resolved.resolve()

    inferred = resolve_jiuwenclaw_repo_root()
    if inferred is not None:
        return inferred.resolve()
    return Path.cwd().resolve()


def import_jiuwenclaw_module(module_suffix: str) -> Any:
    """导入主仓子模块，``module_suffix`` 为相对 ``jiuwenclaw`` 的点分路径。

    示例::

        import_jiuwenclaw_module("infrastructure.log_masking.engine")
    """
    suffix = str(module_suffix or "").strip().lstrip(".")
    if not suffix:
        raise ValueError("module_suffix is required")
    ensure_jiuwenclaw_importable()
    return importlib.import_module(f"jiuwenclaw.{suffix}")
