# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""预置告警规则 — 定义 5 种内置异常检测规则.

规则基类 AlertRule 提供 evaluate() 接口，每条规则接收新事件和 LogStore，
判断是否应触发告警。

当前内置规则:
1. ConsecutiveFailureRule     — 同会话连续失败 N 次
2. TokenBudgetExceededRule   — 24h Token 消耗超限
3. ResponseTimeoutRule       — 单次请求响应时间超过阈值
4. PermissionDenialFloodRule — 短时间大量权限拒绝（预留，暂无数据源）
5. ErrorRateSpikeRule        — 错误率突增
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from .config import AuditConfig
from .log_store import LogStore
from .models import Alert, AlertSeverity, AuditEvent, AuditEventType

logger = logging.getLogger(__name__)


class AlertRule(ABC):
    """告警规则基类."""

    name: str = ""
    description: str = ""
    severity: AlertSeverity = AlertSeverity.WARNING

    @abstractmethod
    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        """评估新事件是否触发告警.

        Args:
            event: 新写入的审计事件
            store: LogStore（可查询历史数据做统计）
            config: AuditConfig（阈值参数来源）

        Returns:
            若触发告警返回 Alert 对象，否则返回 None
        """
        ...


# ────────────────────────────────────────────────────────────────
# 规则 1: 连续失败
# ────────────────────────────────────────────────────────────────

class ConsecutiveFailureRule(AlertRule):
    """同会话连续出现 N 次错误事件时触发告警.

    监控 chat_error 事件的连续出现次数。
    """

    name = "consecutive_failure"
    description = "同一会话连续出现多次错误"
    severity = AlertSeverity.WARNING

    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        threshold = config.consecutive_failure_threshold

        # 仅在错误事件时检查
        if event.event_type != AuditEventType.CHAT_ERROR:
            return None

        session_id = event.session_id or ""
        if not session_id:
            return None

        # 查询该会话最近的错误事件
        recent_errors = await store.query_events({
            "session_id": session_id,
            "event_type": "chat_error",
            "hours": 1,
            "limit": threshold + 5,
        })

        # 检查是否连续 N 次都是错误（无成功请求穿插）
        consecutive = 0
        for err_event in recent_errors:
            if err_event.event_type == AuditEventType.CHAT_ERROR:
                consecutive += 1
            else:
                consecutive = 0
            if consecutive >= threshold:
                break

        if consecutive >= threshold:
            return Alert(
                alert_type=self.name,
                severity=self.severity,
                rule_name=self.name,
                message=f"会话 {session_id} 连续出现 {consecutive} 次错误（阈值 {threshold}）",
                context={
                    "session_id": session_id,
                    "consecutive_count": consecutive,
                    "threshold": threshold,
                    "latest_error": event.error_detail or "",
                },
            )

        return None


# ────────────────────────────────────────────────────────────────
# 规则 2: Token 日消耗超限
# ────────────────────────────────────────────────────────────────

class TokenBudgetExceededRule(AlertRule):
    """24 小时 Token 消耗超过阈值时触发告警.

    在 memory_after_chat 事件触发时检查累计 Token 消耗。
    """

    name = "token_budget_exceeded"
    description = "Token 日消耗超过配置阈值"
    severity = AlertSeverity.CRITICAL

    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        threshold = config.token_daily_threshold

        # 仅在有 Token 数据的事件时检查
        if not event.token_usage:
            return None

        total = int(event.token_usage.get("total_tokens") or event.token_usage.get("total") or 0)
        if total <= 0:
            return None

        # 查询 24h Token 消耗汇总
        summary = await store.get_token_usage_summary(hours=24)
        if not summary:
            return None

        daily_total = summary.get("total", {}).get("total_tokens", 0)

        if daily_total >= threshold:
            return Alert(
                alert_type=self.name,
                severity=self.severity,
                rule_name=self.name,
                message=f"24h Token 消耗达到 {daily_total}，超过阈值 {threshold}",
                context={
                    "daily_total_tokens": daily_total,
                    "threshold": threshold,
                    "by_channel": summary.get("by_channel", {}),
                },
            )

        return None


# ────────────────────────────────────────────────────────────────
# 规则 3: 响应超时
# ────────────────────────────────────────────────────────────────

class ResponseTimeoutRule(AlertRule):
    """单次请求响应时间超过阈值时触发告警.

    通过对比 chat_request 和 memory_after_chat 事件的
    timestamp 差值计算请求处理时长。
    """

    name = "response_timeout"
    description = "单次请求响应时间超过阈值"
    severity = AlertSeverity.WARNING

    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        timeout_threshold = config.response_timeout_seconds

        # 仅在对话完成事件时检查
        if event.event_type != AuditEventType.MEMORY_AFTER_CHAT:
            return None

        # duration_ms 可能直接在 metadata 中
        if event.duration_ms is not None:
            duration_seconds = event.duration_ms / 1000.0
            if duration_seconds > timeout_threshold:
                return Alert(
                    alert_type=self.name,
                    severity=self.severity,
                    rule_name=self.name,
                    message=f"请求耗时 {duration_seconds:.1f}s，超过阈值 {timeout_threshold}s",
                    context={
                        "session_id": event.session_id or "",
                        "request_id": event.request_id or "",
                        "duration_seconds": duration_seconds,
                        "threshold_seconds": timeout_threshold,
                    },
                )

        # 也可通过查找对应的 chat_request 事件计算时差
        session_id = event.session_id or ""
        if session_id:
            recent_requests = await store.query_events({
                "session_id": session_id,
                "event_type": "chat_request",
                "hours": 1,
                "limit": 5,
            })

            for req_event in recent_requests:
                elapsed = event.timestamp - req_event.timestamp
                if elapsed > timeout_threshold:
                    return Alert(
                        alert_type=self.name,
                        severity=self.severity,
                        rule_name=self.name,
                        message=f"请求耗时 {elapsed:.1f}s，超过阈值 {timeout_threshold}s",
                        context={
                            "session_id": session_id,
                            "request_id": event.request_id or req_event.request_id or "",
                            "duration_seconds": elapsed,
                            "threshold_seconds": timeout_threshold,
                        },
                    )

        return None


# ────────────────────────────────────────────────────────────────
# 规则 4: 权限拒绝洪泛（预留）
# ────────────────────────────────────────────────────────────────

class PermissionDenialFloodRule(AlertRule):
    """短时间内大量权限拒绝事件触发告警.

    注: 当前 Hook 事件不包含 permission_denied 事件的触发，
    此规则为预留设计，待后续扩展 Hook 事件后可激活。
    目前仅在 metadata 中检测 permission_denied 标记时生效。
    """

    name = "permission_denial_flood"
    description = "短时间内大量权限被拒绝"
    severity = AlertSeverity.WARNING

    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        window_minutes = config.permission_denial_window_minutes
        threshold = config.permission_denial_threshold

        # 从 metadata 检测权限拒绝标记
        meta = event.metadata or {}
        if meta.get("permission_denied") or meta.get("permission_result") == "denied":
            # 查询窗口内的权限拒绝事件
            cutoff_hours = max(1, window_minutes // 60 + 1)
            recent = await store.query_events({
                "hours": cutoff_hours,
                "limit": threshold + 10,
            })

            flood_count = 0
            cutoff_time = event.timestamp - window_minutes * 60
            for ev in recent:
                ev_meta = ev.metadata or {}
                if (
                    ev_meta.get("permission_denied")
                    or ev_meta.get("permission_result") == "denied"
                ) and ev.timestamp >= cutoff_time:
                    flood_count += 1

            if flood_count >= threshold:
                return Alert(
                    alert_type=self.name,
                    severity=self.severity,
                    rule_name=self.name,
                    message=f"最近 {window_minutes} 分钟内有 {flood_count} 次权限拒绝（阈值 {threshold}）",
                    context={
                        "flood_count": flood_count,
                        "window_minutes": window_minutes,
                        "threshold": threshold,
                    },
                )

        return None


# ────────────────────────────────────────────────────────────────
# 规则 5: 错误率突增
# ────────────────────────────────────────────────────────────────

class ErrorRateSpikeRule(AlertRule):
    """错误率在短时间内突然升高时触发告警.

    计算最近 N 分钟内错误事件占总事件的比例，
    超过阈值时告警。
    """

    name = "error_rate_spike"
    description = "短时间内错误率突增"
    severity = AlertSeverity.CRITICAL

    async def evaluate(
        self,
        event: AuditEvent,
        store: LogStore,
        config: AuditConfig,
    ) -> Alert | None:
        window_minutes = config.error_rate_window_minutes
        threshold_ratio = config.error_rate_threshold_ratio

        # 仅在错误事件时触发检查（减少无谓查询）
        if event.event_type not in (
            AuditEventType.CHAT_ERROR,
            AuditEventType.SYSTEM_STOP,
        ):
            return None

        cutoff_hours = max(1, window_minutes // 60 + 1)
        recent_all = await store.query_events({
            "hours": cutoff_hours,
            "limit": 2000,
        })

        cutoff_time = event.timestamp - window_minutes * 60
        total_in_window = 0
        errors_in_window = 0

        for ev in recent_all:
            if ev.timestamp >= cutoff_time:
                total_in_window += 1
                if ev.error_type is not None or ev.event_type == AuditEventType.CHAT_ERROR:
                    errors_in_window += 1

        if total_in_window < 5:
            # 样本量太小，不做判断
            return None

        error_rate = errors_in_window / total_in_window

        if error_rate >= threshold_ratio:
            return Alert(
                alert_type=self.name,
                severity=self.severity,
                rule_name=self.name,
                message=(
                    f"最近 {window_minutes} 分钟错误率 "
                    f"{error_rate:.1%}（阈值 {threshold_ratio:.1%}），"
                    f"共 {total_in_window} 条事件中 {errors_in_window} 条错误"
                ),
                context={
                    "error_rate": error_rate,
                    "threshold_ratio": threshold_ratio,
                    "total_in_window": total_in_window,
                    "errors_in_window": errors_in_window,
                    "window_minutes": window_minutes,
                },
            )

        return None


# ── 所有预置规则列表 ────────────────────────────────────────────

DEFAULT_RULES: list[AlertRule] = [
    ConsecutiveFailureRule(),
    TokenBudgetExceededRule(),
    ResponseTimeoutRule(),
    PermissionDenialFloodRule(),
    ErrorRateSpikeRule(),
]
