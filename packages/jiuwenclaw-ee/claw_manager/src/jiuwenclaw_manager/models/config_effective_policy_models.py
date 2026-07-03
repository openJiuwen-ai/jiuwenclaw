"""企业级配置策略表定义（复合主键 id + jiuwenclaw_id，与 Gateway 返回的 id 对齐）。"""

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
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("policy_id", "string", length=100, nullable=False),
        ColumnDefinition("policy_name", "string", length=128, nullable=False),
        ColumnDefinition("policy_desc", "string", length=512, nullable=True),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "policy_id"], unique=True),
    ],
)

CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_service_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("policy_id", "string", length=100, nullable=False),
        ColumnDefinition("policy_name", "string", length=128, nullable=False),
        ColumnDefinition("policy_desc", "string", length=512, nullable=True),
        ColumnDefinition("service_id", "string", length=512, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "policy_id"], unique=True),
    ],
)

CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF = TableDefinition(
    table_name="config_effective_agent_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("policy_id", "string", length=100, nullable=False),
        ColumnDefinition("policy_name", "string", length=128, nullable=False),
        ColumnDefinition("policy_desc", "string", length=512, nullable=True),
        ColumnDefinition("agent_id", "string", length=512, nullable=False),
        ColumnDefinition("service_policy_id", "string", length=100, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False, default=0),
        ColumnDefinition("match_expr", "string", length=8192, nullable=True),
        ColumnDefinition("template_ref", "json", nullable=False),
        ColumnDefinition("send_file_allowed", "boolean", nullable=False, default=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "policy_id"], unique=True),
    ],
)

CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF = TableDefinition(
    table_name="config_default_template_mapping",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=False,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("policy_id", "string", length=100, nullable=False),
        ColumnDefinition("policy_name", "string", length=128, nullable=False),
        ColumnDefinition("policy_desc", "string", length=512, nullable=True),
        ColumnDefinition("scope_type", "string", length=32, nullable=False),
        ColumnDefinition("scope_id", "string", length=512, nullable=False),
        ColumnDefinition("priority", "integer", nullable=False),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_type", "string", length=512, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "policy_id"], unique=True),
    ],
)
