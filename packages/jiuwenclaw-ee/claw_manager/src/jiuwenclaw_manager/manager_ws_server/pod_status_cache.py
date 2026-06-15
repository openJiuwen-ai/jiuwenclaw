"""Gateway 上报的 AgentServer Pod 状态内存缓存。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

_CACHE: dict[str, dict[str, Any]] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_now() -> str:
    return _utc_now().isoformat().replace("+00:00", "Z")


def _parse_snapshot_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def update_pod_status_snapshot(
    jiuwenclaw_id: str,
    *,
    snapshot_time: str | None,
    data: dict[str, Any],
) -> dict[str, Any]:
    """保存某个 Gateway 最近一次上报的 Pod 状态。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        raise ValueError("jiuwenclaw_id is required")
    stored = {
        "source": "gateway_report",
        "jiuwenclaw_id": jid,
        "snapshot_time": snapshot_time or _iso_now(),
        "received_at": _iso_now(),
        **deepcopy(data),
    }
    _CACHE[jid] = stored
    return deepcopy(stored)


def get_pod_status_snapshot(
    jiuwenclaw_id: str,
    *,
    stale_after_seconds: int = 90,
) -> dict[str, Any] | None:
    """读取某个 Gateway 最近一次 Pod 状态快照，并补充 stale 信息。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return None
    snapshot = _CACHE.get(jid)
    if snapshot is None:
        return None

    result = deepcopy(snapshot)
    snapshot_dt = _parse_snapshot_time(result.get("snapshot_time"))
    if snapshot_dt is None:
        snapshot_dt = _parse_snapshot_time(result.get("received_at"))
    if snapshot_dt is None:
        result["stale"] = True
        result["snapshot_age_seconds"] = None
        return result

    age = max(0, int((_utc_now() - snapshot_dt).total_seconds()))
    result["snapshot_age_seconds"] = age
    result["stale"] = age > stale_after_seconds
    return result
