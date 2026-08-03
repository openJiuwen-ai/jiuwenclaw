# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import time
from contextvars import ContextVar
from typing import Any

_request_context: ContextVar[dict[str, Any] | None] = ContextVar("perf_request_context", default=None)
_request_wall_start: ContextVar[float | None] = ContextVar("perf_request_wall_start", default=None)
_current_llm_call_id: ContextVar[str | None] = ContextVar("perf_current_llm_call_id", default=None)
_current_llm_call_start: ContextVar[float | None] = ContextVar("perf_current_llm_call_start", default=None)
_tool_starts: ContextVar[dict[str, tuple[str, float]] | None] = ContextVar(
    "perf_tool_starts",
    default=None,
)
_inherited_task_id: ContextVar[str | None] = ContextVar("perf_inherited_task_id", default=None)
_react_iteration: ContextVar[int] = ContextVar("perf_react_iteration", default=0)


def set_request_context(
    *,
    session_id: str,
    request_id: str,
    channel_id: str,
    mode: str,
    trace_id: str | None = None,
    service_id: str | None = None,
    agent_id: str | None = None,
) -> None:
    started_at = time.time()
    _request_context.set(
        {
            "session_id": session_id,
            "request_id": request_id,
            "channel_id": channel_id,
            "mode": mode,
            "trace_id": trace_id,
            "started_at": started_at,
            "service_id": service_id,
            "agent_id": agent_id,
        }
    )
    _request_wall_start.set(started_at)
    from jiuwenclaw.perf.collector import get_perf_collector

    get_perf_collector().begin_request(
        session_id=session_id,
        request_id=request_id,
        channel_id=channel_id,
        mode=mode,
        trace_id=trace_id,
        started_at=started_at,
        service_id=service_id,
        agent_id=agent_id,
    )


def clear_request_context() -> None:
    _request_context.set(None)
    _request_wall_start.set(None)
    _current_llm_call_id.set(None)
    _current_llm_call_start.set(None)
    _tool_starts.set(None)
    _inherited_task_id.set(None)
    _react_iteration.set(0)


def reset_react_iteration() -> None:
    _react_iteration.set(0)


def increment_react_iteration() -> int:
    iteration = int(_react_iteration.get() or 0) + 1
    _react_iteration.set(iteration)
    return iteration


def get_react_iteration() -> int:
    return int(_react_iteration.get() or 0)


def set_inherited_task_id(task_id: str | None) -> None:
    _inherited_task_id.set(task_id)


def resolve_task_id() -> str | None:
    """Resolve task_id from TaskExecutionRail binding or inherited parent context."""
    try:
        from jiuwenclaw.agentserver.deep_agent.rails.task_execution_rail import (
            get_current_task_id,
        )

        task_id = get_current_task_id()
    except Exception:
        task_id = None
    if task_id:
        return task_id
    return _inherited_task_id.get()


def get_request_context() -> dict[str, Any] | None:
    return _request_context.get()


def get_request_wall_start() -> float | None:
    return _request_wall_start.get()


def mark_first_byte_latency() -> None:
    wall_start = _request_wall_start.get()
    ctx = _request_context.get()
    if wall_start is None or ctx is None:
        return
    request_id = str(ctx.get("request_id") or "")
    latency_ms = max(0.0, (time.time() - wall_start) * 1000)
    from jiuwenclaw.perf.collector import get_perf_collector

    get_perf_collector().mark_first_byte_latency(request_id, latency_ms)


def set_current_llm_call(call_id: str, start_monotonic: float) -> None:
    _current_llm_call_id.set(call_id)
    _current_llm_call_start.set(start_monotonic)


def get_current_llm_call() -> tuple[str | None, float | None]:
    return _current_llm_call_id.get(), _current_llm_call_start.get()


def clear_current_llm_call() -> None:
    _current_llm_call_id.set(None)
    _current_llm_call_start.set(None)


def set_tool_start(tool_call_id: str, tool_name: str, start_monotonic: float) -> None:
    key = (tool_call_id or "").strip() or f"__anon__{tool_name}_{start_monotonic}"
    starts = _tool_starts.get()
    if starts is None:
        starts = {}
        _tool_starts.set(starts)
    starts[key] = (tool_name, start_monotonic)


def pop_tool_start(tool_call_id: str, tool_name: str = "") -> tuple[str, float] | None:
    key = (tool_call_id or "").strip()
    starts = _tool_starts.get()
    if not starts:
        return None
    if key and key in starts:
        tool_name_stored, start_monotonic = starts.pop(key)
        if not starts:
            _tool_starts.set(None)
        return tool_name_stored, start_monotonic
    if len(starts) == 1:
        only_key = next(iter(starts))
        tool_name_stored, start_monotonic = starts.pop(only_key)
        _tool_starts.set(None)
        return tool_name_stored, start_monotonic
    anon_for_name = [
        candidate_key
        for candidate_key, value in list(starts.items())
        if candidate_key.startswith("__anon__") and tool_name and value[0] == tool_name
    ]
    if len(anon_for_name) == 1:
        candidate_key = anon_for_name[0]
        value = starts.pop(candidate_key)
        if not starts:
            _tool_starts.set(None)
        return value
    return None
