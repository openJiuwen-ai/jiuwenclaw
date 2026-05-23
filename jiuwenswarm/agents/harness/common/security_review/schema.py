# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data models for security review rails."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FailureClass(str, Enum):
    PERMISSION_DENIED = "permission_denied"
    SANDBOX_DENIED = "sandbox_denied"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    CROSS_WORKSPACE_DENIED = "cross_workspace_denied"
    NETWORK_DENIED = "network_denied"
    UNKNOWN_FAILURE = "unknown_failure"


@dataclass(slots=True)
class SecurityReviewConfig:
    enabled: bool = False
    runtime_advice: bool = True
    async_review: bool = True
    evolve_security_skills: bool = True
    propose_policy_rules: bool = True
    ring_buffer_size: int = 128
    max_sessions: int = 128
    max_event_chars: int = 2048
    max_reviews_per_session: int = 2
    async_queue_size: int = 1
    min_review_interval_iterations: int = 3
    repeated_tool_failure_threshold: int = 2
    timely_tool_failure_review: bool = True
    high_risk_advice_threshold: int = 1
    repeated_permission_denied_threshold: int = 2
    repeated_blocked_command_threshold: int = 2


@dataclass(slots=True)
class SecurityEvent:
    event_type: str
    session_id: str
    iteration: int = 0
    tool_name: str = ""
    arguments_digest: str = ""
    result_digest: str = ""


@dataclass(slots=True)
class SecurityReviewMessage:
    role: str
    content_digest: str


@dataclass(slots=True)
class SecuritySignal:
    signal_type: str
    severity: Severity
    session_id: str
    iteration: int = 0
    tool_name: str = ""
    failure_class: FailureClass | None = None
    evidence: str = ""
    skill_name: str = ""
    source: str = ""
    confidence: str = ""
    reason_code: str = ""


@dataclass(slots=True)
class SecurityAdvice:
    session_id: str
    severity: Severity
    content: str
    consumed: bool = False


@dataclass(slots=True)
class ReviewRequest:
    request_type: str
    session_id: str
    priority: Severity
    dedupe_key: tuple[str, ...]
    iteration: int = 0
    signals: list[SecuritySignal] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    sample_events: list[SecurityEvent] = field(default_factory=list)
    sample_messages: list[dict[str, str]] = field(default_factory=list)
    skill_state: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ReviewResult:
    session_id: str
    summary: str
    runtime_advice: str = ""
    candidates: list[dict[str, Any]] = field(default_factory=list)
