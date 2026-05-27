"""应用配置表定义：channel_config。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

_CHANNEL_CONFIG_TABLE_DEF = TableDefinition(
    table_name="channel_config",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
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
        IndexDefinition(["jiuwenclaw_id", "channel_id"], unique=True),
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["status"], unique=False),
    ],
)
