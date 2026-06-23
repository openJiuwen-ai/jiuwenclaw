"""基础设施层通用工具：路由标识、列表排序。"""

from __future__ import annotations

import re
from typing import Collection

# --- routing_id ---

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


# --- list_order ---

# order_by 元组第二项为 is_desc（True=降序），与 SQLAlchemyHandler.list_records 一致。
DEFAULT_TEMPLATE_ORDER_BY: list[tuple[str, bool]] = [("updated_at", True)]

DEFAULT_POLICY_ORDER_BY: list[tuple[str, bool]] = [
    ("priority", False),
    ("updated_at", True),
]

_TIE_BREAKER_FIELD = "updated_at"


def resolve_order_by(
    sort_by: str | None,
    sort_order: str | None,
    *,
    allowed_sort_fields: Collection[str],
    default_order_by: list[tuple[str, bool]] | None = None,
) -> list[tuple[str, bool]]:
    defaults = list(
        default_order_by if default_order_by is not None else DEFAULT_TEMPLATE_ORDER_BY
    )
    field = (sort_by or "").strip()
    order = (sort_order or "").strip().lower()
    if not field or not order:
        return defaults
    if field not in allowed_sort_fields or order not in {"asc", "desc"}:
        return defaults
    is_desc = order == "desc"
    if field == _TIE_BREAKER_FIELD:
        return [(field, is_desc)]
    return [(field, is_desc), (_TIE_BREAKER_FIELD, is_desc)]
