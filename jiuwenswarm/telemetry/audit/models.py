# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Audit event types and models."""

from __future__ import annotations

from enum import Enum


class AuditType(str, Enum):
    """Audit event categories — no overlap with Trace/Metrics."""

    TOOL_ACTION = "tool_action"
    """Tool risk assessment, interception, HITL decisions, cross-tenant attempts."""

    PRIVACY_PII = "privacy_pii"
    """PII detection, masking actions, RAG citation tracking, data flow anomalies."""

    GUARDRAILS_SAFETY = "guardrails_safety"
    """Prompt injection, jailbreak detection, content safety filtering, compliance violations."""
