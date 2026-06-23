# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""EE 扩展等外部模块的动态加载。"""

from jiuwenclaw.infrastructure.module_importer.manager_ws_client_importer import (
    LOADED_EXTENSION_PARENT_PKG,
    MANAGER_WS_CLIENT_EXT_PKG,
    ensure_manager_ws_client_package,
    import_manager_ws_client_module,
    resolve_manager_ws_client_root,
)

__all__ = (
    "LOADED_EXTENSION_PARENT_PKG",
    "MANAGER_WS_CLIENT_EXT_PKG",
    "ensure_manager_ws_client_package",
    "import_manager_ws_client_module",
    "resolve_manager_ws_client_root",
)
