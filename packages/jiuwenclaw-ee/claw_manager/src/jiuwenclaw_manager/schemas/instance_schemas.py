"""实例管理 API 请求/响应模型。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateInstanceBody(BaseModel):
    jiuwenclaw_name: str = Field(..., max_length=128)
    description: str | None = None
    k8s_master_host: str
    k8s_auth_type: str
    k8s_auth_config: dict[str, Any]
    k8s_namespace: str
    resource_quota: dict[str, Any] | None = None
    creator_id: str = Field(default="system", max_length=64)
    group_id: str = Field(default="default", max_length=64)
    space_id: str = Field(default="default", max_length=64)
    # 指向网关侧 agent_client_rest 根地址，如 http://127.0.0.1:18080（与 config.yaml extensions.agent_client_rest 一致）
    management_api_base: str | None = None


class InstanceUpdateBody(BaseModel):
    """更新 instance_info（未传字段不修改）。

    ``status`` / ``last_heartbeat`` 由 Gateway WebSocket 心跳维护，不可通过本接口修改。
    """

    jiuwenclaw_name: str | None = Field(default=None, max_length=128)
    description: str | None = Field(default=None, max_length=4096)
    k8s_master_host: str | None = Field(default=None, max_length=256)
    k8s_auth_type: str | None = Field(default=None, max_length=32)
    k8s_auth_config: dict[str, Any] | None = None
    k8s_namespace: str | None = Field(default=None, max_length=64)
    resource_quota: dict[str, Any] | None = None
    group_id: str | None = Field(default=None, max_length=64)
    space_id: str | None = Field(default=None, max_length=64)
    data: dict[str, Any] | None = None


class ProvisionLocalInstanceBody(BaseModel):
    """本地拉起 Gateway + AgentServer（需 MANAGER_ALLOW_LOCAL_PROVISION=true）。"""

    jiuwenclaw_name: str = Field(default="local-instance", max_length=128)
    creator_id: str = Field(default="system", max_length=64)
    description: str | None = None


class InstanceSummary(BaseModel):
    jiuwenclaw_id: str
    jiuwenclaw_name: str
    status: str
    k8s_namespace: str
    group_id: str
    space_id: str
    created_at: str | None = None
    last_heartbeat: str | None = None
    updated_at: str | None = None


class InstanceDetail(InstanceSummary):
    description: str | None
    k8s_master_host: str
    k8s_auth_type: str
    resource_quota: dict[str, Any] | None
    data: dict[str, Any] | None
