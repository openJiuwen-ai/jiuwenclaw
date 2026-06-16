# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""跨模块基础设施（日志脱敏、EE 扩展加载等）。"""

from jiuwenclaw.infrastructure.module_importer import (
    LOADED_EXTENSION_PARENT_PKG,
    MANAGER_WS_CLIENT_EXT_PKG,
    ensure_manager_ws_client_package,
    import_manager_ws_client_module,
    is_manager_ws_client_available,
    resolve_manager_ws_client_root,
)

__all__ = (
    "LOADED_EXTENSION_PARENT_PKG",
    "MANAGER_WS_CLIENT_EXT_PKG",
    "ensure_manager_ws_client_package",
    "import_manager_ws_client_module",
    "is_manager_ws_client_available",
    "resolve_manager_ws_client_root",
)
