"""``jiuwenclaw_id`` 校验（独立模块，避免与 ``common`` / ``schemas`` 循环引用）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.instance.instance_service import get_instance_row


async def validate_jiuwenclaw_id(handler: DBHandler, jiuwenclaw_id: str) -> str:
    """校验并规范化 ``jiuwenclaw_id``；实例不存在时抛出 ``ValueError``。"""
    normalized = jiuwenclaw_id.strip()
    if not normalized:
        raise ValueError("jiuwenclaw_id is required")
    inst = await get_instance_row(handler, normalized)
    if inst is None:
        raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
    return normalized
