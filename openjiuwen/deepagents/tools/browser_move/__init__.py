# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""browser_move package for browser runtime integration."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent

__all__ = [
    "REPO_ROOT",
    "build_browser_runtime_mcp_config",
    "register_browser_runtime_mcp_server",
    "restart_local_browser_runtime_server",
    "stop_local_browser_runtime_server",
]


def __getattr__(name: str) -> Any:
    if name in {
        "build_browser_runtime_mcp_config",
        "register_browser_runtime_mcp_server",
        "restart_local_browser_runtime_server",
        "stop_local_browser_runtime_server",
    }:
        module = import_module("openjiuwen.deepagents.tools.browser_move.playwright_runtime.browser_tools")
        return getattr(module, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
