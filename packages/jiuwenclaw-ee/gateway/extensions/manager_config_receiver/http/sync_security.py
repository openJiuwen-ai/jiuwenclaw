# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager → Gateway 同步请求信封解析（仅 revision，无签密）。"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

_ENVELOPE_KEYS = frozenset({"revision", "sig", "enc"})


def split_envelope(body: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """拆出 revision 与业务字段；忽略历史 ``sig`` / ``enc``（若仍被传入）。"""
    revision = str(body.get("revision") or "")
    if not revision:
        raise HTTPException(status_code=400, detail="revision is required")
    business = {k: v for k, v in body.items() if k not in _ENVELOPE_KEYS}
    return revision, business
