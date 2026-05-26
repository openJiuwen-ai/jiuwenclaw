# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""模板表：model_template、extension_config_template、skill_whitelist_template、
service_config_template（与 Claw Manager 企业级数据模型对齐）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)


class ModelTemplateInfo(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    model_type: Any
    model_tags: list[Any] | None
    api_base: str
    api_key: str
    model_id: str
    model_provider: str
    parameters: dict[str, Any] | None
    timeout: int
    retry_count: int
    enable_streaming: bool
    enable_function_calling: bool
    verify_ssl: bool
    enabled: bool
    data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


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
        ColumnDefinition("template_name", "string", length=128, nullable=False),
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
        IndexDefinition(["enabled"], unique=False),
    ],
)


class ExtensionConfigTemplateInfo(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    component: str
    hook_type: str
    hook_config: dict[str, Any]
    custom_config: dict[str, Any] | None
    enabled: bool
    data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


EXTENSION_CONFIG_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="extension_config_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("component", "string", length=32, nullable=False),
        ColumnDefinition("hook_type", "string", length=32, nullable=False),
        ColumnDefinition("hook_config", "json", nullable=False),
        ColumnDefinition("custom_config", "json", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
        IndexDefinition(["enabled"], unique=False),
        IndexDefinition(["component"], unique=False),
        IndexDefinition(["hook_type"], unique=False),
    ],
)


class SkillWhitelistTemplateInfo(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    skill_id: str
    skill_version: str
    skill_source: str
    enabled: bool
    data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


SKILL_WHITELIST_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="skill_whitelist_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("skill_id", "string", length=512, nullable=False),
        ColumnDefinition("skill_version", "string", length=64, nullable=False),
        ColumnDefinition("skill_source", "string", length=512, nullable=False),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
        IndexDefinition(["enabled"], unique=False),
        IndexDefinition(["skill_id"], unique=False),
    ],
)


class ServiceConfigTemplateInfo(BaseModel):
    id: int
    template_id: str
    template_name: str
    description: str | None
    agent_image: str
    namespace: str
    pod_name: str | None
    container_name: str
    container_port: int
    port_name: str
    image_pull_policy: str
    replicas: int
    kubeconfig: str | None
    agent_runtime: str | None
    readiness_initial_delay: int
    readiness_period: int
    ready_timeout: int
    ready_poll_interval: int
    nfs_server: str | None
    nfs_path: str
    nfs_mount_path: str | None
    host_path: str | None
    host_mount_path: str | None
    mode: str
    node_name: str
    cpu_request: str
    memory_request: str
    cpu_limit: str
    memory_limit: str
    min_idle_services: int
    max_services: int
    service_concurrency: int
    service_ttl: int
    autoscale_interval: str
    message_timeout: int
    session_concurrency: int
    session_ttl: int
    enabled: bool
    data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


SERVICE_CONFIG_TEMPLATE_TABLE_DEF = TableDefinition(
    table_name="service_config_template",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("template_id", "string", length=100, nullable=False),
        ColumnDefinition("template_name", "string", length=128, nullable=False),
        ColumnDefinition("description", "string", length=512, nullable=True),
        ColumnDefinition("agent_image", "string", length=512, nullable=False),
        ColumnDefinition("namespace", "string", length=128, nullable=False),
        ColumnDefinition("pod_name", "string", length=128, nullable=True),
        ColumnDefinition("container_name", "string", length=128, nullable=False),
        ColumnDefinition("container_port", "integer", nullable=False),
        ColumnDefinition("port_name", "string", length=64, nullable=False, default="http"),
        ColumnDefinition(
            "image_pull_policy",
            "string",
            length=32,
            nullable=False,
            default="IfNotPresent",
        ),
        ColumnDefinition("replicas", "integer", nullable=False, default=1),
        ColumnDefinition("kubeconfig", "string", length=512, nullable=True),
        ColumnDefinition("agent_runtime", "string", length=128, nullable=True),
        ColumnDefinition("readiness_initial_delay", "integer", nullable=False, default=5),
        ColumnDefinition("readiness_period", "integer", nullable=False, default=10),
        ColumnDefinition("ready_timeout", "integer", nullable=False, default=300),
        ColumnDefinition("ready_poll_interval", "integer", nullable=False, default=2),
        ColumnDefinition("nfs_server", "string", length=256, nullable=True),
        ColumnDefinition("nfs_path", "string", length=512, nullable=False, default="/"),
        ColumnDefinition("nfs_mount_path", "string", length=512, nullable=True),
        ColumnDefinition("host_path", "string", length=512, nullable=True),
        ColumnDefinition("host_mount_path", "string", length=512, nullable=True),
        ColumnDefinition("mode", "string", length=512, nullable=False, default="product"),
        ColumnDefinition("node_name", "string", length=512, nullable=False, default=""),
        ColumnDefinition("cpu_request", "string", length=32, nullable=False, default="500m"),
        ColumnDefinition("memory_request", "string", length=32, nullable=False, default="512Mi"),
        ColumnDefinition("cpu_limit", "string", length=32, nullable=False, default="1000m"),
        ColumnDefinition("memory_limit", "string", length=32, nullable=False, default="1Gi"),
        ColumnDefinition("min_idle_services", "integer", nullable=False, default=1),
        ColumnDefinition("max_services", "integer", nullable=False, default=10),
        ColumnDefinition("service_concurrency", "integer", nullable=False, default=10),
        ColumnDefinition("service_ttl", "integer", nullable=False, default=30),
        ColumnDefinition(
            "autoscale_interval",
            "string",
            length=32,
            nullable=False,
            default="0.2",
        ),
        ColumnDefinition("message_timeout", "integer", nullable=False, default=300),
        ColumnDefinition("session_concurrency", "integer", nullable=False, default=10),
        ColumnDefinition("session_ttl", "integer", nullable=False, default=20),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["template_id"], unique=True),
        IndexDefinition(["enabled"], unique=False),
        IndexDefinition(["namespace"], unique=False),
    ],
)
