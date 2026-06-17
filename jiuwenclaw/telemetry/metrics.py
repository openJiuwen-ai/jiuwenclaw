# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Metric instrument definitions for JiuWenClaw telemetry."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from contextvars import ContextVar

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.resources import Resource

from jiuwenclaw.extensions.identity_provider import IdentityStore
from jiuwenclaw.telemetry.attributes import JIUWENCLAW_CLAW_ID, JIUWENCLAW_SESSION_ID

_meter = metrics.get_meter("jiuwenclaw")
_session_active_observer: Callable[[], int] | None = None

# Global Resource reference for metric attributes
_resource: Resource | None = None

# Request-scoped session_id for metric labels.
# Set by TelemetryRail.set_telemetry_context(), read by _resource_metric_labels().
# Decouples metrics.py from telemetry_rail.py (no circular import).
metrics_session_id: ContextVar[str] = ContextVar("metrics_session_id", default="")


def set_resource(resource: Resource | None) -> None:
    """设置 Resource 引用，用于从 Resource 获取属性."""
    global _resource
    _resource = resource


def _resource_metric_labels() -> dict:
    """Build Resource-derived + request-scoped metric attributes."""
    labels = {}
    if _resource is not None:
        claw_id = _resource.attributes.get(JIUWENCLAW_CLAW_ID)
        if claw_id is not None:
            labels[JIUWENCLAW_CLAW_ID] = claw_id
    # Inject session_id from metrics-scoped ContextVar (set by TelemetryRail.set_telemetry_context)
    session_id = metrics_session_id.get()
    if session_id:
        labels[JIUWENCLAW_SESSION_ID] = session_id
    return labels


def _identity_metric_labels() -> dict:
    """Build IdentityStore-derived metric attributes."""
    try:
        identity = IdentityStore.get_instance().get_identity()
    except Exception:
        return {}

    if identity is None:
        return {}

    labels = {}
    if identity.user_id is not None:
        labels["user_id"] = identity.user_id
    if identity.domain_id is not None:
        labels["domain_id"] = identity.domain_id
    if identity.app_id is not None:
        labels["app_id"] = identity.app_id
    for k, v in identity.extra.items():
        if isinstance(v, str):
            labels[k] = v
    return labels


def _identity_span_attrs() -> dict[str, str]:
    """Return identity attributes for spans (user.id/domain.id/app.id).

    Reads from IdentityStore singleton. Only adds attributes when values
    are non-None to avoid empty/null dimensions (consistent with metrics).

    Returns:
        dict with 'user.id', 'domain.id', 'app.id' keys (only non-None values)
    """
    try:
        identity = IdentityStore.get_instance().get_identity()
    except Exception:
        return {}

    if identity is None:
        return {}

    attrs = {}
    if identity.user_id is not None:
        attrs["user.id"] = identity.user_id
    if identity.domain_id is not None:
        attrs["domain.id"] = identity.domain_id
    if identity.app_id is not None:
        attrs["app.id"] = identity.app_id
    return attrs


def _with_common_labels(attrs: dict) -> dict:
    """Inject common Resource and identity labels into metric attributes."""
    result = dict(attrs)
    result.update(_resource_metric_labels())
    result.update(_identity_metric_labels())
    return result


def _with_resource_labels(attrs: dict) -> dict:
    """Compatibility wrapper for common metric labels injection."""
    return _with_common_labels(attrs)


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

    return [Observation(active_sessions, attributes=_with_common_labels({}))]

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

# --- Recorder helpers with resource labels injection ---


def record_agent_duration(duration: float, attrs: dict) -> None:
    agent_duration.record(duration, _with_resource_labels(attrs))


def record_request_duration(duration: float, attrs: dict) -> None:
    request_duration.record(duration, _with_resource_labels(attrs))


def record_llm_duration(duration: float, attrs: dict) -> None:
    llm_duration.record(duration, _with_resource_labels(attrs))


def record_tool_duration(duration: float, attrs: dict) -> None:
    tool_duration.record(duration, _with_resource_labels(attrs))


def record_session_stuck_age(age_ms: float, attrs: dict) -> None:
    session_stuck_age.record(age_ms, _with_resource_labels(attrs))


def add_request_count(value: int, attrs: dict) -> None:
    request_count.add(value, _with_resource_labels(attrs))


def add_request_error_count(value: int, attrs: dict) -> None:
    request_error_count.add(value, _with_resource_labels(attrs))


def add_llm_call_count(value: int, attrs: dict) -> None:
    llm_call_count.add(value, _with_resource_labels(attrs))


def add_token_usage(value: int, attrs: dict) -> None:
    token_usage.add(value, _with_resource_labels(attrs))


def add_tool_call_count(value: int, attrs: dict) -> None:
    tool_call_count.add(value, _with_resource_labels(attrs))


def add_tool_error_count(value: int, attrs: dict) -> None:
    tool_error_count.add(value, _with_resource_labels(attrs))


def add_session_created_count(value: int) -> None:
    session_created_count.add(value, _with_resource_labels({}))


def add_session_state_count(value: int, attrs: dict) -> None:
    session_state_count.add(value, _with_resource_labels(attrs))


def add_session_stuck_count(value: int, attrs: dict) -> None:
    session_stuck_count.add(value, _with_resource_labels(attrs))


# --- Skill metrics ---
skill_call_count = _meter.create_counter(
    name="gen_ai.skill.call.count",
    unit="{call}",
    description="Number of skill activations",
)

skill_duration = _meter.create_histogram(
    name="gen_ai.skill.duration",
    unit="s",
    description="Skill session duration from activation to completion",
    explicit_bucket_boundaries_advisory=[1, 5, 10, 30, 60, 120, 300],
)

skill_error_count = _meter.create_counter(
    name="gen_ai.skill.error.count",
    unit="{call}",
    description="Number of errors during skill execution",
)


def add_skill_call_count(value: int, attrs: dict) -> None:
    skill_call_count.add(value, _with_resource_labels(attrs))


def record_skill_duration(duration: float, attrs: dict) -> None:
    skill_duration.record(duration, _with_resource_labels(attrs))


def add_skill_error_count(value: int, attrs: dict) -> None:
    skill_error_count.add(value, _with_resource_labels(attrs))


# --- Tool & skill token usage metrics ---
tool_token_usage = _meter.create_counter(
    name="gen_ai.tool.token.usage",
    unit="{token}",
    description="Token usage per tool definition consumed in LLM call",
)

skill_token_usage = _meter.create_counter(
    name="gen_ai.skill.token.usage",
    unit="{token}",
    description="Token usage consumed by skill content (body + pin) in LLM call",
)


def add_tool_token_usage(value: int, attrs: dict) -> None:
    tool_token_usage.add(value, _with_resource_labels(attrs))


def add_skill_token_usage(value: int, attrs: dict) -> None:
    skill_token_usage.add(value, _with_resource_labels(attrs))


# --- TTFT (Time to First Token) ---
first_token_duration = _meter.create_histogram(
    name="gen_ai.client.token.first_token_duration",
    unit="ms",
    description="Duration from agent invoke to first streaming token (TTFT)",
    explicit_bucket_boundaries_advisory=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
)


def record_first_token_duration(duration: float, attrs: dict) -> None:
    first_token_duration.record(duration, _with_resource_labels(attrs))
