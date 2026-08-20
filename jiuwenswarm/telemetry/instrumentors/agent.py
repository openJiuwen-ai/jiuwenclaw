# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Instrumentor for JiuWenClaw.process_message / process_message_stream — AGENT span."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from opentelemetry import context, trace
from opentelemetry.trace import SpanKind, StatusCode

from jiuwenswarm.utils import logger
from jiuwenswarm.telemetry.attributes import (
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_SPAN_TYPE,
    JIUWENCLAW_AGENT_NAME,
    JIUWENCLAW_BOT_ID,
    JIUWENCLAW_CHANNEL_ID,
    JIUWENCLAW_GROUP_ID,
    JIUWENCLAW_REQUEST_ID,
    JIUWENCLAW_SESSION_ID,
    JIUWENCLAW_USER_ID,
)
from jiuwenswarm.telemetry.context_propagation import extract_trace_context
from jiuwenswarm.telemetry.metrics import record_agent_duration

_tracer = trace.get_tracer("jiuwenclaw.agent")


@dataclass
class RoutingCtx:
    """Routing identity fields for a single request."""
    channel_id: str = ""
    session_id: str = ""
    request_id: str = ""
    user_id: str = ""
    group_id: str = ""
    bot_id: str = ""


def instrument_agent() -> None:
    """Monkey-patch JiuWenClaw to create agent spans with cross-WebSocket context propagation."""
    try:
        from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenClaw
    except ImportError:
        logger.debug("[Telemetry] JiuWenClaw not available, skipping agent instrumentor")
        return

    _original_process_message = JiuWenClaw.process_message
    _original_process_message_stream = JiuWenClaw.process_message_stream

    async def _traced_process_message(self, request):
        parent_ctx = extract_trace_context(request.metadata)
        with _tracer.start_as_current_span(
            "jiuwenclaw.agent.invoke",
            context=parent_ctx,
            kind=SpanKind.SERVER,
            attributes=_build_attrs(self, request),
        ) as span:
            _store_agent_ctx(
                self, trace.set_span_in_context(span),
                RoutingCtx(
                    channel_id=request.channel_id or "",
                    session_id=request.session_id or "",
                    request_id=request.request_id or "",
                    user_id=_resolve_routing_field(request, "user_id"),
                    group_id=_resolve_routing_field(request, "group_id"),
                    bot_id=_resolve_routing_field(request, "bot_id"),
                ),
            )
            start = time.monotonic()
            try:
                result = await _original_process_message(self, request)
                span.set_status(StatusCode.OK)
                return result
            except Exception as exc:
                span.set_status(StatusCode.ERROR, str(exc)[:256])
                span.record_exception(exc)
                raise
            finally:
                duration = time.monotonic() - start
                record_agent_duration(duration, {
                    JIUWENCLAW_AGENT_NAME: getattr(self, "_agent_name", ""),
                    JIUWENCLAW_CHANNEL_ID: request.channel_id or "",
                })

    async def _traced_process_message_stream(self, request):
        parent_ctx = extract_trace_context(request.metadata)
        span = _tracer.start_span(
            "jiuwenclaw.agent.invoke.stream",
            context=parent_ctx,
            kind=SpanKind.SERVER,
            attributes=_build_attrs(self, request),
        )
        ctx = trace.set_span_in_context(span)
        _store_agent_ctx(
            self, ctx,
            RoutingCtx(
                channel_id=request.channel_id or "",
                session_id=request.session_id or "",
                request_id=request.request_id or "",
                user_id=_resolve_routing_field(request, "user_id"),
                group_id=_resolve_routing_field(request, "group_id"),
                bot_id=_resolve_routing_field(request, "bot_id"),
            ),
        )
        token = context.attach(ctx)
        start = time.monotonic()
        try:
            async for chunk in _original_process_message_stream(self, request):
                yield chunk
            span.set_status(StatusCode.OK)
        except Exception as exc:
            span.set_status(StatusCode.ERROR, str(exc)[:256])
            span.record_exception(exc)
            raise
        finally:
            duration = time.monotonic() - start
            record_agent_duration(duration, {
                JIUWENCLAW_AGENT_NAME: getattr(self, "_agent_name", ""),
                JIUWENCLAW_CHANNEL_ID: request.channel_id or "",
            })
            span.end()
            context.detach(token)

    JiuWenClaw.process_message = _traced_process_message
    JiuWenClaw.process_message_stream = _traced_process_message_stream


def _store_agent_ctx(
    jiuwenclaw_server, ctx, routing: RoutingCtx,
) -> None:
    """Store agent span context and routing fields on the JiuClawReActAgent instance.

    JiuWenClaw._instance is JiuClawReActAgent — LLM/tool instrumentors
    read self.otel_agent_ctx, self.otel_channel_id, self.otel_session_id
    and self.otel_request_id from that same instance. Routing identity
    (user_id/group_id/bot_id) is also exposed so the LLM instrumentor can
    attach them as metric labels on gen_ai.client.token.usage.
    """
    instance = getattr(jiuwenclaw_server, "_instance", None)
    if instance is not None:
        instance.otel_agent_ctx = ctx
        instance.otel_channel_id = routing.channel_id
        instance.otel_session_id = routing.session_id
        instance.otel_request_id = routing.request_id
        instance.otel_user_id = routing.user_id
        instance.otel_group_id = routing.group_id
        instance.otel_bot_id = routing.bot_id


def _coerce_routing_field(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return ""
    return str(value).strip()


def _routing_field_sources(request: Any) -> list[dict[str, Any]]:
    """Collect routing field sources in priority order: params → metadata → metadata.query."""
    sources: list[dict[str, Any]] = []
    params = getattr(request, "params", None)
    if isinstance(params, dict):
        sources.append(params)
    metadata = getattr(request, "metadata", None)
    if isinstance(metadata, dict):
        sources.append(metadata)
        query = metadata.get("query")
        if isinstance(query, dict):
            sources.append(query)
    return sources


def _resolve_routing_field(request: Any, field: str) -> str:
    """Extract a routing field (user_id / group_id / bot_id) from the request.

    Mirrors manager_ws_client.core.enterprise_config.loader._resolve_routing_field
    so the core telemetry package can stay decoupled from the EE gateway extension.
    ``chat_id`` is used as a fallback for ``group_id`` to match gateway behavior.
    """
    for source in _routing_field_sources(request):
        if field not in source:
            continue
        coerced = _coerce_routing_field(source[field])
        if coerced:
            return coerced
    if field == "group_id":
        return _coerce_routing_field(getattr(request, "chat_id", None))
    return ""


def _build_attrs(agent_server, request) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        JIUWENCLAW_AGENT_NAME: getattr(agent_server, "_agent_name", ""),
        JIUWENCLAW_SESSION_ID: request.session_id or "",
        JIUWENCLAW_CHANNEL_ID: request.channel_id or "",
        JIUWENCLAW_REQUEST_ID: request.request_id or "",
        GEN_AI_AGENT_NAME: getattr(agent_server, "_agent_name", ""),
        GEN_AI_CONVERSATION_ID: request.session_id or "",
        GEN_AI_SPAN_TYPE: "agent",
    }
    user_id = _resolve_routing_field(request, "user_id")
    group_id = _resolve_routing_field(request, "group_id")
    bot_id = _resolve_routing_field(request, "bot_id")
    if user_id:
        attrs[JIUWENCLAW_USER_ID] = user_id
    if group_id:
        attrs[JIUWENCLAW_GROUP_ID] = group_id
    if bot_id:
        attrs[JIUWENCLAW_BOT_ID] = bot_id
    return attrs
