# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

CHANNEL_CONFIG_TABLE_DEF = TableDefinition(
    table_name="channel_config",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("channel_id", "string", length=64, nullable=False),
        ColumnDefinition("channel_name", "string", length=128, nullable=False),
        ColumnDefinition("channel_type", "string", length=32, nullable=False),
        ColumnDefinition("bot_id", "string", length=64, nullable=False),
        ColumnDefinition("config", "json", nullable=True),
        ColumnDefinition("status", "string", length=32, nullable=False),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["channel_id"], unique=True),
        IndexDefinition(["channel_type"], unique=False),
        IndexDefinition(["bot_id"], unique=False),
        IndexDefinition(["status"], unique=False),
    ],
)
