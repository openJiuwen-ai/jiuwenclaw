# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""OpenAbility WebSocket 外层帧：sandboxId + msgDetail（内层 E2A JSON 字符串）。"""

from __future__ import annotations

import json
from typing import Any


def wrap_openability_message(sandbox_id: str, detail: dict[str, Any]) -> dict[str, Any]:
    """将内层 E2A 线 dict 封装为 OpenAbility 外层帧。"""
    sid = str(sandbox_id or "").strip()
    if not sid:
        raise ValueError("wrap_openability_message: sandbox_id is required")
    return {
        "sandboxId": sid,
        "msgDetail": json.dumps(detail, ensure_ascii=False),
    }


def _parse_msg_detail(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValueError("unwrap_openability_message: msgDetail is empty")
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("unwrap_openability_message: msgDetail JSON must be an object")
        return parsed
    raise ValueError(
        f"unwrap_openability_message: msgDetail must be str or dict, got {type(raw).__name__}"
    )


def unwrap_openability_message(
    data: dict[str, Any],
    *,
    expected_sandbox_id: str | None = None,
) -> dict[str, Any]:
    """解析 OpenAbility 外层帧，返回内层 E2A 线 dict。"""
    if not isinstance(data, dict):
        raise ValueError("unwrap_openability_message: frame must be a dict")

    sandbox_id = data.get("sandboxId")
    if sandbox_id is None:
        sandbox_id = data.get("sandbox_id")
    sid = str(sandbox_id or "").strip()
    if not sid:
        raise ValueError("unwrap_openability_message: sandboxId is required")

    if expected_sandbox_id is not None:
        expected = str(expected_sandbox_id).strip()
        if expected and sid != expected:
            raise ValueError(
                f"unwrap_openability_message: sandboxId mismatch "
                f"(expected={expected!r}, got={sid!r})"
            )

    if "msgDetail" not in data:
        raise ValueError("unwrap_openability_message: msgDetail is required")

    return _parse_msg_detail(data["msgDetail"])
