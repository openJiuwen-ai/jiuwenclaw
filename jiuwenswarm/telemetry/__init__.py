# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuWenSwarm OpenTelemetry GenAI telemetry — public API."""

from __future__ import annotations

import logging

from jiuwenswarm.telemetry.sqlite_exporter import (
    SQLiteSpanExporter,
    query_spans,
    get_trace_tree,
    get_span_statistics,
)

__all__ = [
    "SQLiteSpanExporter",
    "query_spans",
    "get_trace_tree",
    "get_span_statistics",
]

_initialized = False
_logger = logging.getLogger(__name__)


def init_telemetry() -> None:
    """Initialize OpenTelemetry tracing and metrics.

    Reads config from env vars / config.yaml. If telemetry is disabled,
    this is a no-op with zero overhead. Initialization failures are logged
    and silently ignored so they never block the main application.
    """
    global _initialized
    if _initialized:
        return

    from jiuwenswarm.telemetry.config import load_telemetry_config

    cfg = load_telemetry_config()
    if not cfg.enabled:
        return

    try:
        from jiuwenswarm.telemetry.provider import init_providers

        _logger.info(
            "[Telemetry] Initializing: traces_exporter=%s, metrics_exporter=%s, "
            "traces_endpoint=%s, metrics_endpoint=%s, log_messages=%s",
            cfg.traces_exporter,
            cfg.metrics_exporter,
            cfg.traces_endpoint,
            cfg.metrics_endpoint,
            cfg.log_messages,
        )

        from jiuwenswarm.telemetry.metrics import set_resource
        from jiuwenswarm.telemetry.instrumentors import apply_instrumentors

        bundle = init_providers(cfg)
        set_resource(bundle.resource)
        apply_instrumentors(
            log_messages=cfg.log_messages,
            session_stuck_threshold_ms=cfg.session_stuck_threshold_ms,
            session_stuck_check_interval_s=cfg.session_stuck_check_interval_s,
        )

        _initialized = True
        _logger.info("[Telemetry] Initialization complete")
    except Exception:
        _logger.warning("[Telemetry] Initialization failed, continuing without telemetry", exc_info=True)


def is_telemetry_initialized() -> bool:
    """Check if telemetry has been initialized.

    Returns:
        True if telemetry has been initialized, False otherwise.
    """
    return _initialized


def reset_telemetry() -> None:
    """Reset the telemetry initialized flag (test-only).

    Allows ``init_telemetry()`` to be re-invoked after env/config changes in
    tests. Note that the global OTel TracerProvider/MeterProvider installed by
    the first init cannot be replaced (OTel forbids overriding), so a second
    init only re-reads config and re-applies instrumentors; it does not swap
    the active provider. Exposed as a public function so callers need not poke
    the private ``_initialized`` module attribute.
    """
    global _initialized
    _initialized = False
