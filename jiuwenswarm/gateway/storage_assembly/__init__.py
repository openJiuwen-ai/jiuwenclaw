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
    create_a2a_outbound_repository,
    create_a2ui_config_repository,
    create_browser_config_repository,
    create_channel_config_repository,
    create_enterprise_record_repository,
    create_enterprise_record_repositories,
    create_gateway_storage_context,
    create_heartbeat_config_repository,
    create_logging_config_repository,
    create_memory_config_repository,
    create_permissions_config_repository,
    create_preferred_language_config_repository,
    create_session_map_repository,
    is_session_map_repository_enabled,
    is_storage_repositories_enabled,
    resolve_storage_instance_id,
    setup_gateway_storage_repositories,
    setup_session_map_repository,
    teardown_gateway_storage_repositories,
    teardown_session_map_repository,
)

__all__ = [
    "GatewayDbConnection",
    "assert_replicas_db_compat",
    "build_gateway_store_registry",
    "create_a2a_outbound_repository",
    "clear_manager_ws_table_store",
    "create_a2ui_config_repository",
    "create_browser_config_repository",
    "create_channel_config_repository",
    "create_enterprise_record_repository",
    "create_enterprise_record_repositories",
    "create_gateway_storage_context",
    "create_heartbeat_config_repository",
    "create_logging_config_repository",
    "create_memory_config_repository",
    "create_permissions_config_repository",
    "create_preferred_language_config_repository",
    "create_session_map_repository",
    "is_session_map_repository_enabled",
    "is_storage_repositories_enabled",
    "resolve_storage_instance_id",
    "setup_gateway_storage_repositories",
    "setup_session_map_repository",
    "teardown_gateway_storage_repositories",
    "teardown_session_map_repository",
    "wire_manager_ws_table_store",
]
