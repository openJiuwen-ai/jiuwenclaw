# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Metric instrument definitions for JiuWenClaw telemetry."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

_meter = metrics.get_meter("jiuwenclaw")
_session_active_observer: Callable[[], int] | None = None


def set_session_active_observer(observer: Callable[[], int] | None) -> None:
    """Register the current active-session observer used by the session gauge."""
    global _session_active_observer
    _session_active_observer = observer


def _observe_session_active(_options: CallbackOptions) -> Iterable[Observation]:
    observer = _session_active_observer
    if observer is None:
        return []

    try:
        active_sessions = max(int(observer()), 0)
    except Exception:
        return []

    return [Observation(active_sessions)]

# --- Histograms ---
request_duration = _meter.create_histogram(
    name="jiuwenclaw.request.duration",
    unit="s",
    description="End-to-end request processing duration",
)

agent_duration = _meter.create_histogram(
    name="jiuwenclaw.agent.duration",
    unit="s",
    description="Agent invoke duration",
)

llm_duration = _meter.create_histogram(
    name="gen_ai.client.operation.duration",
    unit="s",
    description="GenAI LLM call duration",
)

tool_duration = _meter.create_histogram(
    name="gen_ai.tool.duration",
    unit="s",
    description="Tool execution duration",
)

# --- Counters ---
request_count = _meter.create_counter(
    name="jiuwenclaw.request.count",
    unit="{request}",
    description="Total request count",
)

request_error_count = _meter.create_counter(
    name="jiuwenclaw.request.error.count",
    unit="{request}",
    description="Failed request count",
)

llm_call_count = _meter.create_counter(
    name="gen_ai.client.operation.count",
    unit="{call}",
    description="LLM call count",
)

token_usage = _meter.create_counter(
    name="gen_ai.client.token.usage",
    unit="{token}",
    description="Token usage by type (input/output/cache)",
)

tool_call_count = _meter.create_counter(
    name="gen_ai.tool.call.count",
    unit="{call}",
    description="Tool call count",
)

tool_error_count = _meter.create_counter(
    name="gen_ai.tool.error.count",
    unit="{call}",
    description="Tool call error count",
)

# --- Session metrics ---
session_active = _meter.create_observable_gauge(
    name="jiuwenclaw.session.active",
    callbacks=[_observe_session_active],
    unit="{session}",
    description="Current active session count",
)

session_created_count = _meter.create_counter(
    name="jiuwenclaw.session.created.count",
    unit="{session}",
    description="Total created session count",
)

session_state_count = _meter.create_counter(
    name="jiuwenclaw.session.state",
    unit="{transition}",
    description="Session state transition count",
)

session_stuck_count = _meter.create_counter(
    name="jiuwenclaw.session.stuck",
    unit="{occurrence}",
    description="Session stuck occurrence count",
)

session_stuck_age = _meter.create_histogram(
    name="jiuwenclaw.session.stuck_age_ms",
    unit="ms",
    description="Duration a session has been stuck",
)

# --- Queue metrics ---
_queue_depth_observer: Callable[[], list[Observation]] | None = None


def set_queue_depth_observer(observer: Callable[[], list[Observation]] | None) -> None:
    """Register the queue depth observer used by the queue gauge."""
    global _queue_depth_observer
    _queue_depth_observer = observer


def _observe_queue_depth(_options: CallbackOptions) -> Iterable[Observation]:
    observer = _queue_depth_observer
    if observer is None:
        return []
    try:
        return observer()
    except Exception:
        return []


queue_depth = _meter.create_observable_gauge(
    name="jiuwenclaw.queue.depth",
    callbacks=[_observe_queue_depth],
    unit="{message}",
    description="Current message count in queue",
)

queue_enqueued = _meter.create_counter(
    name="jiuwenclaw.queue.enqueued",
    unit="{message}",
    description="Total enqueued message count",
)

queue_dequeued = _meter.create_counter(
    name="jiuwenclaw.queue.dequeued",
    unit="{message}",
    description="Total dequeued message count",
)

queue_wait_duration = _meter.create_histogram(
    name="jiuwenclaw.queue.wait_duration",
    unit="ms",
    description="Message wait duration in queue",
)

message_processed = _meter.create_counter(
    name="jiuwenclaw.message.processed",
    unit="{message}",
    description="Processed message count by status",
)
