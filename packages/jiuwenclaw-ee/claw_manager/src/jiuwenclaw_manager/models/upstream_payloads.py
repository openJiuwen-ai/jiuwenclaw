"""与 ``jiuwenclaw.extensions.agent_client.schemas`` 对齐的请求体，供 Manager 转发 HTTP 使用（避免本包安装时强依赖 jiuwenclaw 源码树）。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelConfigCreateBody(BaseModel):
    """对应 ``ModelConfigCreateRequest``。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    model_name: str = Field(..., min_length=1)
    model_type: str = Field(..., min_length=1)
    api_endpoint: str = Field(..., min_length=1)
    api_key_ref: str = Field(..., min_length=1)
    parameters: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    enabled: bool = True
    data: dict[str, Any] | None = None


class ModelConfigUpdateBody(BaseModel):
    """对应 ``ModelConfigUpdateRequest``。"""

    model_name: str | None = None
    model_type: str | None = None
    api_endpoint: str | None = None
    api_key_ref: str | None = None
    parameters: dict[str, Any] | None = None
    rate_limit: dict[str, Any] | None = None
    enabled: bool | None = None
    data: dict[str, Any] | None = None


class ChannelConfigCreateBody(BaseModel):
    """对应 ``ChannelConfigCreateRequest``。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    channel_id: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1)
    channel_type: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)
    config: dict[str, Any] | None = None
    status: Literal["active", "inactive"] = "active"


class AgentServerConfigUpdateBody(BaseModel):
    """对应 ``AgentServerConfigUpdateRequest``。"""

    min_replicas: int | None = None
    max_replicas: int | None = None
    autoscale_enabled: bool | None = None
    autoscale_metrics: dict[str, Any] | None = None


class TenantIsolationPolicyUpdateBody(BaseModel):
    """对应 ``TenantIsolationPolicyUpdateRequest``。"""

    policy_name: str | None = None
    isolation_level: str | None = None
    selector: dict[str, Any] | None = None
    target_instances: list[str] | dict[str, Any] | None = None
    resource_quota: dict[str, Any] | None = None
    priority: int | None = None
    enabled: bool | None = None


class SessionAffinityPolicyUpdateBody(BaseModel):
    """对应 ``SessionAffinityPolicyUpdateRequest``。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., min_length=1)
    affinity_type: Literal["user_id", "session_id", "ip"] | None = None
    session_ttl: int | None = Field(default=None, gt=0)
    max_concurrent_per_session: int | None = Field(default=None, gt=0)
    failover_enabled: bool | None = None


class ResourceConfigUpdateBody(BaseModel):
    """对应 ``ResourceConfigUpdateRequest``。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    component: str = Field(..., min_length=1)
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    storage_request: str | None = None
