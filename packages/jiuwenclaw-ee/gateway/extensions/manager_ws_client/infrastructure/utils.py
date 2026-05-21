# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager WS Client 通用工具函数。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def utc_now() -> datetime:
    """返回当前 UTC 时间（带 ``timezone.utc`` 的 aware ``datetime``）。"""
    return datetime.now(timezone.utc)


def format_ts(val: Any) -> str:
    """将数据库/ORM 时间值格式化为带时区偏移的 ISO 8601 字符串。"""
    if val is None:
        return ""
    if isinstance(val, datetime):
        dt = val
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    return str(val)
