# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 扩展基础设施工具函数。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

_jiuwenclaw_id: str | None = None

KNOWN_SLOT_KEYS = frozenset({
    "default_model",
    "video_model",
    "audio_model",
    "vision_model",
    "skill_whitelist",
    "extension_config",
    "service_config",
})


def set_jiuwenclaw_id(jiuwenclaw_id: str | None) -> None:
    """由 ``manager_ws_client`` 在 register.ack 成功后写入当前实例 id。"""
    global _jiuwenclaw_id
    if jiuwenclaw_id is None:
        _jiuwenclaw_id = None
        return
    normalized = str(jiuwenclaw_id).strip()
    _jiuwenclaw_id = normalized or None


def resolve_jiuwenclaw_id() -> str | None:
    """返回 Manager WS 建联成功后下发的 ``jiuwenclaw_id``；未建联时为 ``None``。"""
    return _jiuwenclaw_id


def require_jiuwenclaw_id(*, payload: dict[str, Any] | None = None) -> str:
    """解析当前实例 ``jiuwenclaw_id``；分布式场景可回退到 push payload 中的字段。"""
    jid = resolve_jiuwenclaw_id()
    if jid:
        return jid
    if payload is not None:
        payload_jid = str(payload.get("jiuwenclaw_id") or "").strip()
        if payload_jid:
            return payload_jid
    raise ValueError(
        "jiuwenclaw_id is not set; manager ws register.ack required "
        "or provide jiuwenclaw_id in payload"
    )


def assert_jiuwenclaw_id_matches_payload(payload: dict[str, Any]) -> str:
    """校验 push payload 与已注册 ``jiuwenclaw_id`` 一致，并返回有效 id。"""
    jid = require_jiuwenclaw_id(payload=payload)
    payload_jid = str(payload.get("jiuwenclaw_id") or "").strip()
    registered = resolve_jiuwenclaw_id()
    if registered and payload_jid and payload_jid != registered:
        raise ValueError(
            f"jiuwenclaw_id mismatch: push={payload_jid!r} registered={registered!r}"
        )
    return jid


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


def normalize_template_ref(value: Any) -> dict[str, list[str]]:
    """将 ``template_ref`` 规范为 ``{slot: [ref_string, ...]}``；空值键省略。"""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("template_ref must be a JSON object")
    out: dict[str, list[str]] = {}
    for key, raw in value.items():
        slot = str(key).strip()
        if not slot or raw is None:
            continue
        if not isinstance(raw, list):
            raise ValueError(f"template_ref[{slot!r}] must be a list")
        refs = [
            str(item).strip()
            for item in raw
            if item is not None and str(item).strip()
        ]
        if refs:
            out[slot] = refs
    return out


def merge_template_ref(*layers: dict[str, list[str]]) -> dict[str, list[str]]:
    """按参数顺序从左到右合并 ``template_ref``；后出现的槽位整组覆盖先前的同名槽位。"""
    merged: dict[str, list[str]] = {}
    for layer in layers:
        merged.update(layer)
    return merged


def fill_missing_template_ref_slots(
    merged: dict[str, list[str]],
    fallback: dict[str, list[str]],
) -> dict[str, list[str]]:
    """将 ``fallback`` 中尚未出现在 ``merged`` 的槽位补入（用于全局兜底按槽位回填）。"""
    if not fallback:
        return merged
    out = dict(merged)
    for slot, refs in fallback.items():
        if slot not in out:
            out[slot] = refs
    return out


def read_template_ref_from_row(row: Any) -> dict[str, list[str]]:
    raw = getattr(row, "template_ref", None)
    if isinstance(raw, dict):
        return normalize_template_ref(raw)
    return {}


def apply_template_ref_to_updates(
    updates: dict[str, Any],
    *,
    existing_row: Any | None,
) -> dict[str, Any]:
    payload = dict(updates)
    if "template_ref" not in payload:
        return payload
    patch = payload.pop("template_ref")
    base = read_template_ref_from_row(existing_row) if existing_row is not None else {}
    payload["template_ref"] = merge_template_ref(base, normalize_template_ref(patch))
    return payload
