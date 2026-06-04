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

LOG_MASKING_RULE_TABLE_DEF = TableDefinition(
    table_name="log_masking_rule",
    columns=[
        ColumnDefinition("id", "integer", primary_key=True, autoincrement=True, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("rule_id", "string", length=64, nullable=False),
        ColumnDefinition("rule_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("pattern", "string", length=512, nullable=False),
        ColumnDefinition(
            "replacement",
            "string",
            length=64,
            nullable=False,
            default="******",
        ),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("source", "string", length=16, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "rule_id"], unique=True),
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["enabled"], unique=False),
        IndexDefinition(["priority"], unique=False),
    ],
)
