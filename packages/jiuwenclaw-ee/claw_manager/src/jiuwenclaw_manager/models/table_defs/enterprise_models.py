"""企业级配置表（复合主键 jiuwenclaw_id + id，与 Gateway 返回的 id 对齐）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

MODEL_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="model_template",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
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
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)

CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_global_policy",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("default_model", "string", length=128, nullable=True),
        ColumnDefinition("video_model", "string", length=128, nullable=True),
        ColumnDefinition("audio_model", "string", length=128, nullable=True),
        ColumnDefinition("vision_model", "string", length=128, nullable=True),
        ColumnDefinition("channel_ids", "json", nullable=False),
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
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("service_id", "string", length=512, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("default_model", "string", length=128, nullable=True),
        ColumnDefinition("video_model", "string", length=128, nullable=True),
        ColumnDefinition("audio_model", "string", length=128, nullable=True),
        ColumnDefinition("vision_model", "string", length=128, nullable=True),
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
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("agent_id", "string", length=512, nullable=False),
        ColumnDefinition("service_policy_id", "integer", nullable=False),
        ColumnDefinition("priority", "integer", nullable=False, default=0),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("default_model", "string", length=128, nullable=True),
        ColumnDefinition("video_model", "string", length=128, nullable=True),
        ColumnDefinition("audio_model", "string", length=128, nullable=True),
        ColumnDefinition("vision_model", "string", length=128, nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
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
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("user_id", "string", length=512, nullable=True),
        ColumnDefinition("group_id", "string", length=512, nullable=True),
        ColumnDefinition("template_id", "string", length=512, nullable=False),
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
