# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deep adapter integration hooks for request_summaries.jsonl."""

from __future__ import annotations

from typing import Any

from jiuwenclaw.perf.collector import get_perf_collector
from jiuwenclaw.perf.context import clear_request_context, mark_first_byte_latency
from jiuwenclaw.perf.guard import run_perf_safe

_COMPONENT = "JiuWenClawDeepAdapter"


def set_perf_summary_context(
    rail: Any | None,
    *,
    channel_id: str = "",
    session_id: str = "",
    request_id: str = "",
    mode: str = "agent.plan",
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    """Bind perf request context at parent-agent request entry."""
    if rail is None:
        return
    run_perf_safe(
        _COMPONENT,
        "perf summary context setup",
        lambda: rail.set_request_context(
            channel_id=channel_id,
            session_id=session_id,
            request_id=request_id,
            mode=mode,
            service_id=service_id,
            agent_id=agent_id,
        ),
    )


def mark_request_first_byte() -> None:
    """Record user-visible first-byte latency for the current request."""
    run_perf_safe(
        _COMPONENT,
        "perf summary first-byte mark",
        mark_first_byte_latency,
    )


def finalize_perf_summary_request(
    request_id: str | None,
    *,
    status: str = "ok",
) -> None:
    """Finalize and persist request summary; safe to call from finally blocks."""
    rid = (request_id or "").strip()
    if not rid:
        return

    def _finalize() -> None:
        from jiuwenclaw.perf.extract import current_trace_id_hex

        trace_id = current_trace_id_hex()
        acc = get_perf_collector().get_accumulator(rid)
        if acc is not None and trace_id:
            acc.meta = acc.meta.with_trace_id(trace_id)
        get_perf_collector().finalize_request(rid, status=status)

    run_perf_safe(
        _COMPONENT,
        f"perf summary finalize request_id={rid} status={status}",
        _finalize,
    )


def clear_perf_summary_context() -> None:
    """Clear perf ContextVar bindings after request completes."""
    run_perf_safe(
        _COMPONENT,
        "perf summary context clear",
        clear_request_context,
    )
