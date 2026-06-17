# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""TelemetryRail — OpenTelemetry instrumentation via DeepAgentRail hooks.

This Rail creates spans for:
- AGENT (jiuwenclaw.agent.invoke) — via before_invoke/after_invoke
- LLM (gen_ai.chat) — via before_model_call/after_model_call
- TOOL (gen_ai.tool.execute) — via before_tool_call/after_tool_call

Follows OpenTelemetry GenAI semantic conventions and integrates with
the existing telemetry module (attributes, metrics, context_propagation).
"""

from __future__ import annotations

import asyncio
import functools
import json
import os
import time
from contextvars import ContextVar, Token
from typing import Any, NamedTuple, Optional, Tuple

from opentelemetry import trace, context
from opentelemetry.trace import SpanKind, StatusCode

from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.utils import logger
from jiuwenclaw.telemetry.attributes import (
    ERROR_TYPE,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_REQUEST_STREAMING,
    GEN_AI_REQUEST_TEMPERATURE,
    GEN_AI_REQUEST_TOP_P,
    GEN_AI_RESPONSE_FINISH_REASON,
    GEN_AI_RESPONSE_FINISH_REASONS,
    GEN_AI_RESPONSE_MODEL,
    GEN_AI_SPAN_TYPE,
    GEN_AI_SYSTEM,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS,
    GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS,
    GEN_AI_USAGE_REASONING_OUTPUT_TOKENS,
    GEN_AI_USAGE_CACHE_READ_TOKENS,  # legacy
    GEN_AI_USAGE_ESTIMATED,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    GEN_AI_USAGE_TOTAL_TOKENS,
    JIUWENCLAW_AGENT_NAME,
    JIUWENCLAW_CANCELED,
    JIUWENCLAW_CHANNEL_ID,
    JIUWENCLAW_ITERATION,
    JIUWENCLAW_REQUEST_ID,
    JIUWENCLAW_SESSION_ID,
    # New constants for attribute refactor
    GEN_AI_INPUT_MESSAGES,
    GEN_AI_INPUT_MESSAGES_COUNT,
    GEN_AI_INPUT_MESSAGES_TOTAL_LENGTH,
    GEN_AI_OUTPUT_MESSAGES,
    GEN_AI_TOOL_DEFINITIONS,
    GEN_AI_DECISION_TYPE,
    GEN_AI_DECISION_TOOL_NAMES,
    GEN_AI_DECISION_TOOL_COUNT,
    GEN_AI_STREAMING_FIRST_TOKEN,
    GEN_AI_TOOL_ARGUMENTS,
    GEN_AI_TOOL_RESULT,
    GEN_AI_SKILL_NAME,
    GEN_AI_SKILL_ID,
    GEN_AI_SKILL_VERSION,
    GEN_AI_CONTEXT_SKILL,
    GEN_AI_CONTEXT_SYSTEM_PROMPT,
    GEN_AI_CONTEXT_USER_MESSAGES,
    GEN_AI_CONTEXT_ASSISTANT_MESSAGES,
    GEN_AI_CONTEXT_TOOL_RESULTS,
    GEN_AI_CONTEXT_TOOL_DEFINITIONS,
)
from jiuwenclaw.telemetry.metrics import (
    _identity_span_attrs,
    metrics_session_id,
    _with_resource_labels,
    add_skill_call_count,
    add_skill_error_count,
    add_skill_token_usage,
    add_tool_token_usage,
    agent_duration,
    llm_call_count,
    llm_duration,
    record_first_token_duration,
    record_skill_duration,
    token_usage,
    tool_call_count,
    tool_duration,
    tool_error_count,
)
from jiuwenclaw.telemetry.context_propagation import extract_trace_context

_tracer = trace.get_tracer("jiuwenclaw.telemetry_rail")


class _LLMContextBundle(NamedTuple):
    """Encapsulates LLM call context data for background token counting."""
    messages: Any
    tools: Any
    model_name: str
    req_ctx: dict


# Module-level flag for message logging
_log_messages: bool = True

# Environment variable configs for message recording
_log_full_messages: bool = os.getenv("OTEL_LOG_FULL_MESSAGES", "false").lower() == "true"
_message_content_max_length: int = int(os.getenv("OTEL_MESSAGE_CONTENT_MAX_LENGTH", "4096"))

# Request-scoped context variables (isolates state across concurrent requests)
# Each request sets these via set_telemetry_context(), and they are automatically
# isolated per asyncio task/context.
_request_context: ContextVar[Optional[dict]] = ContextVar("telemetry_request_context", default=None)
# Stores (span, start_time, context_token) for the active AGENT span
_agent_span_ctx: ContextVar[Optional[Tuple[trace.Span, float, Optional[Token]]]] = ContextVar(
    "telemetry_agent_span", default=None
)
# Accumulates token usage across LLM calls within an agent invoke.
_agent_token_usage: ContextVar[Optional[dict]] = ContextVar(
    "telemetry_agent_token_usage", default=None
)
# Time when first streaming token arrived (monotonic timestamp).
# Set by TTFT stream patch (inlined in instrumentors/__init__.py) on the first yielded chunk,
# read here in after_model_call. Non-streaming calls never set it.
first_token_time: ContextVar[Optional[float]] = ContextVar("first_token_time", default=None)
# Skill sessions are now tracked via ``ctx.extra["_skill_sessions"]`` shared dict
# instead of a ContextVar. ContextVars are per-asyncio-task, but skill_tool and
# skill_complete execute in DIFFERENT tasks (each tool call is a new task via
# asyncio.gather). The ctx.extra dict is a shared reference passed by the
# ability_manager, so data written in the skill_tool task is visible in the
# skill_complete task.

# Module-level lazy token counter singleton (cl100k_base encoding)
_token_counter: Optional[Any] = None
_TOKEN_COUNTER_FAILED = object()  # Sentinel: initialization tried and failed


def _get_token_counter() -> Any:
    """Lazy-init tiktoken counter; returns None on permanent failure."""
    global _token_counter
    if _token_counter is None:
        try:
            from openjiuwen.core.context_engine.token.tiktoken_counter import TiktokenCounter
            _token_counter = TiktokenCounter(model="gpt-4")
        except Exception:
            logger.warning("[TelemetryRail] tiktoken unavailable — token metrics will use len//4 fallback")
            _token_counter = _TOKEN_COUNTER_FAILED
    if _token_counter is _TOKEN_COUNTER_FAILED:
        return None
    return _token_counter


def _bg_task_done_cb(task: asyncio.Task) -> None:
    """Done callback for background token-counting tasks.

    Logs any exception that escaped the inner try/except in
    _async_record_llm_context. Cancellation is expected (timeout
    or on_model_exception) and is not logged as an error.
    """
    exc = task.exception()
    if exc is not None and not isinstance(exc, asyncio.CancelledError):
        logger.error("[TelemetryRail] background context task failed: %s", exc)


def _normalize_tool_args(args: Any) -> dict:
    """Normalize tool call arguments to a dict, handling JSON strings.

    LLM providers (OpenAI, etc.) return function-call arguments as JSON
    strings, not dicts. The ability_manager parses them in
    ``_parse_tool_arguments``, but that runs AFTER ``before_tool_call``
    hooks fire — so the telemetry rail must also handle this case.
    """
    if isinstance(args, dict):
        return args
    if isinstance(args, str):
        try:
            return json.loads(args)
        except (json.JSONDecodeError, TypeError):
            return {}
    return {}


def _extract_model_info(agent: Any) -> tuple[str, str]:
    """Extract (model_name, gen_ai.system) from a BaseAgent/ReActAgent instance.

    ReActAgent stores model_provider/model_client_config/model_name on
    ``agent._config`` (a ReActAgentConfig), not directly on the agent. Read
    direct attributes first (for future compatibility), then fall back to
    ``_config``, then to ``_config.model_client_config.client_provider``.
    """
    if agent is None:
        return "", "unknown"

    config = getattr(agent, "_config", None)

    model_name = getattr(agent, "model_name", "")
    if not model_name and config is not None:
        model_name = getattr(config, "model_name", "")

    provider = getattr(agent, "model_provider", "")
    if not provider and config is not None:
        provider = getattr(config, "model_provider", "")

    if not provider:
        mcc = getattr(agent, "model_client_config", None)
        if mcc is None and config is not None:
            mcc = getattr(config, "model_client_config", None)
        if mcc is not None:
            if isinstance(mcc, dict):
                provider = mcc.get("client_provider", "")
            else:
                provider = getattr(mcc, "client_provider", "")

    system = str(provider).lower() if provider else "unknown"
    return str(model_name or ""), system


def set_log_messages(enabled: bool) -> None:
    """Enable/disable logging full message content in span events."""
    global _log_messages
    _log_messages = enabled


def _hook_safe(method):
    """Decorator: swallow exceptions in TelemetryRail hooks; count toward circuit breaker."""
    @functools.wraps(method)
    async def wrapper(self, *args, **kwargs):
        if getattr(self, "_degraded", False):
            return None
        try:
            return await method(self, *args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._failure_count = getattr(self, "_failure_count", 0) + 1
            logger.warning(
                "[TelemetryRail] hook %s failed (%d/%d): %s",
                method.__name__,
                self._failure_count,
                self._failure_threshold,
                exc,
            )
            if self._failure_count >= self._failure_threshold:
                self._degraded = True
                logger.warning(
                    "[TelemetryRail] circuit breaker tripped — hooks disabled until process restart"
                )
            return None
    return wrapper


class TelemetryRail(DeepAgentRail):
    """Rail that creates OpenTelemetry spans for agent, LLM, and tool calls.

    This Rail hooks into DeepAgent's lifecycle callbacks to create spans
    following OpenTelemetry GenAI semantic conventions.

    Priority should be set low (e.g., 10) so it runs first and captures
    the full execution timeline.

    Request-scoped state (channel_id, session_id, request_id, trace_context,
    agent_span, iteration) is stored in ContextVars to isolate across
    concurrent requests on the same agent instance.
    """

    priority = 10

    def __init__(self) -> None:
        super().__init__()
        self._agent: Optional[Any] = None
        # Active spans keyed by call_id (LLM/tool call IDs are unique per request)
        self._llm_spans: dict[str, tuple[trace.Span, float]] = {}
        self._tool_spans: dict[str, tuple[trace.Span, float, str]] = {}
        # P0: Background tasks for heavy context recording (concurrent with LLM call)
        self._bg_tasks: dict[str, asyncio.Task] = {}
        # Skill session tracking: shared across skill_tool / skill_complete (ctx.extra is per-call)
        self._skill_sessions: dict[str, dict] = {}
        # P1: Cache for tool definition token estimates (tools rarely change between iterations)
        self._tools_cache_key: Optional[tuple[str, ...]] = None
        self._tools_cache_value: int = 0
        # Circuit breaker (instance-level, applies to all hooks)
        self._failure_count: int = 0
        self._degraded: bool = False
        try:
            self._failure_threshold: int = int(os.getenv("OTEL_HOOK_FAILURE_THRESHOLD", "10"))
        except ValueError:
            self._failure_threshold = 10

    def init(self, agent: Any) -> None:
        """Called when Rail is attached to agent."""
        self._agent = agent

    def set_telemetry_context(
        self,
        channel_id: str = "",
        session_id: str = "",
        request_id: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Set telemetry context for the current request.

        Called by JiuWenClawDeepAdapter before invoking the agent.
        Uses ContextVars to isolate state across concurrent requests.

        IMPORTANT: Always clears _trace_context when metadata is None or {},
        preventing inheritance from previous request's traceparent.
        """
        # Extract W3C TraceContext from metadata; explicitly clear if no metadata
        trace_ctx = None
        if metadata:
            trace_ctx = extract_trace_context(metadata)

        # Store all request-scoped state in ContextVar
        _request_context.set({
            "channel_id": channel_id,
            "session_id": session_id,
            "request_id": request_id,
            "trace_context": trace_ctx,
            "iteration": 0,
        })
        # Sync session_id to metrics-scoped ContextVar (decouples metrics.py from this module)
        metrics_session_id.set(session_id)

    def _get_request_context(self) -> dict:
        """Get current request context from ContextVar."""
        ctx = _request_context.get()
        return ctx if ctx is not None else {
            "channel_id": "",
            "session_id": "",
            "request_id": "",
            "trace_context": None,
            "iteration": 0,
        }

    # ------------------------------------------------------------------
    # Invoke hooks — AGENT span
    # ------------------------------------------------------------------

    @_hook_safe
    async def before_invoke(self, ctx: Any) -> None:
        """Create AGENT span when agent starts processing."""
        req_ctx = self._get_request_context()
        # Reset iteration for this request
        req_ctx["iteration"] = 0
        _request_context.set(req_ctx)

        # Reset token accumulation for this agent invoke
        _agent_token_usage.set({"input_tokens": 0, "output_tokens": 0})
        # Reset first_token_time defensively — prevents stale value from
        # a previous request if after_model_call was skipped (circuit breaker, error).
        first_token_time.set(None)
        # Defensive cleanup: remove orphaned skill sessions from a previous request
        # with the same session_id (if after_invoke was skipped due to circuit breaker).
        session_id = req_ctx.get("session_id", "")
        orphaned_keys = [k for k in self._skill_sessions if k.startswith(f"skill_{session_id}_")]
        for k in orphaned_keys:
            self._skill_sessions.pop(k, None)

        # Get conversation_id from inputs
        conversation_id = ""
        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "conversation_id"):
                conversation_id = inputs.conversation_id or ""
            # Also extract from inputs dict if available
            if isinstance(inputs, dict):
                conversation_id = inputs.get("conversation_id", "")

        agent_name = ""
        if self._agent and hasattr(self._agent, "card"):
            agent_name = getattr(self._agent.card, "id", "")

        # 用户可见日志：Agent 开始处理
        logger.info(
            "[TelemetryRail] Agent 开始处理: agent=%s, session_id=%s, channel_id=%s",
            agent_name,
            req_ctx["session_id"],
            req_ctx["channel_id"],
            extra={'user_visible': 'critical'}
        )

        attrs = {
            GEN_AI_AGENT_NAME: agent_name,
            GEN_AI_CONVERSATION_ID: conversation_id,
            GEN_AI_SPAN_TYPE: "agent",
            JIUWENCLAW_AGENT_NAME: agent_name,
            JIUWENCLAW_SESSION_ID: req_ctx["session_id"],
            JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
            JIUWENCLAW_REQUEST_ID: req_ctx["request_id"],
        }

        # Include user query on the agent span for observability.
        # At before_invoke, ctx.inputs is InvokeInputs with a .query attribute.
        user_input = ""
        if hasattr(ctx, "inputs"):
            query = getattr(ctx.inputs, "query", "")
            if isinstance(query, str) and query:
                user_input = query[:500] if len(query) > 500 else query
        if user_input:
            attrs[GEN_AI_INPUT_MESSAGES] = json.dumps(
                [{"role": "user", "parts": [{"type": "text", "content": user_input}]}],
                ensure_ascii=False,
            )

        attrs.update(_identity_span_attrs())

        # Start span with extracted parent context (for cross-WebSocket propagation)
        agent_span = _tracer.start_span(
            "jiuwenclaw.agent.invoke",
            context=req_ctx["trace_context"],
            kind=SpanKind.SERVER,
            attributes=attrs,
        )
        agent_start_time = time.monotonic()

        # Activate span context for child spans (LLM/TOOL)
        ctx_token: Optional[Token] = None
        if agent_span:
            ctx_token = context.attach(trace.set_span_in_context(agent_span))

        # Store span state in ContextVar for this request
        _agent_span_ctx.set((agent_span, agent_start_time, ctx_token))

    @_hook_safe
    async def after_invoke(self, ctx: Any) -> None:
        """End AGENT span after agent finishes."""
        entry = _agent_span_ctx.get()
        if not entry:
            return

        agent_span, agent_start_time, ctx_token = entry
        req_ctx = self._get_request_context()

        # 用户可见日志：Agent 处理完成
        agent_name = ""
        if self._agent and hasattr(self._agent, "card"):
            agent_name = getattr(self._agent.card, "id", "")
        usage_accum = _agent_token_usage.get()
        usage_info = ""
        if usage_accum and (usage_accum["input_tokens"] > 0 or usage_accum["output_tokens"] > 0):
            usage_info = f"input_tokens={usage_accum['input_tokens']}, output_tokens={usage_accum['output_tokens']}"
        duration = time.monotonic() - agent_start_time
        err = getattr(ctx, "error", None)
        status = "error" if err else "success"
        logger.info(
            "[TelemetryRail] Agent 处理完成: agent=%s, status=%s, duration=%.2fs, %s",
            agent_name,
            status,
            duration,
            usage_info,
            extra={'user_visible': 'critical'}
        )

        if agent_span:
            # Check for errors
            err = getattr(ctx, "error", None)
            if err:
                agent_span.set_status(StatusCode.ERROR, str(err)[:256])
                agent_span.record_exception(err)
                if isinstance(err, asyncio.CancelledError):
                    agent_span.set_attribute(JIUWENCLAW_CANCELED, True)
                    agent_span.set_attribute(ERROR_TYPE, "CancelledError")
                elif isinstance(err, asyncio.TimeoutError):
                    agent_span.set_attribute(ERROR_TYPE, "TimeoutError")
                else:
                    agent_span.set_attribute(ERROR_TYPE, type(err).__name__)
            else:
                agent_span.set_status(StatusCode.OK)

            # Record duration metric
            duration = time.monotonic() - agent_start_time
            agent_duration.record(duration, _with_resource_labels({
                JIUWENCLAW_AGENT_NAME: getattr(self._agent, "card", None) and getattr(self._agent.card, "id", ""),
                JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
            }))

            # Set aggregated token usage on agent span
            usage_accum = _agent_token_usage.get()
            if usage_accum and (usage_accum["input_tokens"] > 0 or usage_accum["output_tokens"] > 0):
                agent_span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, usage_accum["input_tokens"])
                agent_span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, usage_accum["output_tokens"])
                agent_span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS,
                                          usage_accum["input_tokens"] + usage_accum["output_tokens"])

            agent_span.end()

        # Detach context token
        if ctx_token is not None:
            context.detach(ctx_token)

        # Clear the agent span context
        _agent_span_ctx.set(None)
        _agent_token_usage.set(None)

        # Cleanup skill sessions for this request only (keyed by session_id).
        # Do NOT .clear() — other concurrent requests may have active sessions.
        session_id = req_ctx.get("session_id", "")
        keys_to_remove = [k for k in self._skill_sessions if k.startswith(f"skill_{session_id}_")]
        for k in keys_to_remove:
            self._skill_sessions.pop(k, None)

        # Defensive cleanup: cancel any lingering background tasks that weren't
        # consumed by after_model_call / on_model_exception (e.g. hook skipped, rail degraded)
        for _, bg_task in list(self._bg_tasks.items()):
            if not bg_task.done():
                bg_task.cancel()
        self._bg_tasks.clear()

    # ------------------------------------------------------------------
    # Model call hooks — LLM span
    # ------------------------------------------------------------------

    async def _async_record_llm_context(
        self, span: trace.Span, bundle: _LLMContextBundle,
    ) -> None:
        """Background task: token counting + JSON serialization + span attributes + metrics.

        Runs concurrently with the LLM call (the event loop schedules this
        task during the LLM's network I/O await). Wrapped in try/except so
        background failure never crashes the agent.
        """
        try:
            if bundle.messages is not None:
                self._record_input_messages(span, bundle.messages)
                token_components = self._record_context_composition(span, bundle.messages)
            else:
                token_components = {}

            if bundle.tools is not None:
                self._record_tools(span, bundle.tools)
                tool_def_tokens = self._estimate_tools_tokens(bundle.tools)
                span.set_attribute(GEN_AI_CONTEXT_TOOL_DEFINITIONS, tool_def_tokens)
                token_components["tool_definitions"] = tool_def_tokens

                # Per-tool token usage metric
                per_tool_tokens = self._estimate_per_tool_tokens(bundle.tools)
                channel_id = bundle.req_ctx.get("channel_id", "")
                for t_name, t_tokens in per_tool_tokens.items():
                    add_tool_token_usage(t_tokens, {
                        GEN_AI_TOOL_NAME: t_name,
                        GEN_AI_REQUEST_MODEL: bundle.model_name,
                        JIUWENCLAW_CHANNEL_ID: channel_id,
                    })
            else:
                token_components["tool_definitions"] = 0

            # Skill token usage metric — per-skill breakdown (handles multi-skill contexts)
            per_skill_tokens = token_components.get("per_skill_tokens", {})
            channel_id = bundle.req_ctx.get("channel_id", "")
            for s_name, s_tokens in per_skill_tokens.items():
                if s_tokens > 0 and s_name:
                    add_skill_token_usage(s_tokens, {
                        GEN_AI_SKILL_NAME: s_name,
                        GEN_AI_REQUEST_MODEL: bundle.model_name,
                        JIUWENCLAW_CHANNEL_ID: channel_id,
                    })

        except Exception:
            logger.warning("[TelemetryRail] background context recording failed")

    @_hook_safe
    async def before_model_call(self, ctx: Any) -> None:
        """Create LLM span before model call."""
        req_ctx = self._get_request_context()

        temperature = None
        top_p = None

        agent = getattr(ctx, "agent", None)
        model_name, system = _extract_model_info(agent)

        # model_config_obj may live on agent or agent._config
        model_cfg = getattr(agent, "model_config_obj", None)
        if model_cfg is None:
            config = getattr(agent, "_config", None)
            if config is not None:
                model_cfg = getattr(config, "model_config_obj", None)
        if model_cfg is not None:
            temperature = getattr(model_cfg, "temperature", None)
            top_p = getattr(model_cfg, "top_p", None)

        # 用户可见日志：模型调用开始
        streaming = False
        if hasattr(ctx, "model"):
            streaming = bool(getattr(ctx.model, "streaming", False))
        logger.info(
            "[TelemetryRail] 模型调用开始: model=%s, system=%s, iteration=%d, streaming=%s",
            model_name,
            system,
            req_ctx["iteration"] + 1,
            streaming,
            extra={'user_visible': 'critical'}
        )

        # Generate unique span key
        call_id = str(time.monotonic_ns())

        attrs = {
            GEN_AI_SYSTEM: system,
            GEN_AI_REQUEST_MODEL: model_name,
            GEN_AI_RESPONSE_MODEL: model_name,
            GEN_AI_OPERATION_NAME: "chat",
            GEN_AI_SPAN_TYPE: "model",
            JIUWENCLAW_SESSION_ID: req_ctx["session_id"],
            JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
            JIUWENCLAW_REQUEST_ID: req_ctx["request_id"],
        }
        attrs.update(_identity_span_attrs())

        if temperature is not None:
            attrs[GEN_AI_REQUEST_TEMPERATURE] = float(temperature)
        if top_p is not None:
            attrs[GEN_AI_REQUEST_TOP_P] = float(top_p)

        # Increment iteration counter (request-scoped)
        req_ctx["iteration"] += 1
        _request_context.set(req_ctx)
        attrs[JIUWENCLAW_ITERATION] = req_ctx["iteration"]

        streaming = False
        if hasattr(ctx, "model"):
            streaming = bool(getattr(ctx.model, "streaming", False))
        attrs[GEN_AI_REQUEST_STREAMING] = streaming

        # Use the current OTel context — supports sub-agent / handoff nesting automatically.
        span = _tracer.start_span(
            "gen_ai.chat",
            kind=SpanKind.INTERNAL,
            attributes=attrs,
        )

        inputs = getattr(ctx, "inputs", None)

        # Collect messages and tools for background recording
        messages = None
        if inputs is not None:
            messages = getattr(inputs, "messages", None)
        if messages is None:
            messages = getattr(ctx, "messages", None)

        tools = None
        if inputs is not None:
            tools = getattr(inputs, "tools", None)

        # P0: Offload heavy token counting + JSON serialization + metric recording
        # to a background asyncio task that runs concurrently with the LLM call.
        # The LLM call is network I/O (await), so the event loop will schedule
        # this background task during the I/O wait — achieving true parallelism.
        bg_task = asyncio.create_task(
            self._async_record_llm_context(span, _LLMContextBundle(messages, tools, model_name, req_ctx))
        )
        # Register exception callback so asyncio doesn't silently swallow errors.
        # _async_record_llm_context already has try/except, but add_done_callback
        # catches cancellation or unexpected crashes that escape the inner handler.
        bg_task.add_done_callback(_bg_task_done_cb)
        self._bg_tasks[call_id] = bg_task

        self._llm_spans[call_id] = (span, time.monotonic())
        ctx._otel_llm_call_id = call_id

    def record_first_token(self, ctx: Any) -> None:
        """Set gen_ai.streaming.first_token attribute on active LLM span.

        Call sites invoke this at the first streaming chunk.
        No-op if no matching span is found.
        """
        call_id = getattr(ctx, "_otel_llm_call_id", None)
        if not call_id:
            return
        entry = self._llm_spans.get(call_id)
        if not entry:
            return
        span, _ = entry
        span.set_attribute(GEN_AI_STREAMING_FIRST_TOKEN, True)

    @_hook_safe
    async def after_model_call(self, ctx: Any) -> None:
        """End LLM span after model call completes."""
        call_id = getattr(ctx, "_otel_llm_call_id", None)
        if not call_id:
            return

        entry = self._llm_spans.pop(call_id, None)
        if not entry:
            # Also clean up any pending background task
            self._bg_tasks.pop(call_id, None)
            return

        span, start_time = entry

        # P0: Ensure background context recording has finished before ending the span.
        # Typically the bg_task completed during the LLM call (1-5s network I/O >> 100ms token counting).
        bg_task = self._bg_tasks.pop(call_id, None)
        if bg_task is not None and not bg_task.done():
            try:
                await asyncio.wait_for(bg_task, timeout=0.2)
            except asyncio.TimeoutError:
                # Cancel the task so it doesn't write attributes to an already-ended span
                # or continue consuming CPU after span.end()
                bg_task.cancel()
            except Exception:
                pass  # bg_task raised an unexpected error; already logged inside _async_record_llm_context

        req_ctx = self._get_request_context()
        channel_id = req_ctx["channel_id"]

        agent = getattr(ctx, "agent", None)
        model_name, system = _extract_model_info(agent)

        # Upstream ReActAgent sets the assistant message on ``ctx.inputs.response``
        # (see openjiuwen/core/single_agent/agents/react_agent.py L778). Older
        # plans referenced ``ctx.result`` which does not exist on
        # AgentCallbackContext; falling back keeps both shapes working.
        result = None
        inputs = getattr(ctx, "inputs", None)
        if inputs is not None:
            result = getattr(inputs, "response", None)
            if result is None and isinstance(inputs, dict):
                result = inputs.get("response")
        if result is None:
            result = getattr(ctx, "result", None)

        # 用户可见日志：模型调用完成
        duration = time.monotonic() - start_time
        usage_info = ""
        if result is not None:
            usage = getattr(result, "usage_metadata", None)
            if usage is not None:
                input_tokens = getattr(usage, "input_tokens", 0) or 0
                output_tokens = getattr(usage, "output_tokens", 0) or 0
                usage_info = f"input_tokens={input_tokens}, output_tokens={output_tokens}"
        err = getattr(ctx, "error", None)
        status = "error" if err else "success"
        logger.info(
            "[TelemetryRail] 模型调用完成: model=%s, system=%s, status=%s, duration=%.2fs, %s",
            model_name,
            system,
            status,
            duration,
            usage_info,
            extra={'user_visible': 'critical'}
        )

        if result is not None:
            self._record_token_usage(span, result, model_name, system, channel_id)

            # Finish reason
            finish_reason = getattr(result, "finish_reason", None)
            if finish_reason and str(finish_reason) != "null":
                span.set_attribute(GEN_AI_RESPONSE_FINISH_REASONS, [str(finish_reason)])
                span.set_attribute(GEN_AI_RESPONSE_FINISH_REASON, str(finish_reason))

            # Record output message
            if _log_messages:
                self._record_output_message(span, result)

            # Record decision analysis (ReAct decision: tool_call vs answer)
            self._record_decision(span, result)

        # Check for errors
        if hasattr(ctx, "error") and ctx.error:
            span.set_status(StatusCode.ERROR, str(ctx.error)[:256])
            span.record_exception(ctx.error)
            llm_call_count.add(1, _with_resource_labels({
                GEN_AI_REQUEST_MODEL: model_name,
                "status": "error",
                JIUWENCLAW_CHANNEL_ID: channel_id,
            }))
        else:
            span.set_status(StatusCode.OK)
            llm_call_count.add(1, _with_resource_labels({
                GEN_AI_REQUEST_MODEL: model_name,
                "status": "success",
                JIUWENCLAW_CHANNEL_ID: channel_id,
            }))

        # Record duration metric
        duration = time.monotonic() - start_time
        llm_duration.record(duration, _with_resource_labels({
            GEN_AI_REQUEST_MODEL: model_name,
            GEN_AI_SYSTEM: system,
            JIUWENCLAW_CHANNEL_ID: channel_id,
        }))

        # --- TTFT: first iteration only ---
        # first_token_time is set by the TTFT stream patch on the first yielded chunk.
        # Non-streaming calls never set it, so they're naturally skipped.
        ft_time = first_token_time.get()
        if ft_time is not None and req_ctx.get("iteration", 0) == 1:
            agent_ctx = _agent_span_ctx.get()
            if agent_ctx is not None:
                _, agent_start_time, _ = agent_ctx
                ttft_ms = (ft_time - agent_start_time) * 1000  # convert to ms
                if ttft_ms >= 0:
                    ttft_attrs = {
                        GEN_AI_REQUEST_MODEL: model_name,
                        GEN_AI_SYSTEM: system,
                        JIUWENCLAW_CHANNEL_ID: channel_id,
                    }
                    record_first_token_duration(ttft_ms, ttft_attrs)
        # Clear ContextVar for next invoke cycle
        first_token_time.set(None)

        span.end()

    @_hook_safe
    async def on_model_exception(self, ctx: Any) -> None:
        """Handle model call exception."""
        call_id = getattr(ctx, "_otel_llm_call_id", None)
        if not call_id:
            return

        entry = self._llm_spans.pop(call_id, None)
        if not entry:
            return

        # P0: Cancel any pending background context recording task
        bg_task = self._bg_tasks.pop(call_id, None)
        if bg_task is not None and not bg_task.done():
            bg_task.cancel()

        span, start_time = entry

        # Clear first_token_time on exception path (consistent with after_model_call)
        first_token_time.set(None)

        if hasattr(ctx, "exception"):
            exc = ctx.exception
            span.set_status(StatusCode.ERROR, str(exc)[:256])
            span.record_exception(exc)

        span.end()

    # ------------------------------------------------------------------
    # Tool call hooks — TOOL span
    # ------------------------------------------------------------------

    @_hook_safe
    async def before_tool_call(self, ctx: Any) -> None:
        """Create TOOL span before tool execution."""
        req_ctx = self._get_request_context()

        # Get tool info from inputs
        tool_name = ""
        tool_call_id = ""
        arguments = {}

        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "tool_call"):
                tc = inputs.tool_call
                tool_name = getattr(tc, "name", "") or ""
                tool_call_id = getattr(tc, "id", "") or ""
                arguments = getattr(tc, "arguments", {}) or {}
                # Normalize: LLM providers may return JSON strings instead of dicts
                arguments = _normalize_tool_args(arguments)
            elif hasattr(inputs, "tool_name"):
                tool_name = inputs.tool_name

        # 用户可见日志：工具调用开始
        logger.info(
            "[TelemetryRail] 工具调用开始: tool=%s, tool_call_id=%s",
            tool_name,
            tool_call_id,
            extra={'user_visible': 'critical'}
        )

        # Fix: Generate unique key when tool_call_id is empty to prevent span overwrite
        span_key = tool_call_id if tool_call_id else f"__no_id__{tool_name}_{time.monotonic_ns()}"

        attrs = {
            GEN_AI_TOOL_NAME: tool_name,
            GEN_AI_TOOL_CALL_ID: tool_call_id,  # Keep original (may be empty)
            GEN_AI_SPAN_TYPE: "tool",
            JIUWENCLAW_SESSION_ID: req_ctx["session_id"],
            JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
            JIUWENCLAW_REQUEST_ID: req_ctx["request_id"],
        }
        attrs.update(_identity_span_attrs())

        # Use the current OTel context — supports sub-agent / handoff nesting automatically.
        span = _tracer.start_span(
            f"gen_ai.tool.execute: {tool_name}",
            kind=SpanKind.INTERNAL,
            attributes=attrs,
        )

        # Record arguments as span attribute
        span.set_attribute(GEN_AI_TOOL_ARGUMENTS, str(arguments)[:4096])

        # Enrich skill_complete span with gen_ai.skill.* attributes (OTel GenAI #86)
        if tool_name == "skill_complete":
            skill_name = arguments.get("skill_name", "") if isinstance(arguments, dict) else ""
            if skill_name:
                span.set_attribute(GEN_AI_OPERATION_NAME, "release_skill")
                span.set_attribute(GEN_AI_SKILL_NAME, skill_name)

        # Skill session tracking: record call_count and start_time when skill_tool activates
        if tool_name == "skill_tool":
            skill_name = ""
            skill_version = ""
            tool_msg = getattr(inputs, "tool_msg", None) if inputs else None
            if tool_msg is not None:
                meta = getattr(tool_msg, "metadata", None) or {}
                if isinstance(meta, dict) and (meta.get("is_skill_body") or meta.get("original_is_skill_body")):
                    skill_name = str(meta.get("skill_name", "") or "")
                    skill_version = str(meta.get("skill_version", "") or "")

            if not skill_name and isinstance(arguments, dict):
                skill_name = str(arguments.get("skill_name", "") or "")

            if skill_name:
                add_skill_call_count(1, {
                    GEN_AI_SKILL_NAME: skill_name,
                    GEN_AI_SKILL_VERSION: skill_version,
                    GEN_AI_SYSTEM: "jiuwenclaw",
                    JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
                })

                session_id = req_ctx.get("session_id", "")
                self._skill_sessions[f"skill_{session_id}_{skill_name}"] = {
                    "start_time": time.monotonic(),
                    "skill_name": skill_name,
                    "skill_version": skill_version,
                    "session_id": session_id,
                }

        self._tool_spans[span_key] = (span, time.monotonic(), tool_name)

        tool_call_count.add(1, _with_resource_labels({
            GEN_AI_TOOL_NAME: tool_name,
            JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
        }))

        # Store span_key in ctx for retrieval in after_tool_call
        ctx._otel_tool_span_key = span_key

    @_hook_safe
    async def after_tool_call(self, ctx: Any) -> None:
        """End TOOL span after tool execution."""
        req_ctx = self._get_request_context()
        inputs = None

        # Retrieve span_key from ctx (set in before_tool_call)
        span_key = getattr(ctx, "_otel_tool_span_key", None)

        # Fallback: reconstruct span_key using same logic as before_tool_call
        if not span_key:
            tool_call_id = ""
            tool_name = ""
            if hasattr(ctx, "inputs"):
                inputs = ctx.inputs
                if hasattr(inputs, "tool_call"):
                    tc = inputs.tool_call
                    tool_call_id = getattr(tc, "id", "") if tc else ""
                    tool_name = getattr(tc, "name", "") if tc else ""
                elif hasattr(inputs, "tool_name"):
                    tool_name = inputs.tool_name
            span_key = tool_call_id if tool_call_id else f"__no_id__{tool_name}_{time.monotonic_ns()}"

        entry = self._tool_spans.pop(span_key, None)
        if not entry:
            return

        span, start_time, span_tool_name = entry

        # 用户可见日志：工具调用完成
        duration = time.monotonic() - start_time
        logger.info(
            "[TelemetryRail] 工具调用完成: tool=%s, duration=%.2fs",
            span_tool_name,
            duration,
            extra={'user_visible': 'critical'}
        )

        # Get result
        result = None
        if hasattr(ctx, "inputs"):
            inputs = ctx.inputs
            if hasattr(inputs, "tool_result"):
                result = inputs.tool_result

        # Record result as span attribute
        result_str = str(result)[:4096] if result is not None else ""
        span.set_attribute(GEN_AI_TOOL_RESULT, result_str[:4096] if result_str else "")

        # Error detection — check structured error field first to avoid
        # false positives from "error=None" appearing in str(result).
        is_error = False
        if result is not None:
            if hasattr(result, "error"):
                is_error = bool(getattr(result, "error", None))
            elif isinstance(result, dict):
                is_error = bool(result.get("error"))
            elif result_str:
                lower = result_str.lower()
                is_error = "error" in lower or "exception" in lower or "traceback" in lower

        if is_error:
            span.set_status(StatusCode.ERROR, result_str[:256])
            tool_error_count.add(1, _with_resource_labels({
                GEN_AI_TOOL_NAME: span_tool_name,
                JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
            }))
        else:
            span.set_status(StatusCode.OK)

        # Record duration metric
        duration = time.monotonic() - start_time
        tool_duration.record(duration, _with_resource_labels({
            GEN_AI_TOOL_NAME: span_tool_name,
            JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
        }))

        # Enrich skill_tool / skill_complete spans with gen_ai.skill.* attributes (OTel GenAI #86)
        if span_tool_name == "skill_tool":
            tool_msg = getattr(inputs, "tool_msg", None) if inputs else None
            if tool_msg is not None:
                meta = getattr(tool_msg, "metadata", None) or {}
                if isinstance(meta, dict) and (meta.get("is_skill_body") or meta.get("original_is_skill_body")):
                    skill_name = str(meta.get("skill_name") or "")
                    if skill_name:
                        span.set_attribute(GEN_AI_OPERATION_NAME, "load_skill")
                        span.set_attribute(GEN_AI_SKILL_NAME, skill_name)
                        span.set_attribute(GEN_AI_SKILL_ID, f"skill_{hash(skill_name) & 0xFFFFFFFF:08x}")
                        span.add_event("skill.loaded", {
                            "skill.name": skill_name,
                            "skill.path": str(meta.get("relative_file_path") or ""),
                        })
        elif span_tool_name == "skill_complete":
            skill_name = ""
            if inputs is not None:
                tc = getattr(inputs, "tool_call", None)
                if tc is not None:
                    args = getattr(tc, "arguments", None)
                    args = _normalize_tool_args(args)
                    skill_name = str(args.get("skill_name", "") or "")
            span.add_event("skill.released", {"skill.name": skill_name} if skill_name else {})

            # Skill session completion: record duration and error_count
            if skill_name:
                session_id = req_ctx.get("session_id", "")
                session_key = f"skill_{session_id}_{skill_name}"
                session_info = self._skill_sessions.pop(session_key, None)

                if session_info:
                    duration = time.monotonic() - session_info["start_time"]

                    has_error = False
                    tool_result = getattr(inputs, "tool_result", None) if inputs else None
                    if tool_result is not None:
                        if hasattr(tool_result, "error") and getattr(tool_result, "error", None):
                            has_error = True
                        elif isinstance(tool_result, dict) and tool_result.get("error"):
                            has_error = True

                    attrs = {
                        GEN_AI_SKILL_NAME: skill_name,
                        GEN_AI_SKILL_VERSION: session_info.get("skill_version", ""),
                        GEN_AI_SYSTEM: "jiuwenclaw",
                        JIUWENCLAW_CHANNEL_ID: req_ctx["channel_id"],
                    }
                    record_skill_duration(duration, attrs)
                    if has_error:
                        add_skill_error_count(1, attrs)

        span.end()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_role(self, msg: Any) -> str:
        """Extract role from message object or dict."""
        role = getattr(msg, "role", None)
        if role is None and isinstance(msg, dict):
            role = msg.get("role", "unknown")
        return str(role) if role else "unknown"

    def _get_content(self, msg: Any) -> str:
        """Extract content from message object or dict."""
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content", "")
        return str(content) if content else ""

    def _extract_text_content(self, msg: Any) -> str:
        """Extract countable text from any message shape.

        Handles None, str, list (multimodal), and dict messages.
        Image/URL parts are excluded — only text-type parts are counted.
        """
        content = getattr(msg, "content", None)
        if content is None and isinstance(msg, dict):
            content = msg.get("content")
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict) and part.get("type") == "text":
                    parts.append(part.get("text", ""))
                elif hasattr(part, "type") and getattr(part, "type", "") == "text":
                    parts.append(getattr(part, "text", ""))
            return "\n".join(parts)
        return str(content)

    def _count_assistant_extras(self, msg: Any, counter: Any) -> int:
        """Count tokens for assistant-message tool_calls and reasoning_content.

        Returns 0 when counter is None (tiktoken unavailable), since the
        main text body is already counted via len//4 fallback.
        """
        if counter is None:
            return 0

        extra = 0
        tool_calls = getattr(msg, "tool_calls", None)
        if tool_calls is None and isinstance(msg, dict):
            tool_calls = msg.get("tool_calls")
        if tool_calls:
            if hasattr(msg, "model_dump"):
                dict_msg = msg.model_dump()
                tc_json = dict_msg.get("tool_calls", tool_calls)
            else:
                tc_json = tool_calls
            extra += counter.count(json.dumps(tc_json, ensure_ascii=False))
        reasoning = getattr(msg, "reasoning_content", None)
        if reasoning is None and isinstance(msg, dict):
            reasoning = msg.get("reasoning_content")
        if reasoning and isinstance(reasoning, str):
            extra += counter.count(reasoning)
        return extra

    def _count_tokens(self, text: str, counter: Any) -> int:
        """Count tokens using tiktoken counter, falling back to len//4."""
        if counter is not None and text:
            return counter.count(text)
        return len(text) // 4

    def _build_message_entry(self, msg: Any) -> dict:
        """Build a single message entry for gen_ai.input.messages JSON array."""
        role = self._get_role(msg)
        content = self._get_content(msg)

        entry = {
            "role": role,
            "parts": [{"type": "text", "content": content}]
        }

        # Tool message needs tool_call_id
        if role == "tool":
            tool_call_id = getattr(msg, "tool_call_id", "") or ""
            if isinstance(msg, dict):
                tool_call_id = msg.get("tool_call_id", "")
            entry["tool_call_id"] = str(tool_call_id)

        return entry

    def _build_recent_messages(self, messages: list) -> list:
        """Extract key context: system + last tool + last assistant + current user.

        Used when OTEL_LOG_FULL_MESSAGES=false (default mode).
        """
        recent = []

        # 1. System prompt (first message)
        for msg in messages:
            if self._get_role(msg) == "system":
                recent.append(self._build_message_entry(msg))
                break

        # 2. From end: last tool, last assistant, current user
        last_tool = None
        last_assistant = None
        current_user = None

        for msg in reversed(messages):
            role = self._get_role(msg)
            if role == "user" and current_user is None:
                current_user = msg
            elif role == "assistant" and last_assistant is None:
                last_assistant = msg
            elif role == "tool" and last_tool is None:
                last_tool = msg

        if last_tool:
            recent.append(self._build_message_entry(last_tool))
        if last_assistant:
            recent.append(self._build_message_entry(last_assistant))
        if current_user:
            recent.append(self._build_message_entry(current_user))

        return recent

    def _build_full_messages(self, messages: list) -> list:
        """Build full message history with truncation.

        Used when OTEL_LOG_FULL_MESSAGES=true.
        """
        attr_messages = []
        for msg in messages:
            entry = self._build_message_entry(msg)
            # Apply truncation to text parts
            for part in entry.get("parts", []):
                if part.get("type") == "text":
                    content = part.get("content", "")
                    if len(content) > _message_content_max_length:
                        part["content"] = self._smart_truncate(content, _message_content_max_length)
            attr_messages.append(entry)
        return attr_messages

    def _smart_truncate(self, content: str, max_len: int) -> str:
        """Smart truncation: preserve start + end with ellipsis."""
        if len(content) <= max_len:
            return content
        head_len = int(max_len * 0.8)
        tail_len = max_len - head_len - 3
        return content[:head_len] + "..." + content[-tail_len:]

    def _record_input_messages(self, span: trace.Span, messages: Any) -> None:
        """Record input messages as gen_ai.input.messages JSON attribute.

        Follows OpenTelemetry GenAI semantic conventions with configurable
        message collection strategy via environment variables.
        """
        # Always record context metadata (even when message content disabled)
        total_count = len(messages)
        total_length = sum(len(self._get_content(m)) for m in messages)
        span.set_attribute(GEN_AI_INPUT_MESSAGES_COUNT, total_count)
        span.set_attribute(GEN_AI_INPUT_MESSAGES_TOTAL_LENGTH, total_length)

        # Skip content recording if disabled
        if not _log_messages:
            return

        # Choose collection strategy based on env var
        if _log_full_messages:
            attr_messages = self._build_full_messages(messages)
        else:
            attr_messages = self._build_recent_messages(messages)

        # Set as JSON attribute
        span.set_attribute(GEN_AI_INPUT_MESSAGES, json.dumps(attr_messages, ensure_ascii=False))

    def _record_context_composition(self, span: trace.Span, messages: Any) -> dict[str, int]:
        """Record context composition as span attributes and return token counts.

        Skill content is tracked as a first-class category because it spans
        tool messages (skill body) and system messages (skill pin).
        Token counts use tiktoken (cl100k_base) for accuracy, especially
        for Chinese text where len//4 underestimates by 2-3x.
        Falls back to len//4 if tiktoken is unavailable.

        Returns:
            dict with keys: system_prompt, user_messages, assistant_messages,
            tool_results, skill, per_skill_tokens (dict[str, int]),
            message_total (sum of message-based components).
        """
        counter = _get_token_counter()
        tokens = {"skill": 0, "system": 0, "user": 0, "assistant": 0, "tool": 0}
        # Per-skill token breakdown for metric labeling (handles multi-skill contexts)
        per_skill_tokens: dict[str, int] = {}

        for msg in messages:
            role = self._get_role(msg)
            text = self._extract_text_content(msg)
            est = self._count_tokens(text, counter)

            metadata = getattr(msg, "metadata", None) or {}
            if isinstance(msg, dict):
                metadata = msg.get("metadata", {})

            if role == "tool":
                if metadata.get("is_skill_body") or metadata.get("original_is_skill_body"):
                    tokens["skill"] += est
                    s_name = str(metadata.get("skill_name", "") or "")
                    if s_name:
                        per_skill_tokens[s_name] = per_skill_tokens.get(s_name, 0) + est
                else:
                    tokens["tool"] += est
            elif role == "system":
                if metadata.get("active_skill_pin"):
                    tokens["skill"] += est
                    # active_skill_pin value may be skill_name or boolean; prefer metadata.skill_name
                    s_name = str(metadata.get("skill_name", "") or metadata.get("active_skill_pin", "") or "")
                    # Filter out non-name values (active_skill_pin can be True/bool)
                    if s_name and s_name.lower() not in ("true", "false"):
                        per_skill_tokens[s_name] = per_skill_tokens.get(s_name, 0) + est
                else:
                    tokens["system"] += est
            elif role == "assistant":
                tokens["assistant"] += est + self._count_assistant_extras(msg, counter)
            elif role in tokens:
                tokens[role] += est

        span.set_attribute(GEN_AI_CONTEXT_SKILL, tokens["skill"])
        span.set_attribute(GEN_AI_CONTEXT_SYSTEM_PROMPT, tokens["system"])
        span.set_attribute(GEN_AI_CONTEXT_USER_MESSAGES, tokens["user"])
        span.set_attribute(GEN_AI_CONTEXT_ASSISTANT_MESSAGES, tokens["assistant"])
        span.set_attribute(GEN_AI_CONTEXT_TOOL_RESULTS, tokens["tool"])

        message_total = sum(tokens.values())
        return {
            "system_prompt": tokens["system"],
            "user_messages": tokens["user"],
            "assistant_messages": tokens["assistant"],
            "tool_results": tokens["tool"],
            "skill": tokens["skill"],
            "per_skill_tokens": per_skill_tokens,
            "message_total": message_total,
        }

    @staticmethod
    def _serialize_tool_def(tool: Any, idx: int) -> tuple[str, str]:
        """Serialize a single tool definition into its name and framed JSON string.

        Returns (tool_name, piece) where piece is the tiktoken-friendly
        framing: <|start|>functions.{name}:{idx}\n{json}<|end|>

        Handles both dict (OpenAI format) and object (ToolInfo/Tool) shapes.
        """
        if isinstance(tool, dict):
            func_obj = tool.get("function", tool)
            name = func_obj.get("name", "") if isinstance(func_obj, dict) else ""
            json_str = json.dumps(tool, ensure_ascii=False, separators=(",", ":"))
            piece = f"<|start|>functions.{name}:{idx}\n{json_str}<|end|>"
            return name, piece

        # ToolInfo / Tool object: reconstruct canonical format
        name = getattr(tool, "name", "")
        if not name:
            func = getattr(tool, "function", None)
            if func:
                name = getattr(func, "name", "")

        func_obj: dict[str, Any] = {}
        func_obj["name"] = name or ""

        desc = ""
        card = getattr(tool, "card", None)
        if card:
            desc = getattr(card, "description", "")
        if not desc:
            func = getattr(tool, "function", None)
            if func:
                desc = getattr(func, "description", "")
        if not desc:
            desc = getattr(tool, "description", "")
        func_obj["description"] = desc or ""

        parameters = None
        func = getattr(tool, "function", None)
        if func:
            parameters = getattr(func, "parameters", None)
        if parameters is None:
            parameters = getattr(tool, "parameters", None)
        if parameters is not None:
            if isinstance(parameters, type) and hasattr(parameters, "model_json_schema"):
                parameters = parameters.model_json_schema()
            func_obj["parameters"] = parameters

        tool_def = {"type": getattr(tool, "type", "function"), "function": func_obj}
        json_str = json.dumps(tool_def, ensure_ascii=False, separators=(",", ":"))
        piece = f"<|start|>functions.{func_obj['name']}:{idx}\n{json_str}<|end|>"
        return name or "", piece

    def _estimate_tools_tokens(self, tools: Any) -> int:
        """Count tokens for tool definitions using tiktoken.

        Serializes each tool to the standard function-calling JSON format
        (matching what actually gets sent to the LLM), then counts exactly.
        Falls back to len//4 if tiktoken is unavailable.

        P1: Results are cached by tool name tuple — tool definitions rarely
        change between iterations within the same ReAct loop.
        Cache assumption: tool name list is stable across ReAct iterations;
        description/parameters are assumed unchanged when names match.
        If tool definitions change dynamically, the cache may return stale
        values until the TelemetryRail instance is re-created.
        """
        # P1: Cache by tool names — tool definitions rarely change between iterations
        cache_key = tuple(
            getattr(t, "name", "") or (t.get("function", {}).get("name", "") if isinstance(t, dict) else "")
            for t in tools
        )
        if cache_key == self._tools_cache_key:
            return self._tools_cache_value

        counter = _get_token_counter()
        total = 0
        for idx, tool in enumerate(tools):
            _, piece = self._serialize_tool_def(tool, idx)
            if isinstance(tool, dict):
                # Dict tools: len//4 fallback uses raw dict length (no framing overhead)
                char_count = len(json.dumps(tool, ensure_ascii=False))
            else:
                char_count = len(piece)
            total += counter.count(piece) if counter is not None else char_count // 4

        result = total + 3 if counter is not None else total // 4  # Reserve 3 for assistant priming (tiktoken only)
        self._tools_cache_key = cache_key
        self._tools_cache_value = result
        return result

    def _estimate_per_tool_tokens(self, tools: Any) -> dict[str, int]:
        """Count tokens per individual tool definition.

        Returns a dict mapping tool name to its token count, for recording
        gen_ai.tool.token.usage metric with per-tool granularity.
        Uses the same serialization logic as _estimate_tools_tokens.
        """
        counter = _get_token_counter()
        per_tool = {}
        for idx, tool in enumerate(tools):
            name, piece = self._serialize_tool_def(tool, idx)
            name = name or f"_unknown_{idx}"
            if counter is not None:
                per_tool[name] = counter.count(piece)
            else:
                char_count = len(json.dumps(tool, ensure_ascii=False)) if isinstance(tool, dict) else len(piece)
                per_tool[name] = char_count // 4

        return per_tool

    def _record_tools(self, span: trace.Span, tools: Any) -> None:
        """Record available tools as gen_ai.tool.definitions JSON attribute.

        Follows OpenTelemetry GenAI semantic conventions.
        Format: [{"type": "function", "name": "...", "description": "...", "parameters": {...}}]
        """
        tool_defs = []
        for tool in tools:
            tool_def = {}

            if isinstance(tool, dict):
                # OpenAI format
                tool_def["type"] = tool.get("type", "function")
                func = tool.get("function", {})
                if isinstance(func, dict):
                    if func.get("name"):
                        tool_def["name"] = func["name"]
                    if func.get("description"):
                        tool_def["description"] = func["description"]
                    if func.get("parameters"):
                        tool_def["parameters"] = func["parameters"]
            else:
                # Tool object format (LocalFunction, Tool, etc.)
                tool_def["type"] = getattr(tool, "type", "function")
                name = getattr(tool, "name", "")
                if not name:
                    func = getattr(tool, "function", None)
                    if func:
                        name = getattr(func, "name", "")
                if name:
                    tool_def["name"] = name

                # Description from multiple sources
                description = ""
                card = getattr(tool, "card", None)
                if card:
                    description = getattr(card, "description", "")
                elif hasattr(tool, "function"):
                    func = tool.function
                    description = getattr(func, "description", "")
                if not description:
                    description = getattr(tool, "description", "")
                if description:
                    tool_def["description"] = description

                # Parameters
                func = getattr(tool, "function", None)
                if func:
                    params = getattr(func, "parameters", None)
                    if params:
                        tool_def["parameters"] = params

            if tool_def.get("name"):
                tool_defs.append(tool_def)

        if tool_defs:
            span.set_attribute(GEN_AI_TOOL_DEFINITIONS, json.dumps(tool_defs, ensure_ascii=False))

    def _record_output_message(self, span: trace.Span, result: Any) -> None:
        """Record assistant output message as gen_ai.output.messages JSON attribute.

        Follows OpenTelemetry GenAI semantic conventions.
        """
        content = getattr(result, "content", "") or ""
        tool_calls = getattr(result, "tool_calls", None)
        reasoning_content = getattr(result, "reasoning_content", None)

        output_message = {
            "role": "assistant",
            "parts": [{"type": "text", "content": str(content)}]
        }

        # Add tool_calls if present
        if tool_calls:
            tc_list = []
            for tc in tool_calls:
                tc_entry = {
                    "id": getattr(tc, "id", ""),
                    "name": getattr(tc, "name", ""),
                }
                args = getattr(tc, "arguments", None)
                if args:
                    tc_entry["arguments"] = args if isinstance(args, dict) else str(args)
                tc_list.append(tc_entry)
            output_message["tool_calls"] = tc_list

        # Add reasoning content if present
        if reasoning_content:
            output_message["parts"].append({
                "type": "reasoning",
                "content": str(reasoning_content)[:4096]
            })

        span.set_attribute(GEN_AI_OUTPUT_MESSAGES, json.dumps([output_message], ensure_ascii=False))

    def _record_decision(self, span: trace.Span, result: Any) -> None:
        """Record Agent decision as attributes.

        ReAct Agent makes two types of decisions:
        - tool_call: Agent decides to execute tools, continues the loop
        - answer: Agent decides to output text, ends the loop
        """
        tool_calls = getattr(result, "tool_calls", None)
        content = getattr(result, "content", "") or ""

        if tool_calls and len(tool_calls) > 0:
            # Decision: execute tools
            span.set_attribute(GEN_AI_DECISION_TYPE, "tool_call")
            tool_names = [getattr(tc, "name", "") for tc in tool_calls if getattr(tc, "name", "")]
            span.set_attribute(GEN_AI_DECISION_TOOL_NAMES, str(tool_names))
            span.set_attribute(GEN_AI_DECISION_TOOL_COUNT, len(tool_calls))
        else:
            # Decision: output answer
            span.set_attribute(GEN_AI_DECISION_TYPE, "answer")

    def _record_token_usage(
        self,
        span: trace.Span,
        result: Any,
        model_name: str,
        system: str,
        channel_id: str = "",
    ) -> None:
        """Extract token usage from result and record.

        Supports OpenTelemetry GenAI semantic conventions for:
        - Basic tokens: input_tokens, output_tokens
        - Prompt caching: cache_read.input_tokens, cache_creation.input_tokens
        - Reasoning: reasoning.output_tokens (for DeepSeek R1, Claude thinking, etc.)
        """
        usage = getattr(result, "usage_metadata", None)
        # TODO(§11.5): when an estimator provides a fallback count because the
        # adapter did not return usage, set GEN_AI_USAGE_ESTIMATED=True on the
        # span so downstream consumers can distinguish reported vs estimated.
        if not usage:
            return

        # Basic tokens
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0

        # Accumulate for agent-level aggregation on jiuwenclaw.agent.invoke span
        usage_accum = _agent_token_usage.get()
        if usage_accum is not None:
            usage_accum["input_tokens"] += input_tokens
            usage_accum["output_tokens"] += output_tokens
            _agent_token_usage.set(usage_accum)

        # Cache read tokens - try multiple field names from different APIs
        # OpenAI: usage.prompt_tokens_details.cached_tokens
        # Anthropic: usage.cache_read_input_tokens
        # GLM/Zhipu: usage.cache_tokens
        cache_read = 0
        prompt_details = getattr(usage, "prompt_tokens_details", None)
        if prompt_details:
            cache_read = getattr(prompt_details, "cached_tokens", 0) or 0
        if not cache_read:
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        if not cache_read:
            cache_read = getattr(usage, "cache_tokens", 0) or 0

        # Cache creation tokens
        cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0

        # Reasoning tokens (for models like DeepSeek R1, Claude thinking)
        reasoning_tokens = 0
        completion_details = getattr(usage, "completion_tokens_details", None)
        if completion_details:
            reasoning_tokens = getattr(completion_details, "reasoning_tokens", 0) or 0
        if not reasoning_tokens:
            reasoning_tokens = getattr(usage, "reasoning_tokens", 0) or 0

        # Record standard OpenTelemetry attributes
        span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
        span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
        span.set_attribute(GEN_AI_USAGE_TOTAL_TOKENS, input_tokens + output_tokens)

        # Prompt caching tokens (OpenTelemetry standard names)
        if cache_read:
            span.set_attribute(GEN_AI_USAGE_CACHE_READ_INPUT_TOKENS, cache_read)
            span.set_attribute(GEN_AI_USAGE_CACHE_READ_TOKENS, cache_read)  # legacy compat
        if cache_creation:
            span.set_attribute(GEN_AI_USAGE_CACHE_CREATION_INPUT_TOKENS, cache_creation)

        # Reasoning tokens
        if reasoning_tokens:
            span.set_attribute(GEN_AI_USAGE_REASONING_OUTPUT_TOKENS, reasoning_tokens)

        # Metric counters
        base_attrs = _with_resource_labels({
            GEN_AI_REQUEST_MODEL: model_name,
            GEN_AI_SYSTEM: system,
            JIUWENCLAW_CHANNEL_ID: channel_id,
        })
        if input_tokens:
            token_usage.add(input_tokens, {**base_attrs, "gen_ai.token.type": "input"})
        if output_tokens:
            token_usage.add(output_tokens, {**base_attrs, "gen_ai.token.type": "output"})
        if cache_read:
            token_usage.add(cache_read, {**base_attrs, "gen_ai.token.type": "cache_read"})
        if cache_creation:
            token_usage.add(cache_creation, {**base_attrs, "gen_ai.token.type": "cache_creation"})
        if reasoning_tokens:
            token_usage.add(reasoning_tokens, {**base_attrs, "gen_ai.token.type": "reasoning"})
