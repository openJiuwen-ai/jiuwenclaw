# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)

# 自动扩缩容指标缺省：并发度（与库表 autoscale_metrics 列默认值一致）
DEFAULT_AUTOSCALE_METRICS: dict[str, Any] = {"metric": "concurrency"}


class InstanceConfigInfo(BaseModel):
    """instance_config 表行映射（与 INSTANCE_CONFIG_TABLE_DEF 一致）。"""

    id: int
    component: str
    min_replicas: int
    max_replicas: int
    current_replicas: int
    autoscale_enabled: bool
    autoscale_metrics: dict[str, Any]
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


INSTANCE_CONFIG_TABLE_DEF = TableDefinition(
    table_name="instance_config",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("component", "string", length=32, nullable=False),
        ColumnDefinition("min_replicas", "integer", nullable=False),
        ColumnDefinition("max_replicas", "integer", nullable=False),
        ColumnDefinition("current_replicas", "integer", nullable=False),
        ColumnDefinition("autoscale_enabled", "boolean", nullable=False),
        ColumnDefinition(
            "autoscale_metrics",
            "json",
            nullable=False,
            default=DEFAULT_AUTOSCALE_METRICS,
        ),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["component"], unique=True),
    ],
)


class ServiceStatusViewInfo(BaseModel):
    """service_status_view 表行映射（与 SERVICE_STATUS_VIEW_TABLE_DEF 一致）。

    ``cpu_usage`` / ``memory_usage`` 为使用率百分比的小数值（与 DECIMAL 存储一致）。
    """

    pod_name: str
    component: str
    status: str
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    restart_count: int
    start_time: Optional[datetime] = None
    ready: bool
    node_name: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


SERVICE_STATUS_VIEW_TABLE_DEF = TableDefinition(
    table_name="service_status_view",
    columns=[
        ColumnDefinition(
            "pod_name",
            "string",
            length=64,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition("component", "string", length=32, nullable=False),
        ColumnDefinition("status", "string", length=32, nullable=False),
        ColumnDefinition("cpu_usage", "float", nullable=True),
        ColumnDefinition("memory_usage", "float", nullable=True),
        ColumnDefinition("restart_count", "integer", nullable=False, default=0),
        ColumnDefinition("start_time", "datetime", nullable=True),
        ColumnDefinition("ready", "boolean", nullable=False, default=False),
        ColumnDefinition("node_name", "string", length=64, nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["component"], unique=False),
        IndexDefinition(["status"], unique=False),
    ],
)


class SessionMappingInfo(BaseModel):
    """session_mapping 表行映射（与 SESSION_MAPPING_TABLE_DEF 一致）。"""

    session_id: str
    user_id: Optional[str] = None
    group_id: Optional[str] = None
    bot_id: Optional[str] = None
    agent_server_pod: str
    create_time: datetime
    last_active_time: datetime
    ttl: int
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


SESSION_MAPPING_TABLE_DEF = TableDefinition(
    table_name="session_mapping",
    columns=[
        ColumnDefinition(
            "session_id",
            "string",
            length=64,
            primary_key=True,
            nullable=False,
        ),
        ColumnDefinition("user_id", "string", length=64, nullable=True),
        ColumnDefinition("group_id", "string", length=64, nullable=True),
        ColumnDefinition("bot_id", "string", length=64, nullable=True),
        ColumnDefinition("agent_server_pod", "string", length=64, nullable=False),
        ColumnDefinition("create_time", "datetime", nullable=False),
        ColumnDefinition("last_active_time", "datetime", nullable=False),
        ColumnDefinition("ttl", "integer", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["agent_server_pod"], unique=False),
        IndexDefinition(["user_id"], unique=False),
        IndexDefinition(["group_id"], unique=False),
        IndexDefinition(["bot_id"], unique=False),
    ],
)


class TenantIsolationPolicyInfo(BaseModel):
    """tenant_isolation_policy 表行映射（与 TENANT_ISOLATION_POLICY_TABLE_DEF 一致）。"""

    id: int
    policy_name: str
    isolation_level: str
    selector: dict[str, Any]
    target_instances: list[Any] | dict[str, Any]
    resource_quota: Optional[dict[str, Any]] = None
    priority: int
    enabled: bool
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


TENANT_ISOLATION_POLICY_TABLE_DEF = TableDefinition(
    table_name="tenant_isolation_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("policy_name", "string", length=128, nullable=False),
        ColumnDefinition("isolation_level", "string", length=32, nullable=False),
        ColumnDefinition("selector", "json", nullable=False, default={}),
        ColumnDefinition("target_instances", "json", nullable=False, default=[]),
        ColumnDefinition("resource_quota", "json", nullable=True),
        ColumnDefinition("priority", "integer", nullable=False, default=0),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["isolation_level"], unique=False),
        IndexDefinition(["enabled"], unique=False),
        IndexDefinition(["priority"], unique=False),
    ],
)


class SessionAffinityPolicyInfo(BaseModel):
    """session_affinity_policy 表行映射（与 SESSION_AFFINITY_POLICY_TABLE_DEF 一致）。"""

    id: int
    policy_name: str
    affinity_type: str
    session_ttl: int
    max_concurrent_per_session: Optional[int] = None
    failover_enabled: bool
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


SESSION_AFFINITY_POLICY_TABLE_DEF = TableDefinition(
    table_name="session_affinity_policy",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("policy_name", "string", length=128, nullable=False, unique=True),
        ColumnDefinition("affinity_type", "string", length=32, nullable=False),
        ColumnDefinition("session_ttl", "integer", nullable=False),
        ColumnDefinition("max_concurrent_per_session", "integer", nullable=True),
        ColumnDefinition("failover_enabled", "boolean", nullable=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["affinity_type"], unique=False),
        IndexDefinition(["failover_enabled"], unique=False),
    ],
)
