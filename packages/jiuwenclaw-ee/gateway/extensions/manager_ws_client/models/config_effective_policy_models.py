# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""配置生效策略与默认模板映射表（与 Claw Manager 企业级数据模型对齐）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_global_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id"], unique=True),
        IndexDefinition(["enabled"], unique=False),
    ],
)


CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_service_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("service_id", "string", length=512, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)


CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_agent_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("agent_id", "string", length=512, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("service_policy_id", "integer", nullable=False),
        ColumnDefinition("priority", "integer", nullable=False, default=0),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("send_file_allowed", "boolean", nullable=False, default=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("send_file_allowed", "boolean", nullable=False, default=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["service_policy_id"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)


CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF = TableDefinition(
    table_name="config_default_template_mapping",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("user_id", "string", length=512, nullable=True),
        ColumnDefinition("group_id", "string", length=512, nullable=True),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_type", "string", length=512, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["user_id"], unique=False),
        IndexDefinition(["group_id"], unique=False),
        IndexDefinition(["template_type"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)
