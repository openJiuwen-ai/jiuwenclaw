# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Metric instrument definitions for JiuWenClaw telemetry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation

if TYPE_CHECKING:
    from opentelemetry.sdk.metrics import Counter, Histogram

_meter: metrics.Meter | None = None
_session_active_observer: Callable[[], int] | None = None
_queue_depth_observer: Callable[[], list[Observation]] | None = None

# Cached instrument instances
_request_duration: Histogram | None = None
_agent_duration: Histogram | None = None
_llm_duration: Histogram | None = None
_tool_duration: Histogram | None = None
_request_count: Counter | None = None
_request_error_count: Counter | None = None
_llm_call_count: Counter | None = None
_token_usage: Counter | None = None
_tool_call_count: Counter | None = None
_tool_error_count: Counter | None = None
_session_active: metrics.ObservableGauge | None = None
_session_created_count: Counter | None = None
_session_state_count: Counter | None = None
_session_stuck_count: Counter | None = None
_session_stuck_age: Histogram | None = None
_queue_depth: metrics.ObservableGauge | None = None
_queue_enqueued: Counter | None = None
_queue_dequeued: Counter | None = None
_queue_wait_duration: Histogram | None = None
_message_processed: Counter | None = None


def _get_meter() -> metrics.Meter:
    """Get or create the meter lazily after MeterProvider is installed."""
    global _meter
    if _meter is None:
        _meter = metrics.get_meter("jiuwenclaw")
    return _meter


def set_session_active_observer(observer: Callable[[], int] | None) -> None:
    """Register the current active-session observer used by the session gauge."""
    global _session_active_observer
    _session_active_observer = observer


def set_queue_depth_observer(observer: Callable[[], list[Observation]] | None) -> None:
    """Register the queue depth observer used by the queue gauge."""
    global _queue_depth_observer
    _queue_depth_observer = observer


def _observe_session_active(_options: CallbackOptions) -> Iterable[Observation]:
    observer = _session_active_observer
    if observer is None:
        return []
    try:
        active_sessions = max(int(observer()), 0)
    except Exception:
        return []
    return [Observation(active_sessions)]


def _observe_queue_depth(_options: CallbackOptions) -> Iterable[Observation]:
    observer = _queue_depth_observer
    if observer is None:
        return []
    try:
        return observer()
    except Exception:
        return []


# ----- Histograms -----
def request_duration() -> Histogram:
    """End-to-end request processing duration."""
    global _request_duration
    if _request_duration is None:
        _request_duration = _get_meter().create_histogram(
            name="jiuwenclaw.request.duration",
            unit="s",
            description="End-to-end request processing duration",
        )
    return _request_duration


def agent_duration() -> Histogram:
    """Agent invoke duration."""
    global _agent_duration
    if _agent_duration is None:
        _agent_duration = _get_meter().create_histogram(
            name="jiuwenclaw.agent.duration",
            unit="s",
            description="Agent invoke duration",
        )
    return _agent_duration


def llm_duration() -> Histogram:
    """GenAI LLM call duration."""
    global _llm_duration
    if _llm_duration is None:
        _llm_duration = _get_meter().create_histogram(
            name="gen_ai.client.operation.duration",
            unit="s",
            description="GenAI LLM call duration",
        )
    return _llm_duration


def tool_duration() -> Histogram:
    """Tool execution duration."""
    global _tool_duration
    if _tool_duration is None:
        _tool_duration = _get_meter().create_histogram(
            name="gen_ai.tool.duration",
            unit="s",
            description="Tool execution duration",
        )
    return _tool_duration


# --- Counters ---
def request_count() -> Counter:
    """Total request count."""
    global _request_count
    if _request_count is None:
        _request_count = _get_meter().create_counter(
            name="jiuwenclaw.request.count",
            unit="{request}",
            description="Total request count",
        )
    return _request_count


def request_error_count() -> Counter:
    """Failed request count."""
    global _request_error_count
    if _request_error_count is None:
        _request_error_count = _get_meter().create_counter(
            name="jiuwenclaw.request.error.count",
            unit="{request}",
            description="Failed request count",
        )
    return _request_error_count


def llm_call_count() -> Counter:
    """LLM call count."""
    global _llm_call_count
    if _llm_call_count is None:
        _llm_call_count = _get_meter().create_counter(
            name="gen_ai.client.operation.count",
            unit="{call}",
            description="LLM call count",
        )
    return _llm_call_count


def token_usage() -> Counter:
    """Token usage by type (input/output/cache)."""
    global _token_usage
    if _token_usage is None:
        _token_usage = _get_meter().create_counter(
            name="gen_ai.client.token.usage",
            unit="{token}",
            description="Token usage by type (input/output/cache)",
        )
    return _token_usage


def tool_call_count() -> Counter:
    """Tool call count."""
    global _tool_call_count
    if _tool_call_count is None:
        _tool_call_count = _get_meter().create_counter(
            name="gen_ai.tool.call.count",
            unit="{call}",
            description="Tool call count",
        )
    return _tool_call_count


def tool_error_count() -> Counter:
    """Tool call error count."""
    global _tool_error_count
    if _tool_error_count is None:
        _tool_error_count = _get_meter().create_counter(
            name="gen_ai.tool.error.count",
            unit="{call}",
            description="Tool call error count",
        )
    return _tool_error_count


# --- Session metrics ---
def session_active() -> metrics.ObservableGauge:
    """Current active session count."""
    global _session_active
    if _session_active is None:
        _session_active = _get_meter().create_observable_gauge(
            name="jiuwenclaw.session.active",
            callbacks=[_observe_session_active],
            unit="{session}",
            description="Current active session count",
        )
    return _session_active


def session_created_count() -> Counter:
    """Total created session count."""
    global _session_created_count
    if _session_created_count is None:
        _session_created_count = _get_meter().create_counter(
            name="jiuwenclaw.session.created.count",
            unit="{session}",
            description="Total created session count",
        )
    return _session_created_count


def session_state_count() -> Counter:
    """Session state transition count."""
    global _session_state_count
    if _session_state_count is None:
        _session_state_count = _get_meter().create_counter(
            name="jiuwenclaw.session.state",
            unit="{transition}",
            description="Session state transition count",
        )
    return _session_state_count


def session_stuck_count() -> Counter:
    """Session stuck occurrence count."""
    global _session_stuck_count
    if _session_stuck_count is None:
        _session_stuck_count = _get_meter().create_counter(
            name="jiuwenclaw.session.stuck",
            unit="{occurrence}",
            description="Session stuck occurrence count",
        )
    return _session_stuck_count


def session_stuck_age() -> Histogram:
    """Duration a session has been stuck."""
    global _session_stuck_age
    if _session_stuck_age is None:
        _session_stuck_age = _get_meter().create_histogram(
            name="jiuwenclaw.session.stuck_age_ms",
            unit="ms",
            description="Duration a session has been stuck",
        )
    return _session_stuck_age


# --- Queue metrics ---
def queue_depth() -> metrics.ObservableGauge:
    """Current message count in queue."""
    global _queue_depth
    if _queue_depth is None:
        _queue_depth = _get_meter().create_observable_gauge(
            name="jiuwenclaw.queue.depth",
            callbacks=[_observe_queue_depth],
            unit="{message}",
            description="Current message count in queue",
        )
    return _queue_depth


def queue_enqueued() -> Counter:
    """Total enqueued message count."""
    global _queue_enqueued
    if _queue_enqueued is None:
        _queue_enqueued = _get_meter().create_counter(
            name="jiuwenclaw.queue.enqueued",
            unit="{message}",
            description="Total enqueued message count",
        )
    return _queue_enqueued


def queue_dequeued() -> Counter:
    """Total dequeued message count."""
    global _queue_dequeued
    if _queue_dequeued is None:
        _queue_dequeued = _get_meter().create_counter(
            name="jiuwenclaw.queue.dequeued",
            unit="{message}",
            description="Total dequeued message count",
        )
    return _queue_dequeued


def queue_wait_duration() -> Histogram:
    """Message wait duration in queue."""
    global _queue_wait_duration
    if _queue_wait_duration is None:
        _queue_wait_duration = _get_meter().create_histogram(
            name="jiuwenclaw.queue.wait_duration",
            unit="ms",
            description="Message wait duration in queue",
        )
    return _queue_wait_duration


def message_processed() -> Counter:
    """Processed message count by status."""
    global _message_processed
    if _message_processed is None:
        _message_processed = _get_meter().create_counter(
            name="jiuwenclaw.message.processed",
            unit="{message}",
            description="Processed message count by status",
        )
    return _message_processed
