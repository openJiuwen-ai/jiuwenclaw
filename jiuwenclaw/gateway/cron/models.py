from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CronTargetChannel(str, Enum):
    """推送频道枚举。"""

    WEB = "web"
    FEISHU = "feishu"
    # XIAOYI = "xiaoyi"
    # DINGTALK = "dingtalk"


def _normalize_targets_str(raw: str) -> str:
    """将 targets 字符串规范为 CronTargetChannel 枚举值，非法则默认 web。"""
    s = (raw or "").strip() or "web"
    try:
        CronTargetChannel(s)
        return s
    except ValueError:
        return CronTargetChannel.WEB.value


@dataclass(frozen=True)
class CronTarget:
    """Where to push cron results."""

    channel_id: str
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "session_id": self.session_id,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CronTarget":
        channel_id = str(data.get("channel_id") or "").strip()
        session_id_raw = data.get("session_id", None)
        session_id = str(session_id_raw).strip() if isinstance(session_id_raw, str) else None
        if not channel_id:
            raise ValueError("target.channel_id is required")
        return CronTarget(channel_id=channel_id, session_id=session_id or None)


@dataclass
class CronJob:
    """Cron job persisted in cron_jobs.json."""

    id: str
    name: str
    enabled: bool
    cron_expr: str
    timezone: str
    wake_offset_seconds: int = 300
    description: str = ""
    # Target channel ID to push results to (e.g. "web").
    # JSON 字段名仍然叫 targets，用字符串保存频道 ID，兼容旧数据。
    targets: str = ""
    created_at: float | None = None
    updated_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "enabled": bool(self.enabled),
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "wake_offset_seconds": int(self.wake_offset_seconds),
            "description": self.description,
            "targets": self.targets,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CronJob":
        job_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        cron_expr = str(data.get("cron_expr") or "").strip()
        timezone = str(data.get("timezone") or "").strip()
        enabled = bool(data.get("enabled", False))

        wake_offset_seconds_raw = data.get("wake_offset_seconds", 300)
        try:
            wake_offset_seconds = int(wake_offset_seconds_raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("wake_offset_seconds must be int") from exc
        if wake_offset_seconds < 0:
            wake_offset_seconds = 0

        description = str(data.get("description") or "")

        # targets 新格式是字符串；旧格式是 list[dict]，此处做兼容。
        targets_raw = data.get("targets", "")
        targets_str = ""
        if isinstance(targets_raw, str):
            targets_str = targets_raw.strip()
        elif isinstance(targets_raw, list):
            # legacy: list of {channel_id, session_id?}
            for item in targets_raw:
                if isinstance(item, dict):
                    ch = str(item.get("channel_id") or "").strip()
                    if ch:
                        targets_str = ch
                        break

        created_at = data.get("created_at", None)
        updated_at = data.get("updated_at", None)
        created_at_f = float(created_at) if isinstance(created_at, (int, float)) else None
        updated_at_f = float(updated_at) if isinstance(updated_at, (int, float)) else None

        if not job_id:
            raise ValueError("id is required")
        if not name:
            raise ValueError("name is required")
        if not cron_expr:
            raise ValueError("cron_expr is required")
        if not timezone:
            raise ValueError("timezone is required")
        if not targets_str:
            raise ValueError("targets is required")

        targets_str = _normalize_targets_str(targets_str)

        return CronJob(
            id=job_id,
            name=name,
            enabled=enabled,
            cron_expr=cron_expr,
            timezone=timezone,
            wake_offset_seconds=wake_offset_seconds,
            description=description,
            targets=targets_str,
            created_at=created_at_f,
            updated_at=updated_at_f,
        )


@dataclass
class CronRunState:
    """In-memory state for a single scheduled run (not persisted)."""

    run_id: str
    job_id: str
    wake_at_iso: str
    push_at_iso: str
    status: str = "pending"  # pending|running|succeeded|failed
    placeholder_sent: bool = False
    pushed_final: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    result_text: str | None = None
    error: str | None = None