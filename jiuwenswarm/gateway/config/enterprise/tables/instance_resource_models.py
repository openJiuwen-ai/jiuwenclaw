# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""实例资源表：instance_agent_resource（Gateway 本地副本；字段对齐 Manager，按 jiuwenclaw_id 隔离）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

INSTANCE_AGENT_RESOURCE_TABLE_DEF = TableDefinition(
    table_name="instance_agent_resource",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("resource_id", "string", length=100, nullable=False),
        ColumnDefinition("resource_name", "string", length=128, nullable=False),
        ColumnDefinition("resource_desc", "string", length=512, nullable=True),
        ColumnDefinition("ref_template_id", "string", length=100, nullable=False),
        ColumnDefinition("match_expr", "json", nullable=True),
        ColumnDefinition("granted_by", "string", length=64, nullable=True),
        ColumnDefinition("expires_at", "datetime", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "resource_id"], unique=True),
    ],
)

__all__ = ("INSTANCE_AGENT_RESOURCE_TABLE_DEF",)
