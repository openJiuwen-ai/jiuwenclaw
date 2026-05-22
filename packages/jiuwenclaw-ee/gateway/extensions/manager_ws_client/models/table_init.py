# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager WS Client 同步所需的表初始化。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.db.table_def import TableDefinition

from .application_config_models import CHANNEL_CONFIG_TABLE_DEF
from .config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from .template_models import (
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
)

ALL_TABLE_DEFINITIONS: tuple[TableDefinition, ...] = (
    MODEL_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    CHANNEL_CONFIG_TABLE_DEF,
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
)


async def init_all_tables(handler: DBHandler) -> None:
    """对已连接的 ``handler`` 依次 ``init_table``，幂等（表已存在则跳过创建逻辑）。"""
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
