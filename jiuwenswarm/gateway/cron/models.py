from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from jiuwenswarm.gateway.cron.cron_expr import validate_cron_expression


class CronTargetChannel(str, Enum):
    """推送频道枚举。"""

    WEB = "web"
    TUI = "tui"
    FEISHU = "feishu"
    WHATSAPP = "whatsapp"
    WECOM = "wecom"
    XIAOYI = "xiaoyi"
    WECHAT = "wechat"
    DINGTALK = "dingtalk"


def _feishu_enterprise_app_id(s: str) -> str:
    """feishu_enterprise 通道键仅为 feishu_enterprise:<app_id>；忽略 :chat: 等后续后缀。"""
    parts = str(s or "").strip().split(":")
    if len(parts) < 2 or parts[0].strip().lower() != "feishu_enterprise":
        return ""
    return parts[1].strip()


def is_valid_target_channel_id(raw: str) -> bool:
    s = str(raw or "").strip()
    if not s:
        return False
    if s.startswith("feishu_enterprise:"):
        return bool(_feishu_enterprise_app_id(s))
    try:
        CronTargetChannel(s.lower())
        return True
    except ValueError:
        return False


def normalize_target_channel_id(raw: str, *, default: str = CronTargetChannel.WEB.value) -> str:
    s = str(raw or "").strip()
    if not s:
        return default
    if s.startswith("feishu_enterprise:"):
        app_id = _feishu_enterprise_app_id(s)
        if app_id:
            return f"feishu_enterprise:{app_id}"
        return default
    low = s.lower()
    try:
        return CronTargetChannel(low).value
    except ValueError:
        return default


def _normalize_targets_str(raw: str) -> str:
    """将 targets 字符串规范为 CronTargetChannel 枚举值，非法则默认 web。"""
    return normalize_target_channel_id(raw, default=CronTargetChannel.WEB.value)


# Cron job execution modes (passed to AgentServer as chat.send params["mode"]).
CRON_JOB_MODES: frozenset[str] = frozenset(
    {
        "agent",       # default → agent.plan at runtime
        "plan",        # legacy shorthand (AgentServer resolves separately)
        "team",        # multi-agent team mode
        "agent.plan",
        "agent.fast",
        "team.plan",
        "code.team",
        # 不走 chat.send，scheduler 消费时直接发 PROACTIVE_TICK WS 请求
        # 触发 AgentServer ProactiveEngine.tick_now()。由 proactive_cron_sync 自动注册。
        "proactive.tick",
    }
)


# Canonical default when create/update/runtime do not specify mode.
CRON_JOB_DEFAULT_MODE: str = "agent.fast"


def normalize_cron_job_mode(raw: Any, *, default: str = CRON_JOB_DEFAULT_MODE) -> str:
    """Normalize and validate a cron job execution mode (strict, for create/update APIs)."""
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if not value:
        return default
    if value not in CRON_JOB_MODES:
        raise ValueError(
            f"Invalid cron job mode {raw!r}. "
            f"Valid: {', '.join(sorted(CRON_JOB_MODES))}"
        )
    return value


def coerce_cron_job_mode(raw: Any, *, default: str = CRON_JOB_DEFAULT_MODE) -> str:
    """Normalize mode for runtime/persistence; unknown values pass through lowercased."""
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if not value:
        return default
    if value in CRON_JOB_MODES:
        return value
    return value


def cron_job_modes_for_tools() -> list[str]:
    return sorted(CRON_JOB_MODES)


def cron_job_metadata() -> dict[str, str | list[str] | int]:
    """Cron job schema for clients (TUI/Web); single source for supported modes."""
    return {
        "modes": cron_job_modes_for_tools(),
        "default_mode": CRON_JOB_DEFAULT_MODE,
        "default_timeout_seconds": CRON_DEFAULT_TIMEOUT_SECONDS,
        "default_team_timeout_seconds": CRON_TEAM_DEFAULT_TIMEOUT_SECONDS,
        "max_timeout_seconds": CRON_MAX_TIMEOUT_SECONDS,
    }


_TEAM_CRON_MODES: frozenset[str] = frozenset({"team", "team.plan", "code.team"})

CRON_DEFAULT_TIMEOUT_SECONDS: int = 10 * 60
CRON_TEAM_DEFAULT_TIMEOUT_SECONDS: int = 20 * 60
CRON_MAX_TIMEOUT_SECONDS: int = 72 * 60 * 60
# Backward-compatible alias used by older imports/tests.
CRON_TEAM_STREAM_TIMEOUT_SECONDS: float = float(CRON_TEAM_DEFAULT_TIMEOUT_SECONDS)


def normalize_cron_job_timeout_seconds(raw: Any) -> int | None:
    """Validate optional per-job timeout override (seconds)."""
    if raw is None:
        return None
    try:
        value = int(raw)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("timeout_seconds must be int") from exc
    if value < 60:
        raise ValueError("timeout_seconds must be at least 60")
    if value > CRON_MAX_TIMEOUT_SECONDS:
        raise ValueError(f"timeout_seconds must be at most {CRON_MAX_TIMEOUT_SECONDS}")
    return value


def resolve_cron_job_timeout_seconds(job: "CronJob") -> float:
    """Return effective execution timeout for a cron job."""
    raw = getattr(job, "timeout_seconds", None)
    if raw is not None:
        return float(int(raw))
    if is_team_cron_mode(job.mode):
        return float(CRON_TEAM_DEFAULT_TIMEOUT_SECONDS)
    return float(CRON_DEFAULT_TIMEOUT_SECONDS)


def is_team_cron_mode(mode: str | None) -> bool:
    """Return True when a cron job should run via Team + SwarmFlow streaming."""
    value = str(mode or "").strip().lower()
    return value in _TEAM_CRON_MODES


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
    # For one-shot schedules where croniter has no "next" after the run.
    expired: bool = False
    # Target channel ID to push results to (e.g. "web").
    # JSON 字段名仍然叫 targets，用字符串保存频道 ID，兼容旧数据。
    targets: str = ""
    # SessionMap 形态（如 feishu::chat_id::bot_id::...），仅 feishu_enterprise 投递用；由 AgentServer 上下文写入。
    session_id: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    # 记录定时任务是在群聊("group")还是私聊("p2p")中创建的，用于推送时决定是否走 IMOutboundPipeline
    chat_type: str | None = None
    # 定时任务执行时使用的 Agent 模式；未指定时默认 agent.fast
    mode: str = CRON_JOB_DEFAULT_MODE
    # 执行一次后自动删除（用于提醒类任务）
    delete_after_run: bool = False
    # 单次执行超时（秒）；未配置时普通模式 10 分钟，team 模式 20 分钟
    timeout_seconds: int | None = None
    # 飞书多应用场景：创建该定时任务的 app_id，用于推送时定位到正确的 app 配置
    app_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "enabled": bool(self.enabled),
            "expired": bool(self.expired),
            "cron_expr": self.cron_expr,
            "timezone": self.timezone,
            "wake_offset_seconds": int(self.wake_offset_seconds),
            "description": self.description,
            "targets": self.targets,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.session_id:
            d["session_id"] = self.session_id
        if self.chat_type:
            d["chat_type"] = self.chat_type
        if self.mode:
            d["mode"] = self.mode
        if self.delete_after_run:
            d["delete_after_run"] = bool(self.delete_after_run)
        if self.timeout_seconds is not None:
            d["timeout_seconds"] = int(self.timeout_seconds)
        if self.app_id:
            d["app_id"] = self.app_id
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CronJob":
        job_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        cron_expr = str(data.get("cron_expr") or "").strip()
        timezone = str(data.get("timezone") or "").strip()
        enabled = bool(data.get("enabled", False))
        expired = bool(data.get("expired", False))

        wake_offset_seconds_raw = data.get("wake_offset_seconds", 300)
        try:
            wake_offset_seconds = int(wake_offset_seconds_raw)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("wake_offset_seconds must be int") from exc
        if wake_offset_seconds < 0:
            wake_offset_seconds = 0

        description = str(data.get("description") or "").strip()
        if not description:
            raise ValueError("description is required")

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
        validate_cron_expression(cron_expr, timezone=timezone)
        if not targets_str:
            raise ValueError("targets is required")

        targets_str = _normalize_targets_str(targets_str)

        sid_raw = data.get("session_id", None)
        job_session_id = str(sid_raw).strip() if isinstance(sid_raw, str) and str(sid_raw).strip() else None

        chat_type_raw = data.get("chat_type", None)
        job_chat_type = (
            str(chat_type_raw).strip()
            if isinstance(chat_type_raw, str) and str(chat_type_raw).strip()
            else None
        )

        mode_raw = data.get("mode", None)
        if isinstance(mode_raw, str) and str(mode_raw).strip():
            job_mode = coerce_cron_job_mode(mode_raw)
        else:
            job_mode = CRON_JOB_DEFAULT_MODE

        delete_after_run = bool(data.get("delete_after_run", False))

        timeout_seconds_raw = data.get("timeout_seconds", None)
        timeout_seconds = None
        if timeout_seconds_raw is not None:
            timeout_seconds = normalize_cron_job_timeout_seconds(timeout_seconds_raw)

        app_id_raw = data.get("app_id", "")
        job_app_id = str(app_id_raw).strip() if isinstance(app_id_raw, str) else ""

        return CronJob(
            id=job_id,
            name=name,
            enabled=enabled,
            expired=expired,
            cron_expr=cron_expr,
            timezone=timezone,
            wake_offset_seconds=wake_offset_seconds,
            description=description,
            targets=targets_str,
            session_id=job_session_id,
            created_at=created_at_f,
            updated_at=updated_at_f,
            chat_type=job_chat_type,
            mode=job_mode,
            delete_after_run=delete_after_run,
            timeout_seconds=timeout_seconds,
            app_id=job_app_id,
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
    job_name: str | None = None
    targets: str | None = None
    session_id: str | None = None
    chat_type: str | None = None
    timezone: str | None = None
