# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-session security review state."""
from __future__ import annotations

from collections import Counter, defaultdict, deque

from jiuwenswarm.agents.harness.common.security_review.schema import (
    FailureClass,
    SecurityAdvice,
    SecurityEvent,
    SecurityReviewConfig,
    SecuritySignal,
    Severity,
)


class SecuritySessionState:
    """Bounded in-memory state for hot-path callbacks."""

    def __init__(self, config: SecurityReviewConfig) -> None:
        self.config = config
        self._events: dict[str, deque[SecurityEvent]] = defaultdict(
            lambda: deque(maxlen=max(1, self.config.ring_buffer_size))
        )
        self._messages: dict[str, deque[dict[str, str]]] = defaultdict(
            lambda: deque(maxlen=max(1, self.config.ring_buffer_size))
        )
        self._failure_counts: Counter[
            tuple[str, str, str, FailureClass, str]
        ] = Counter()
        self._advice: dict[str, SecurityAdvice] = {}
        self._session_order: deque[str] = deque()
        self._active_sessions: set[str] = set()
        self._evicted_sessions: deque[str] = deque()

    def record_event(self, event: SecurityEvent) -> None:
        self._touch_session(event.session_id)
        self._events[event.session_id].append(event)

    def snapshot_events(self, session_id: str) -> list[SecurityEvent]:
        return list(self._events.get(session_id, ()))

    def record_message(self, session_id: str, role: str, content: str) -> None:
        self._touch_session(session_id)
        digest = str(content or "")[: self.config.max_event_chars]
        if not digest.strip():
            return
        self._messages[session_id].append(
            {"role": str(role or "unknown"), "content_digest": digest}
        )

    def snapshot_messages(self, session_id: str) -> list[dict[str, str]]:
        return list(self._messages.get(session_id, ()))

    def drain_evicted_sessions(self) -> list[str]:
        sessions = list(self._evicted_sessions)
        self._evicted_sessions.clear()
        return sessions

    def record_signals(self, signals: list[SecuritySignal]) -> list[SecuritySignal]:
        generated: list[SecuritySignal] = []
        for signal in signals:
            self._touch_session(signal.session_id)
            if signal.severity in {Severity.HIGH, Severity.CRITICAL}:
                self._set_advice(signal)
            if (
                signal.signal_type in {"permission_boundary_hit", "approval_required"}
                and signal.failure_class is not None
                and signal.tool_name
            ):
                generated.extend(self._record_failure(signal))
        return generated

    def consume_advice(self, session_id: str) -> SecurityAdvice | None:
        advice = self._advice.pop(session_id, None)
        if advice is not None:
            advice.consumed = True
        return advice

    def set_runtime_advice(
        self, session_id: str, content: str, severity: Severity = Severity.HIGH
    ) -> None:
        if not content.strip():
            return
        self._touch_session(session_id)
        self._advice[session_id] = SecurityAdvice(
            session_id=session_id,
            severity=severity,
            content=content,
        )

    def counter_snapshot(self, session_id: str) -> dict[str, int]:
        prefix = f"{session_id}:"
        return {
            f"{tool}:{signal_type}:{failure.value}:{reason_code}": count
            for (
                sid,
                tool,
                signal_type,
                failure,
                reason_code,
            ), count in self._failure_counts.items()
            if f"{sid}:" == prefix
        }

    def _record_failure(self, signal: SecuritySignal) -> list[SecuritySignal]:
        assert signal.failure_class is not None
        key = (
            signal.session_id,
            signal.tool_name,
            signal.signal_type,
            signal.failure_class,
            signal.reason_code,
        )
        self._failure_counts[key] += 1
        count = self._failure_counts[key]
        if count < max(1, self.config.repeated_tool_failure_threshold):
            return []

        repeated = SecuritySignal(
            signal_type="repeated_tool_failure",
            severity=Severity.HIGH,
            session_id=signal.session_id,
            iteration=signal.iteration,
            tool_name=signal.tool_name,
            failure_class=signal.failure_class,
            evidence=signal.evidence,
            source="derived",
            confidence="derived",
            reason_code="repeated_tool_failure",
        )
        self._set_repeated_failure_advice(repeated, count)
        generated = [repeated]
        if (
            signal.signal_type == "approval_required"
            and signal.reason_code == "approval_required"
        ):
            generated.append(
                SecuritySignal(
                    signal_type="approval_boundary_gap",
                    severity=Severity.HIGH,
                    session_id=signal.session_id,
                    iteration=signal.iteration,
                    tool_name=signal.tool_name,
                    failure_class=signal.failure_class,
                    evidence=signal.evidence,
                    source="derived",
                    confidence="derived",
                    reason_code="approval_boundary_gap",
                )
            )
        if (
            signal.signal_type == "permission_boundary_hit"
            and signal.failure_class == FailureClass.PERMISSION_DENIED
            and signal.reason_code == "generic_permission_denied"
        ):
            generated.append(
                SecuritySignal(
                    signal_type="policy_rule_gap",
                    severity=Severity.HIGH,
                    session_id=signal.session_id,
                    iteration=signal.iteration,
                    tool_name=signal.tool_name,
                    failure_class=signal.failure_class,
                    evidence=signal.evidence,
                    source="derived",
                    confidence="derived",
                    reason_code="policy_gap_repeated_generic_permission",
                )
            )
        return generated

    def _touch_session(self, session_id: str) -> None:
        if session_id in self._active_sessions:
            return
        self._active_sessions.add(session_id)
        self._session_order.append(session_id)
        while len(self._active_sessions) > max(1, self.config.max_sessions):
            self._evict_session(self._session_order.popleft())

    def _evict_session(self, session_id: str) -> None:
        self._active_sessions.discard(session_id)
        self._events.pop(session_id, None)
        self._messages.pop(session_id, None)
        self._advice.pop(session_id, None)
        self._evicted_sessions.append(session_id)
        for key in list(self._failure_counts):
            if key[0] == session_id:
                del self._failure_counts[key]

    def _set_advice(self, signal: SecuritySignal) -> None:
        self._advice[signal.session_id] = SecurityAdvice(
            session_id=signal.session_id,
            severity=signal.severity,
            content=(
                "安全监督提示：检测到安全风险 "
                f"{signal.signal_type}。后续步骤必须避免重复高风险操作，"
                "如确需继续，请先说明安全目的并请求授权。"
            ),
        )

    def _set_repeated_failure_advice(self, signal: SecuritySignal, count: int) -> None:
        failure = signal.failure_class.value if signal.failure_class else "unknown_failure"
        self._advice[signal.session_id] = SecurityAdvice(
            session_id=signal.session_id,
            severity=Severity.HIGH,
            content=(
                f"安全监督提示：工具 {signal.tool_name} 已因 {failure} 连续失败 {count} 次。"
                "停止重复同一路径；改为说明权限边界、请求用户授权，或选择 workspace 内替代证据。"
            ),
        )
