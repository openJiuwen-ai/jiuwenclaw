# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""存储装配层：按 edition 注入 backend，不属于 ``gateway/storage/`` 基础设施。"""

from jiuwenswarm.gateway.storage_assembly.db_connection import (
    GatewayDbConnection,
    assert_replicas_db_compat,
)
from jiuwenswarm.gateway.storage_assembly.layouts import build_gateway_store_registry
from jiuwenswarm.gateway.storage_assembly.manager_ws_bridge import (
    clear_manager_ws_table_store,
    wire_manager_ws_table_store,
)
from jiuwenswarm.gateway.storage_assembly.setup import (
    create_channel_config_repository,
    create_config_record_repository,
    create_enterprise_config_record_repositories,
    create_gateway_storage_context,
    create_logging_config_repository,
    create_memory_config_repository,
    create_permissions_config_repository,
    create_session_map_repository,
    is_storage_repositories_enabled,
    resolve_storage_instance_id,
)

__all__ = [
    "GatewayDbConnection",
    "assert_replicas_db_compat",
    "build_gateway_store_registry",
    "clear_manager_ws_table_store",
    "create_channel_config_repository",
    "create_config_record_repository",
    "create_enterprise_config_record_repositories",
    "create_gateway_storage_context",
    "create_logging_config_repository",
    "create_memory_config_repository",
    "create_permissions_config_repository",
    "create_session_map_repository",
    "is_storage_repositories_enabled",
    "resolve_storage_instance_id",
    "wire_manager_ws_table_store",
]
