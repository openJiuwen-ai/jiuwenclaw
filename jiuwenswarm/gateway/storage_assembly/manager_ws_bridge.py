# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""将 StorageContext 注入 Manager WS 写路径。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenswarm.gateway.storage.context import StorageContext


def wire_manager_ws_table_store(ctx: StorageContext) -> None:
    """在 Gateway 启动时注册 persistent store 为 Manager WS 写库入口。"""
    from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

    access = import_manager_ws_client_module("infrastructure.table_store_access")
    access.set_table_store_provider(ctx.persistent)


def clear_manager_ws_table_store() -> None:
    """测试或 shutdown 时解除注入。"""
    from jiuwenclaw.infrastructure.module_importer import import_manager_ws_client_module

    access = import_manager_ws_client_module("infrastructure.table_store_access")
    access.clear_table_store_provider()


__all__ = ["clear_manager_ws_table_store", "wire_manager_ws_table_store"]
