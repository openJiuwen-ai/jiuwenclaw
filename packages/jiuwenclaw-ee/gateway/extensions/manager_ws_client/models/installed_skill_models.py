# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""企业已安装技能账本 ``installed_skill``（按 ``jiuwenclaw_id`` 实例隔离）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

INSTALLED_SKILL_TABLE_DEF = TableDefinition(
    table_name="installed_skill",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("group_id", "string", length=256, nullable=True),
        ColumnDefinition("bot_id", "string", length=256, nullable=True),
        ColumnDefinition("user_id", "string", length=256, nullable=True),
        ColumnDefinition("service_id", "string", length=128, nullable=False),
        ColumnDefinition("agent_id", "string", length=128, nullable=False),
        ColumnDefinition("skill_name", "string", length=256, nullable=False),
        ColumnDefinition("source_type", "string", length=32, nullable=False),
        ColumnDefinition("skill_source", "string", length=2048, nullable=True),
        ColumnDefinition("skill_version", "string", length=128, nullable=True),
        ColumnDefinition("db_skill_id", "string", length=128, nullable=True),
        ColumnDefinition("installed_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
    ],
    indexes=[
        IndexDefinition(
            ["jiuwenclaw_id", "service_id", "agent_id", "skill_name"],
            unique=True,
        ),
        IndexDefinition(["jiuwenclaw_id", "service_id", "agent_id"]),
        IndexDefinition(["jiuwenclaw_id", "service_id", "agent_id", "source_type"]),
    ],
)

__all__ = ("INSTALLED_SKILL_TABLE_DEF",)
