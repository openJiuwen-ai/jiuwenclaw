"""从 Agent 请求提取策略匹配上下文。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from jiuwenclaw.schema.agent import AgentRequest


@dataclass(frozen=True)
class RoutingContext:
    jiuwenclaw_id: str
    group_id: str
    bot_id: str
    user_id: str
    service_id: str
    agent_id: str

    def as_dict(self) -> dict[str, str]:
        return {
            "jiuwenclaw_id": self.jiuwenclaw_id,
            "group_id": self.group_id,
            "bot_id": self.bot_id,
            "user_id": self.user_id,
            "service_id": self.service_id,
            "agent_id": self.agent_id,
        }


def _str_field(*candidates: Any) -> str:
    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def routing_context_from_request(request: AgentRequest) -> RoutingContext:
    """解析 ``group_id`` / ``bot_id`` / ``user_id`` 及派生 ``service_id``。"""
    params = request.params if isinstance(request.params, dict) else {}
    metadata = request.metadata if isinstance(request.metadata, dict) else {}

    group_id = _str_field(
        params.get("group_id"),
        metadata.get("group_id"),
        request.chat_id,
    )
    bot_id = _str_field(params.get("bot_id"), metadata.get("bot_id"))
    user_id = _str_field(
        params.get("user_id"),
        metadata.get("user_id"),
    )
    if not user_id and request.permission_context is not None:
        user_id = _str_field(
            request.permission_context.triggering_user_id,
            request.permission_context.principal_user_id,
        )

    service_id = _str_field(request.service_id, params.get("service_id"))
    if not service_id and group_id and bot_id:
        service_id = f"{group_id}::{bot_id}"

    agent_id = _str_field(
        request.agent_id,
        params.get("agent_id"),
        user_id,
    )

    jiuwenclaw_id = os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()

    return RoutingContext(
        jiuwenclaw_id=jiuwenclaw_id,
        group_id=group_id,
        bot_id=bot_id,
        user_id=user_id,
        service_id=service_id,
        agent_id=agent_id,
    )


def routing_context_from_mapping(data: dict[str, Any]) -> RoutingContext:
    jiuwenclaw_id = _str_field(
        data.get("jiuwenclaw_id"),
        os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", ""),
    )
    group_id = _str_field(data.get("group_id"))
    bot_id = _str_field(data.get("bot_id"))
    user_id = _str_field(data.get("user_id"))
    service_id = _str_field(data.get("service_id"))
    if not service_id and group_id and bot_id:
        service_id = f"{group_id}::{bot_id}"
    agent_id = _str_field(data.get("agent_id"), user_id)
    return RoutingContext(
        jiuwenclaw_id=jiuwenclaw_id,
        group_id=group_id,
        bot_id=bot_id,
        user_id=user_id,
        service_id=service_id,
        agent_id=agent_id,
    )
