# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Clear WebView HTTP caches that survive app upgrades/reinstalls.

pywebview keeps a persistent profile under ~/.jiuwenavatar/webview. WebView2 /
WKWebView cache JS/CSS responses locally; reinstalling the exe does not clear
that profile, so users can see stale UI after upgrades.

Only HTTP-related cache directories are removed. localStorage, IndexedDB, and
other session preferences in the profile are preserved.
"""

from __future__ import annotations

import logging
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Relative paths under the pywebview storage_path (…/webview).
_WINDOWS_HTTP_CACHE_REL_DIRS: tuple[Path, ...] = (
    Path("EBWebView/Default/Cache"),
    Path("EBWebView/Default/Code Cache"),
    Path("EBWebView/Default/GPUCache"),
    Path("EBWebView/Default/Service Worker/CacheStorage"),
    Path("EBWebView/ShaderCache"),
    Path("EBWebView/GrShaderCache"),
    Path("EBWebView/GPUPersistentCache"),
)

_MACOS_HTTP_CACHE_DIR = Path.home() / "Library" / "Caches" / "com.jiuwenavatar.desktop"


def _remove_tree(path: Path) -> bool:
  """Remove a directory tree; return False if files are in use or removal fails."""
  try:
    shutil.rmtree(path)
    return True
  except OSError as exc:
    logger.warning("[desktop] failed to clear WebView HTTP cache %s: %s", path, exc)
    return False


def clear_webview_http_cache(storage_path: Path) -> int:
    """Remove HTTP-related WebView cache directories.

    Returns:
        Number of cache directories removed.
    """
    removed = 0

    if sys.platform == "darwin":
        if _MACOS_HTTP_CACHE_DIR.exists() and _remove_tree(_MACOS_HTTP_CACHE_DIR):
            removed += 1
            logger.info("[desktop] cleared WKWebView HTTP cache: %s", _MACOS_HTTP_CACHE_DIR)
    elif sys.platform == "win32":
        for rel in _WINDOWS_HTTP_CACHE_REL_DIRS:
            target = storage_path / rel
            if not target.exists():
                continue
            if _remove_tree(target):
                removed += 1
                logger.info("[desktop] cleared WebView2 HTTP cache: %s", target)

    return removed
