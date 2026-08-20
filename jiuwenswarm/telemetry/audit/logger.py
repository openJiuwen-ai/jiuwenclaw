# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AuditLogger — emits structured audit records via OpenTelemetry Logs API.

Each audit record carries common fields (trace_id, request_id, session_id,
user_id, bot_id, group_id, agent_name) pulled from the request-scoped
ContextVar shared with TelemetryRail, plus type-specific details.

Records flow: AuditLogger → OTel LoggerProvider → Collector → Loki.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from opentelemetry import trace
from opentelemetry._logs import get_logger
from opentelemetry.trace import SpanContext

from jiuwenswarm.telemetry.audit.models import AuditType
from jiuwenswarm.telemetry.instrumentors.telemetry_rail import _request_context
from jiuwenswarm.utils import logger as _log

_LOGGER_NAME = "jiuwenclaw.audit"


class AuditLogger:
    """Emits structured audit log records via the OTel Logs API.

    Usage::

        from jiuwenswarm.telemetry.audit import AuditLogger, AuditType

        audit = AuditLogger()
        audit.log_audit(
            audit_type=AuditType.TOOL_ACTION,
            details={"risk_level": "high", "blocked": True, "rule_name": "sql_drop"},
        )
    """

    def __init__(self) -> None:
        self._logger = get_logger(_LOGGER_NAME)

    def log_audit(
        self,
        audit_type: AuditType,
        details: dict[str, Any],
        *,
        trace_id: str = "",
        request_id: str = "",
        session_id: str = "",
        user_id: str = "",
        bot_id: str = "",
        group_id: str = "",
        agent_name: str = "",
        agent_pod: str = "",
    ) -> None:
        """Emit one audit log record.

        Common fields are filled from explicit args or from the request-scoped
        ContextVar (``_request_context`` in telemetry_rail) when not provided.
        ``trace_id`` is read from the active OTel span when not provided.
        """
        # Fill gaps from request context
        req_ctx = _get_request_context()
        if not trace_id:
            trace_id = _current_trace_id()
        if not request_id:
            request_id = req_ctx.get("request_id", "")
        if not session_id:
            session_id = req_ctx.get("session_id", "")
        if not user_id:
            user_id = req_ctx.get("user_id", "")
        if not bot_id:
            bot_id = req_ctx.get("bot_id", "")
        if not group_id:
            group_id = req_ctx.get("group_id", "")
        if not agent_name:
            agent_name = req_ctx.get("agent_name", "")
        if not agent_pod:
            agent_pod = _get_agent_pod()

        # Build attributes (all flat key-value pairs for Loki indexing)
        attributes: dict[str, Any] = {
            "audit.type": audit_type.value,
            "trace_id": trace_id,
            "request_id": request_id,
            "session_id": session_id,
            "user_id": user_id,
            "bot_id": bot_id,
            "group_id": group_id,
            "agent_name": agent_name,
            "agent_pod": agent_pod,
        }
        # Flatten details into top-level attributes with audit. prefix
        for k, v in details.items():
            attributes[f"audit.{k}"] = v

        body = f"[{audit_type.value}] {details}"

        try:
            self._logger.emit(
                body=body,
                attributes=attributes,
                severity_text="WARN" if audit_type != AuditType.TOOL_ACTION else "INFO",
                severity_number=30 if audit_type != AuditType.TOOL_ACTION else 20,
            )
        except Exception as exc:
            _log.warning("[AuditLogger] emit failed: %s", exc)


def _get_request_context() -> dict[str, Any]:
    """Read the request-scoped ContextVar shared with TelemetryRail."""
    ctx = _request_context.get()
    return ctx if ctx is not None else {}


def _current_trace_id() -> str:
    """Get the trace ID of the current active span, or empty string."""
    span = trace.get_current_span()
    if span is None:
        return ""
    ctx: SpanContext = span.get_span_context()
    if not ctx.is_valid:
        return ""
    return format(ctx.trace_id, "032x")


def _get_agent_pod() -> str:
    """Get the current pod name. In Kubernetes, HOSTNAME is the pod name."""
    return os.getenv("HOSTNAME", "")
