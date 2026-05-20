"""模型模板 model_template 表定义（id 自增主键；对外引用 template_id UUID）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

MODEL_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="model_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("display_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("model_type", "json", nullable=False),
        ColumnDefinition("model_tags", "json", nullable=True),
        ColumnDefinition("api_base", "string", length=512, nullable=False),
        ColumnDefinition("api_key", "string", length=4096, nullable=False),
        ColumnDefinition("model_id", "string", length=128, nullable=False),
        ColumnDefinition("model_provider", "string", length=64, nullable=False),
        ColumnDefinition("parameters", "json", nullable=True),
        ColumnDefinition("timeout", "integer", nullable=False, default=60),
        ColumnDefinition("retry_count", "integer", nullable=False, default=3),
        ColumnDefinition("enable_streaming", "boolean", nullable=False, default=True),
        ColumnDefinition("enable_function_calling", "boolean", nullable=False, default=True),
        ColumnDefinition("verify_ssl", "boolean", nullable=False, default=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)
