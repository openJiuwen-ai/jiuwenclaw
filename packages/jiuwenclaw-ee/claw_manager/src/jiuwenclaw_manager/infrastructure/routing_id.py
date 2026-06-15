"""service_id / agent_id 路由标识校验（与 Gateway substitute_template 约定一致）。"""

from __future__ import annotations

import re

_ROUTING_PLACEHOLDER = re.compile(r"\$\{(user_id|group_id|bot_id)\}")

ROUTING_ID_VALIDATION_ERROR = (
    "invalid routing id placeholder: only ${user_id}, ${group_id}, ${bot_id} "
    "are allowed when $ is present"
)


def validate_routing_id(value: str | None) -> str:
    """校验路由标识；含 ``$`` 时仅允许 ``${user_id}`` / ``${group_id}`` / ``${bot_id}``。"""
    if value is None:
        raise ValueError("routing id is required")
    text = str(value).strip()
    if not text:
        raise ValueError("routing id cannot be empty")
    if "$" not in text:
        return text
    remainder = _ROUTING_PLACEHOLDER.sub("", text)
    if "$" in remainder:
        raise ValueError(ROUTING_ID_VALIDATION_ERROR)
    return text


def coerce_routing_id(value: str | None) -> str:
    return validate_routing_id(value)


def coerce_routing_id_optional(value: str | None) -> str | None:
    if value is None:
        return None
    return validate_routing_id(value)
