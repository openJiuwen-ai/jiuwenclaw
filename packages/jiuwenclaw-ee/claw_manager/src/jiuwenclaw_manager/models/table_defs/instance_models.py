"""Claw Manager 纳管表：instance_info、service_instance。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

INSTANCE_INFO_TABLE_DEF = TableDefinition(
    table_name="instance_info",
    columns=[
        ColumnDefinition("jiuwenclaw_id", "string", length=64, primary_key=True, nullable=False),
        ColumnDefinition("jiuwenclaw_name", "string", length=128, nullable=False),
        ColumnDefinition("creator_id", "string", length=64, nullable=False),
        ColumnDefinition("description", "string", length=4096, nullable=True),
        ColumnDefinition("k8s_master_host", "string", length=256, nullable=False),
        ColumnDefinition("k8s_auth_type", "string", length=32, nullable=False),
        ColumnDefinition("k8s_auth_config", "string", length=8192, nullable=False),
        ColumnDefinition("k8s_namespace", "string", length=64, nullable=False),
        ColumnDefinition("status", "string", length=32, nullable=False, default="pending"),
        ColumnDefinition("resource_quota", "json", nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("group_id", "string", length=64, nullable=False, default="default"),
        ColumnDefinition("space_id", "string", length=64, nullable=False, default="default"),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["status"], unique=False),
        IndexDefinition(["created_at"], unique=False),
    ],
)

SERVICE_INSTANCE_TABLE_DEF = TableDefinition(
    table_name="service_instance",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("jiuwenclaw_id", "string", length=64, nullable=False),
        ColumnDefinition("service_id", "string", length=128, nullable=False),
        ColumnDefinition("service_type", "string", length=32, nullable=False),
        ColumnDefinition("component_role", "string", length=32, nullable=False),
        ColumnDefinition("endpoint", "string", length=256, nullable=True),
        ColumnDefinition("manager_id", "string", length=64, nullable=False),
        ColumnDefinition("status", "string", length=32, nullable=False, default="pending"),
        ColumnDefinition("last_heartbeat", "datetime", nullable=True),
        ColumnDefinition("version", "string", length=32, nullable=True),
        ColumnDefinition("capabilities", "json", nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["jiuwenclaw_id", "service_id"], unique=True),
        IndexDefinition(["jiuwenclaw_id"], unique=False),
        IndexDefinition(["status"], unique=False),
    ],
)
