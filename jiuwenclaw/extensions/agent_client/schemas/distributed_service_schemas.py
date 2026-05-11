from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentServerConfigUpdateRequest(BaseModel):
    min_replicas: int | None = None
    max_replicas: int | None = None
    autoscale_enabled: bool | None = None
    autoscale_metrics: dict[str, Any] | None = None


class InstanceConfigRecord(BaseModel):
    id: int
    component: str = Field(default="agent_server")
    min_replicas: int
    max_replicas: int
    current_replicas: int
    autoscale_enabled: bool
    autoscale_metrics: dict[str, Any] = Field(default_factory=dict)
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | str
    updated_at: datetime | str


class SessionMappingListQueryRequest(BaseModel):
    user_id: str | None = None
    group_id: str | None = None
    bot_id: str | None = None
    session_id: str | None = None
    page_num: int = Field(default=1, ge=1)
    page_size: int = Field(default=10, ge=1, le=200)


class TenantIsolationPolicyUpdateRequest(BaseModel):
    policy_name: str | None = None
    isolation_level: str | None = None
    selector: dict[str, Any] | None = None
    target_instances: list[str] | dict[str, Any] | None = None
    resource_quota: dict[str, Any] | None = None
    priority: int | None = None
    enabled: bool | None = None


class SessionAffinityPolicyUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    policy_name: str = Field(..., min_length=1, description="策略名称，必填")

    affinity_type: Literal["user_id", "session_id", "ip"] | None = Field(
        default=None,
        description="亲和维度，传入时需为 user_id / session_id / ip 之一",
    )

    session_ttl: int | None = Field(
        default=None,
        gt=0,
        description="会话 TTL（秒），传入时需大于 0",
    )

    max_concurrent_per_session: int | None = Field(
        default=None,
        gt=0,
        description="单会话最大并发数，传入时需大于 0",
    )

    failover_enabled: bool | None = Field(
        default=None,
        description="是否启用故障转移（新建记录时须在请求体中给出）",
    )
