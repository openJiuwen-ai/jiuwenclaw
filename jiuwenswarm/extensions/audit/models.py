# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计数据模型 — 定义 AuditEvent、Alert 等核心数据结构."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AuditEventType(str, Enum):
    """审计事件类型 — 映射到已有的 Hook 事件名或系统内部事件."""

    # 系统生命周期
    SYSTEM_START = "system_start"
    SYSTEM_STOP = "system_stop"

    # 会话生命周期
    SESSION_START = "session_start"
    SESSION_END = "session_end"

    # 对话请求/响应
    CHAT_REQUEST = "chat_request"
    CHAT_RESPONSE = "chat_response"
    CHAT_ERROR = "chat_error"

    # 记忆注入
    MEMORY_BEFORE_CHAT = "memory_before_chat"
    MEMORY_AFTER_CHAT = "memory_after_chat"

    # 告警触发
    ALERT_TRIGGERED = "alert_triggered"
    ALERT_RESOLVED = "alert_resolved"

    # 内部标记
    AUDIT_STARTED = "audit_started"
    AUDIT_STOPPED = "audit_stopped"


class AlertSeverity(str, Enum):
    """告警严重级别."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertStatus(str, Enum):
    """告警状态."""

    ACTIVE = "active"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"


# ── Hook 事件名 → AuditEventType 映射 ──────────────────────────

HOOK_EVENT_MAP: dict[str, AuditEventType] = {
    "gateway:gateway_started": AuditEventType.SYSTEM_START,
    "gateway:gateway_stopped": AuditEventType.SYSTEM_STOP,
    "gateway:before_chat_request": AuditEventType.CHAT_REQUEST,
    "agent_server:agent_server_started": AuditEventType.SYSTEM_START,
    "agent_server:agent_server_stopped": AuditEventType.SYSTEM_STOP,
    "agent_server:before_chat_request": AuditEventType.CHAT_REQUEST,
    "agent_server:memory_before_chat": AuditEventType.MEMORY_BEFORE_CHAT,
    "agent_server:memory_after_chat": AuditEventType.MEMORY_AFTER_CHAT,
    "agent_server:before_system_prompt_build": AuditEventType.SESSION_START,
}


@dataclass
class AuditEvent:
    """一条审计事件记录."""

    event_id: str = ""
    event_type: AuditEventType = AuditEventType.SYSTEM_START
    timestamp: float = 0.0
    session_id: str | None = None
    channel_id: str | None = None
    request_id: str | None = None
    agent_name: str | None = None
    duration_ms: float | None = None
    token_usage: dict[str, Any] | None = None
    error_type: str | None = None
    error_detail: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.event_id:
            self.event_id = _new_event_id()
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict[str, Any]:
        """序列化为 JSON 兼容字典."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "request_id": self.request_id,
            "agent_name": self.agent_name,
            "duration_ms": self.duration_ms,
            "token_usage": self.token_usage,
            "error_type": self.error_type,
            "error_detail": self.error_detail,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvent:
        """从字典反序列化."""
        event_type_raw = data.get("event_type", "")
        try:
            event_type = AuditEventType(event_type_raw)
        except ValueError:
            event_type = AuditEventType.SYSTEM_START

        return cls(
            event_id=data.get("event_id", _new_event_id()),
            event_type=event_type,
            timestamp=float(data.get("timestamp", 0.0)),
            session_id=data.get("session_id"),
            channel_id=data.get("channel_id"),
            request_id=data.get("request_id"),
            agent_name=data.get("agent_name"),
            duration_ms=data.get("duration_ms"),
            token_usage=data.get("token_usage"),
            error_type=data.get("error_type"),
            error_detail=data.get("error_detail"),
            metadata=data.get("metadata", {}),
        )


@dataclass
class AuditSessionSummary:
    """一个会话的审计摘要."""

    session_id: str = ""
    channel_id: str | None = None
    start_time: float = 0.0
    end_time: float | None = None
    total_requests: int = 0
    total_errors: int = 0
    total_tokens: dict[str, int] = field(default_factory=dict)
    skills_used: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    duration_seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "total_requests": self.total_requests,
            "total_errors": self.total_errors,
            "total_tokens": self.total_tokens,
            "skills_used": self.skills_used,
            "tools_used": self.tools_used,
            "duration_seconds": self.duration_seconds,
        }


@dataclass
class Alert:
    """一条告警记录."""

    alert_id: str = ""
    alert_type: str = ""           # 规则名称标识
    severity: AlertSeverity = AlertSeverity.WARNING
    status: AlertStatus = AlertStatus.ACTIVE
    triggered_at: float = 0.0
    rule_name: str = ""
    message: str = ""
    context: dict[str, Any] = field(default_factory=dict)
    resolved_at: float | None = None

    def __post_init__(self) -> None:
        if not self.alert_id:
            self.alert_id = _new_event_id()
        if not self.triggered_at:
            self.triggered_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "alert_type": self.alert_type,
            "severity": self.severity.value,
            "status": self.status.value,
            "triggered_at": self.triggered_at,
            "rule_name": self.rule_name,
            "message": self.message,
            "context": self.context,
            "resolved_at": self.resolved_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Alert:
        severity_raw = data.get("severity", "warning")
        try:
            severity = AlertSeverity(severity_raw)
        except ValueError:
            severity = AlertSeverity.WARNING

        status_raw = data.get("status", "active")
        try:
            status = AlertStatus(status_raw)
        except ValueError:
            status = AlertStatus.ACTIVE

        return cls(
            alert_id=data.get("alert_id", _new_event_id()),
            alert_type=data.get("alert_type", ""),
            severity=severity,
            status=status,
            triggered_at=float(data.get("triggered_at", 0.0)),
            rule_name=data.get("rule_name", ""),
            message=data.get("message", ""),
            context=data.get("context", {}),
            resolved_at=data.get("resolved_at"),
        )


def _new_event_id() -> str:
    """生成唯一事件 ID."""
    return f"audit_{uuid.uuid4().hex[:12]}"
