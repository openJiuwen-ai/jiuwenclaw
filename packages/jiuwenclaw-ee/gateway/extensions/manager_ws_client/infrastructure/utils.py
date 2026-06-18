# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 扩展基础设施工具函数。"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

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
    """写入 ``JIUWENCLAW_ID`` 环境变量。"""
    if jiuwenclaw_id is None:
        os.environ.pop("JIUWENCLAW_ID", None)
        return
    normalized = str(jiuwenclaw_id).strip()
    if normalized:
        os.environ["JIUWENCLAW_ID"] = normalized
    else:
        os.environ.pop("JIUWENCLAW_ID", None)


def get_jiuwenclaw_id() -> str | None:
    """从 ``JIUWENCLAW_ID`` 环境变量读取当前实例 id。"""
    val = os.getenv("JIUWENCLAW_ID", "").strip()
    return val or None


def assert_jiuwenclaw_id_matches(jiuwenclaw_id: str) -> str:
    """校验 config.push 顶层 ``jiuwenclaw_id`` 与已注册实例一致，并返回有效 id。"""
    if not jiuwenclaw_id:
        raise ValueError("config.push payload requires jiuwenclaw_id")
    registered = get_jiuwenclaw_id()
    if registered and jiuwenclaw_id != registered:
        raise ValueError(
            f"jiuwenclaw_id mismatch: push={jiuwenclaw_id!r} registered={registered!r}"
        )
    jid = registered or jiuwenclaw_id
    if not jid:
        raise ValueError(
            "jiuwenclaw_id is not set; manager ws register.ack required"
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


def parse_iso_datetime(value: Any) -> Any:
    """将 ISO 8601 字符串解析为 ``datetime``；已是 ``datetime`` 或空值则原样返回。"""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        text = value.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    return value


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
    """PATCH 含 ``template_ref`` 时整列替换（与 Manager / 前端编辑器一致）。"""
    payload = dict(updates)
    if "template_ref" not in payload:
        return payload
    patch = payload.pop("template_ref")
    payload["template_ref"] = normalize_template_ref(patch)
    return payload
