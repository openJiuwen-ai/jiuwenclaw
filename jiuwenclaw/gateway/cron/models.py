from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CronTargetChannel(str, Enum):
    """推送频道枚举。"""

    WEB = "web"
    FEISHU = "feishu"
    WHATSAPP = "whatsapp"
    WECOM = "wecom"
    XIAOYI = "xiaoyi"
    WECHAT = "wechat"
    # DINGTALK = "dingtalk"


def _feishu_app_id(s: str) -> str:
    """feishu 通道键仅为 feishu:<app_id>；忽略 :chat: 等后续后缀。"""
    parts = str(s or "").strip().split(":")
    if len(parts) < 2 or parts[0].strip().lower() != "feishu":
        return ""
    return parts[1].strip()


def is_valid_target_channel_id(raw: str) -> bool:
    s = str(raw or "").strip()
    if not s:
        return False
    if s.startswith("feishu:"):
        return bool(_feishu_app_id(s))
    try:
        CronTargetChannel(s.lower())
        return True
    except ValueError:
        return False


def normalize_target_channel_id(raw: str, *, default: str = CronTargetChannel.WEB.value) -> str:
    s = str(raw or "").strip()
    if not s:
        return default
    if s.startswith("feishu:"):
        app_id = _feishu_app_id(s)
        if app_id:
            return f"feishu:{app_id}"
        return default
    low = s.lower()
    try:
        return CronTargetChannel(low).value
    except ValueError:
        return default


# 与 app_gateway 中单/多飞书 Bot 判别一致（子节点需含 app_id / app_secret / enabled 之一）
_FEISHU_SINGLE_BOT_TOP_KEYS = frozenset(
    {
        "app_id",
        "app_secret",
        "encrypt_key",
        "verification_token",
        "allow_from",
        "enable_streaming",
        "chat_id",
        "enabled",
        "send_file_allowed",
        "group_digital_avatar",
        "my_user_id",
        "my_open_id",
        "bot_name",
        "enable_memory",
        "message_merge_window_ms",
        "last_chat_id",
        "last_open_id",
        "last_message_id",
    }
)


def feishu_multi_bot_app_ids_from_config(feishu_conf: dict[str, Any]) -> list[str]:
    """列出 ``channels.feishu`` 下多 bot 子区块的 ``app_id``（不含单 bot 顶层扁平形态）。"""
    out: list[str] = []
    if not isinstance(feishu_conf, dict):
        return out
    for bot_key, bot_raw in feishu_conf.items():
        if not isinstance(bot_key, str) or not bot_key.strip():
            continue
        if bot_key in _FEISHU_SINGLE_BOT_TOP_KEYS:
            continue
        bot = bot_raw if isinstance(bot_raw, dict) else None
        if bot is None:
            continue
        if not any(k in bot for k in ("app_id", "app_secret", "enabled")):
            continue
        aid = str(bot.get("app_id") or "").strip()
        if aid:
            out.append(aid)
    return out


def upgrade_bare_feishu_target_for_multi_bot_config(targets: str) -> str:
    """合并 enterprise 与飞书后：裸 ``feishu`` 仅在对「多 bot 配置且唯一子 bot」时可改为 ``feishu:<app_id>``。

    - 单 bot 扁平 ``channels.feishu.app_id`` 仍用注册键 ``feishu``，此处不改写。
    - 多 bot 若仅配一个子节点，与网关注册的 ``feishu:<app_id>`` 对齐，避免投递歧义。
    """
    t = str(targets or "").strip()
    if t != CronTargetChannel.FEISHU.value:
        return t
    try:
        from jiuwenclaw.config import get_config_raw

        fc = (get_config_raw() or {}).get("channels") or {}
        feishu_conf = fc.get("feishu")
        if not isinstance(feishu_conf, dict):
            return t
        ids = feishu_multi_bot_app_ids_from_config(feishu_conf)
        if len(ids) == 1:
            return f"feishu:{ids[0]}"
    except Exception:
        return t
    return t


def _normalize_targets_str(raw: str) -> str:
    """将 targets 字符串规范为 CronTargetChannel 枚举值，非法则默认 web。"""
    return normalize_target_channel_id(raw, default=CronTargetChannel.WEB.value)


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
    # SessionMap 形态（如 feishu::chat_id::bot_id::...），仅 feishu 多 bot 投递用；由 AgentServer 上下文写入。
    session_id: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    # 记录定时任务是在群聊("group")还是私聊("p2p")中创建的，用于推送时决定是否走 IMOutboundPipeline
    chat_type: str | None = None
    # 定时任务执行时使用的 mode（"plan" 或 "agent"），创建时从对话上下文继承；无上下文时默认 "agent"
    mode: str = "agent"
    # 执行一次后自动删除（用于提醒类任务）
    delete_after_run: bool = False
    service_id: str = "default"
    agent_id: str = "default"

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
            "service_id": self.service_id,
            "agent_id": self.agent_id,
        }
        if self.session_id:
            d["session_id"] = self.session_id
        if self.chat_type:
            d["chat_type"] = self.chat_type
        if self.mode:
            d["mode"] = self.mode
        if self.delete_after_run:
            d["delete_after_run"] = bool(self.delete_after_run)
        return d

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CronJob":
        job_id = str(data.get("id") or "").strip()
        name = str(data.get("name") or "").strip()
        cron_expr = str(data.get("cron_expr") or "").strip()
        timezone = str(data.get("timezone") or "").strip()
        enabled = bool(data.get("enabled", False))
        expired = bool(data.get("expired", False))

        wake_offset_seconds_raw = data.get("wake_offset_seconds", 60)
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

        sid_raw = data.get("session_id", None)
        job_session_id = str(sid_raw).strip() if isinstance(sid_raw, str) and str(sid_raw).strip() else None

        chat_type_raw = data.get("chat_type", None)
        job_chat_type = (
            str(chat_type_raw).strip()
            if isinstance(chat_type_raw, str) and str(chat_type_raw).strip()
            else None
        )

        mode_raw = data.get("mode", None)
        job_mode = (
            str(mode_raw).strip().lower()
            if isinstance(mode_raw, str) and str(mode_raw).strip()
            else "agent"
        )

        delete_after_run = bool(data.get("delete_after_run", False))

        job_service_id = str(data.get("service_id") or "default").strip() or "default"
        job_agent_id = str(data.get("agent_id") or "default").strip() or "default"

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
            service_id=job_service_id,
            agent_id=job_agent_id,
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
