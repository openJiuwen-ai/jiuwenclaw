# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Agent Client 扩展在 ``models`` 包内声明的所有表的初始化顺序与入口。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.table_def import TableDefinition

from .application_config_models import CHANNEL_CONFIG_TABLE_DEF, MODEL_CONFIG_TABLE_DEF
from .distributed_service_models import (
    INSTANCE_CONFIG_TABLE_DEF,
    SERVICE_STATUS_VIEW_TABLE_DEF,
    SESSION_MAPPING_TABLE_DEF,
    SESSION_AFFINITY_POLICY_TABLE_DEF,
    TENANT_ISOLATION_POLICY_TABLE_DEF,
)
from .physical_resource_models import RESOURCE_CONFIG_TABLE_DEF
from .config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from .template_models import MODEL_TEMPLATE_TABLE_DEF

ALL_TABLE_DEFINITIONS: tuple[TableDefinition, ...] = (
    MODEL_CONFIG_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CHANNEL_CONFIG_TABLE_DEF,
    RESOURCE_CONFIG_TABLE_DEF,
    INSTANCE_CONFIG_TABLE_DEF,
    SERVICE_STATUS_VIEW_TABLE_DEF,
    SESSION_MAPPING_TABLE_DEF,
    TENANT_ISOLATION_POLICY_TABLE_DEF,
    SESSION_AFFINITY_POLICY_TABLE_DEF,
)


async def init_all_tables(handler: DBHandler) -> None:
    """对已连接的 ``handler`` 依次 ``init_table``，幂等（表已存在则跳过创建逻辑）。"""
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
