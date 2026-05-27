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


def new_template_id() -> str:
    """生成模板对外引用 UUID（v4）。"""
    return str(uuid.uuid4())


def format_ts(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(val)
