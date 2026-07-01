"""通用工具：UTC 时间、ID、字符串处理。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_datetime(dt: datetime | None) -> str | None:
    """datetime → ISO-8601 UTC 字符串（末尾 Z）；None 返回 None。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    return str(uuid.uuid4())


def new_uuid4() -> str:
    return str(uuid.uuid4())


def strip_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None
