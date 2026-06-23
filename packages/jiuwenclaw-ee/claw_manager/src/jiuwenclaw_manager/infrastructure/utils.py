"""基础设施层通用工具。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_datetime(dt: datetime | None) -> str | None:
    """将 datetime 格式化为 ISO-8601 UTC 字符串（末尾 ``Z``）；``None`` 返回 ``None``。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def new_uuid4() -> str:
    """生成 UUID v4 字符串。"""
    return str(uuid.uuid4())


def strip_optional(value: str | None) -> str | None:
    """去除首尾空白；``None`` 或空白字符串返回 ``None``。"""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def format_ts(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(val)
