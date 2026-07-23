# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""RequestSummaryRail — collect per-request performance summaries via DeepAgent hooks."""

from __future__ import annotations

import functools
import logging
import os
import time
from typing import Any

from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.perf.collector import get_perf_collector
from jiuwenclaw.perf.config import get_perf_summary_config
from jiuwenclaw.perf.context import (
    clear_current_llm_call,
    clear_request_context,
    get_current_llm_call,
    get_request_context,
    increment_react_iteration,
    pop_tool_start,
    reset_react_iteration,
    resolve_task_id,
    set_current_llm_call,
    set_tool_start,
    set_request_context,
)
from jiuwenclaw.perf.events import LlmPerfEvent, ToolPerfEvent
from jiuwenclaw.perf.extract import (
    current_trace_id_hex,
    extract_agent_id,
    extract_llm_error,
    extract_llm_result,
    extract_model_info,
    extract_react_iteration,
    extract_stream_source_id,
    extract_tool_call_info,
    extract_tool_error,
    extract_tool_result,
    extract_usage_tokens,
    llm_status_from_ctx,
    tool_status_from_result,
)
from jiuwenclaw.perf.guard import run_perf_safe

logger = logging.getLogger(__name__)


def _hook_safe(method):
    @functools.wraps(method)
    async def wrapper(self: RequestSummaryRail, *args, **kwargs):
        if self.is_hook_degraded():
            return None
        try:
            return await method(self, *args, **kwargs)
        except Exception as exc:
            self.register_hook_failure(method.__name__, exc)
            return None

    return wrapper


def _resolve_request_id(req_ctx: dict[str, Any] | None) -> str | None:
    if req_ctx is None:
        return None
    request_id = str(req_ctx.get("request_id") or "").strip()
    return request_id or None


class RequestSummaryRail(DeepAgentRail):
    """Collect llm/tool timings into request_summaries.jsonl."""

    priority = 11

    def __init__(self, *, record_only: bool = False) -> None:
        super().__init__()
        self._record_only = record_only
        self._deep_agent: Any | None = None
        self._failure_count = 0
        self._degraded = False
        try:
            self._failure_threshold = int(os.getenv("PERF_SUMMARY_HOOK_FAILURE_THRESHOLD", "10"))
        except ValueError:
            self._failure_threshold = 10

    def init(self, agent: Any) -> None:
        """Store DeepAgent reference for stable card.id extraction."""
        self._deep_agent = agent

    def is_hook_degraded(self) -> bool:
        return self._degraded

    def register_hook_failure(self, hook_name: str, exc: Exception) -> None:
        self._failure_count += 1
        logger.warning(
            "[RequestSummaryRail] hook %s failed (%d/%d): %s",
            hook_name,
            self._failure_count,
            self._failure_threshold,
            exc,
        )
        if self._failure_count >= self._failure_threshold:
            self._degraded = True
            logger.warning(
                "[RequestSummaryRail] circuit breaker tripped — hooks disabled until restart"
            )

    def set_request_context(
        self,
        *,
        channel_id: str = "",
        session_id: str = "",
        request_id: str = "",
        mode: str = "agent.plan",
        trace_id: str | None = None,
        service_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        if not get_perf_summary_config().enabled:
            return
        run_perf_safe(
            "RequestSummaryRail",
            "set_request_context",
            lambda: set_request_context(
                session_id=session_id,
                request_id=request_id,
                channel_id=channel_id,
                mode=mode,
                trace_id=trace_id,
                service_id=service_id,
                agent_id=agent_id,
            ),
        )

    @_hook_safe
    async def before_invoke(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        reset_react_iteration()

    @_hook_safe
    async def after_invoke(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        if self._record_only:
            return
        request_id = _resolve_request_id(get_request_context())
        if request_id is None:
            return

        status = "error" if getattr(ctx, "error", None) else "ok"
        trace_id = current_trace_id_hex()
        acc = get_perf_collector().get_accumulator(request_id)
        if acc is not None and trace_id:
            acc.meta = acc.meta.with_trace_id(trace_id)
        get_perf_collector().finalize_request(request_id, status=status)
        clear_request_context()

    @_hook_safe
    async def before_model_call(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        increment_react_iteration()
        call_id = str(time.monotonic_ns())
        set_current_llm_call(call_id, time.monotonic())

    @_hook_safe
    async def after_model_call(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        call_id, start_monotonic = get_current_llm_call()
        if not call_id or start_monotonic is None:
            clear_current_llm_call()
            return

        request_id = _resolve_request_id(get_request_context())
        if request_id is None:
            clear_current_llm_call()
            return

        duration_ms = max(0.0, (time.monotonic() - start_monotonic) * 1000.0)
        agent = getattr(ctx, "agent", None)
        model_name, _ = extract_model_info(agent)
        result = extract_llm_result(ctx)
        input_tokens, output_tokens, cache_read = extract_usage_tokens(result)
        acc = get_perf_collector().get_accumulator(request_id)
        if acc is not None and cache_read:
            acc.cache_read_tokens += cache_read

        llm_status = llm_status_from_ctx(ctx)
        event = LlmPerfEvent(
            llm_call_id=str(call_id),
            duration_ms=duration_ms,
            model=model_name,
            iteration=extract_react_iteration(ctx),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            status=llm_status,
            agent_id=extract_agent_id(agent, deep_agent=self._deep_agent),
            task_id=resolve_task_id(),
            stream_source_id=extract_stream_source_id(ctx),
            error_message=extract_llm_error(ctx) if llm_status != "ok" else None,
        )
        get_perf_collector().record_llm(request_id, event)
        clear_current_llm_call()

    @_hook_safe
    async def on_model_exception(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        call_id, start_monotonic = get_current_llm_call()
        if not call_id or start_monotonic is None:
            clear_current_llm_call()
            return

        request_id = _resolve_request_id(get_request_context())
        if request_id is None:
            clear_current_llm_call()
            return

        duration_ms = max(0.0, (time.monotonic() - start_monotonic) * 1000.0)
        agent = getattr(ctx, "agent", None)
        model_name, _ = extract_model_info(agent)
        event = LlmPerfEvent(
            llm_call_id=str(call_id),
            duration_ms=duration_ms,
            model=model_name,
            iteration=extract_react_iteration(ctx),
            input_tokens=0,
            output_tokens=0,
            status="error",
            agent_id=extract_agent_id(agent, deep_agent=self._deep_agent),
            task_id=resolve_task_id(),
            stream_source_id=extract_stream_source_id(ctx),
            error_message=extract_llm_error(ctx) or "model exception",
        )
        get_perf_collector().record_llm(request_id, event)
        clear_current_llm_call()

    @_hook_safe
    async def before_tool_call(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return
        tool_name, tool_call_id, _ = extract_tool_call_info(ctx)
        set_tool_start(tool_call_id, tool_name, time.monotonic())

    @_hook_safe
    async def after_tool_call(self, ctx: Any) -> None:
        if not get_perf_summary_config().enabled:
            return

        tool_name, tool_call_id, _ = extract_tool_call_info(ctx)
        tool_start = pop_tool_start(tool_call_id, tool_name)
        if tool_start is not None:
            resolved_name, start_monotonic = tool_start
            tool_name = tool_name or resolved_name
            duration_ms = max(0.0, (time.monotonic() - start_monotonic) * 1000.0)
        else:
            duration_ms = 0.0

        request_id = _resolve_request_id(get_request_context())
        if request_id is None:
            return

        result = extract_tool_result(ctx)
        tool_status = tool_status_from_result(result)
        agent = getattr(ctx, "agent", None)
        event = ToolPerfEvent(
            tool_call_id=tool_call_id or f"tool_{time.monotonic_ns()}",
            name=tool_name or "unknown",
            duration_ms=duration_ms,
            status=tool_status,
            agent_id=extract_agent_id(agent, deep_agent=self._deep_agent),
            task_id=resolve_task_id(),
            iteration=extract_react_iteration(ctx),
            error_message=extract_tool_error(result) if tool_status != "ok" else None,
        )
        get_perf_collector().record_tool(request_id, event)
