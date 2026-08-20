# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Metric instrument definitions for JiuWenClaw telemetry."""

from __future__ import annotations
from dataclasses import dataclass

from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from threading import Lock
from typing import Any, Union
from weakref import WeakKeyDictionary

from opentelemetry import metrics
from opentelemetry.metrics import (
    CallbackOptions,
    Histogram,
    Meter,
    MeterProvider,
    Observation,
)
from opentelemetry.sdk.resources import Resource

from jiuwenswarm.telemetry.attributes import (
    GEN_AI_REQUEST_MODEL,
    GEN_AI_SYSTEM,
    JIUWENCLAW_BOT_ID,
    JIUWENCLAW_CHANNEL_ID,
    JIUWENCLAW_CLAW_ID,
    JIUWENCLAW_GROUP_ID,
    JIUWENCLAW_SESSION_ID,
    JIUWENCLAW_USER_ID,
)

MetricKind = str

_meter = metrics.get_meter("jiuwenclaw")
_session_active_observer: Callable[[], int] | None = None

# Global Resource reference for metric attributes
_resource: Resource | None = None


def set_resource(resource: Resource | None) -> None:
    """设置 Resource 引用，用于从 Resource 获取 claw_id."""
    global _resource
    _resource = resource


def _with_resource_labels(attrs: dict) -> dict:
    """将 claw.id 注入到 metric attributes 中（从 Resource 获取）."""
    result = dict(attrs)
    if _resource is not None:
        # 从 Resource.attributes 获取 claw_id
        claw_id = _resource.attributes.get(JIUWENCLAW_CLAW_ID)
        if claw_id is not None:
            result[JIUWENCLAW_CLAW_ID] = claw_id
    return result


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

    # Add claw.id to attributes
    attrs = {}
    if _resource is not None:
        claw_id = _resource.attributes.get(JIUWENCLAW_CLAW_ID)
        if claw_id is not None:
            attrs[JIUWENCLAW_CLAW_ID] = claw_id

    return [Observation(active_sessions, attributes=attrs)]

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
        observations = observer()
        # Wrap each Observation with claw.id
        wrapped = []
        for obs in observations:
            attrs = dict(obs.attributes or {})
            if _resource is not None:
                claw_id = _resource.attributes.get(JIUWENCLAW_CLAW_ID)
                if claw_id is not None:
                    attrs[JIUWENCLAW_CLAW_ID] = claw_id
            wrapped.append(Observation(obs.value, attributes=attrs))
        return wrapped
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

# --- Recorder helpers with resource labels injection ---


def record_agent_duration(duration: float, attrs: dict) -> None:
    agent_duration.record(duration, _with_resource_labels(attrs))


def record_request_duration(duration: float, attrs: dict) -> None:
    request_duration.record(duration, _with_resource_labels(attrs))


def record_llm_duration(duration: float, attrs: dict) -> None:
    llm_duration.record(duration, _with_resource_labels(attrs))


def record_tool_duration(duration: float, attrs: dict) -> None:
    tool_duration.record(duration, _with_resource_labels(attrs))


def record_queue_wait_duration(duration_ms: float, attrs: dict) -> None:
    queue_wait_duration.record(duration_ms, _with_resource_labels(attrs))


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


def add_queue_enqueued(value: int, attrs: dict) -> None:
    queue_enqueued.add(value, _with_resource_labels(attrs))


def add_queue_dequeued(value: int, attrs: dict) -> None:
    queue_dequeued.add(value, _with_resource_labels(attrs))


def add_message_processed(value: int, attrs: dict) -> None:
    message_processed.add(value, _with_resource_labels(attrs))


def record_genai_token_usage(
    *,
    input_tokens: int = 0,
    output_tokens: int = 0,
    model_name: str = "",
    system: str = "unknown",
    channel_id: str = "",
    user_id: str = "",
    group_id: str = "",
    bot_id: str = "",
) -> None:
    """Record gen_ai.client.token.usage (input/output) for one LLM-driven agent step.

    Fallback used when TelemetryRail cannot extract usage_metadata from the
    streaming result (deep-agent / SDK-adapter mode: usage arrives via the
    llm_usage chunk, not on the result object). TelemetryRail still records
    operation.count / operation.duration / agent.duration via its callback hooks;
    this function only fills the token-usage gap.

    Records: gen_ai.client.token.usage (input/output).

    The routing identity labels (user_id / group_id / bot_id) are attached so
    the observability dashboard can break token usage down by these dimensions.
    Empty values are dropped to avoid creating a "" bucket that pollutes
    cardinality.
    """
    base = {
        GEN_AI_REQUEST_MODEL: model_name,
        GEN_AI_SYSTEM: system,
        JIUWENCLAW_CHANNEL_ID: channel_id,
    }
    if user_id:
        base[JIUWENCLAW_USER_ID] = user_id
    if group_id:
        base[JIUWENCLAW_GROUP_ID] = group_id
    if bot_id:
        base[JIUWENCLAW_BOT_ID] = bot_id
    if input_tokens:
        add_token_usage(input_tokens, {**base, "gen_ai.token.type": "input"})
    if output_tokens:
        add_token_usage(output_tokens, {**base, "gen_ai.token.type": "output"})


@dataclass
class MetricSpec:
    kind: MetricKind
    unit: str
    description: str


METRIC_SPECS: dict[str, MetricSpec] = {
    "jiuwenclaw.request.duration": MetricSpec("histogram", "s", "Request duration"),
    "jiuwenclaw.request.count": MetricSpec("counter", "{request}", "Requests"),
    "jiuwenclaw.request.error.count": MetricSpec(
        "counter", "{request}", "Request errors"
    ),
    "jiuwenclaw.agent.duration": MetricSpec("histogram", "s", "Agent duration"),
    "gen_ai.client.operation.duration": MetricSpec("histogram", "s", "LLM duration"),
    "gen_ai.client.operation.count": MetricSpec("counter", "{call}", "LLM calls"),
    "gen_ai.client.token.usage": MetricSpec("counter", "{token}", "LLM tokens"),
    "gen_ai.tool.duration": MetricSpec("histogram", "s", "Tool duration"),
    "gen_ai.tool.call.count": MetricSpec("counter", "{call}", "Tool calls"),
    "gen_ai.tool.error.count": MetricSpec("counter", "{call}", "Tool errors"),
    "jiuwenclaw.session.active": MetricSpec(
        "observable_gauge", "{session}", "Active sessions"
    ),
    "jiuwenclaw.session.created.count": MetricSpec(
        "counter", "{session}", "Created sessions"
    ),
    "jiuwenclaw.session.state": MetricSpec(
        "counter", "{transition}", "Session transitions"
    ),
    "jiuwenclaw.session.stuck": MetricSpec(
        "counter", "{occurrence}", "Stuck sessions"
    ),
    "jiuwenclaw.session.stuck_age_ms": MetricSpec(
        "histogram", "ms", "Stuck session age"
    ),
    "gen_ai.skill.call.count": MetricSpec("counter", "{call}", "Skill calls"),
    "gen_ai.skill.duration": MetricSpec("histogram", "s", "Skill duration"),
    "gen_ai.skill.error.count": MetricSpec("counter", "{call}", "Skill errors"),
    "gen_ai.tool.token.usage": MetricSpec(
        "counter", "{token}", "Tool definition tokens"
    ),
    "gen_ai.skill.token.usage": MetricSpec(
        "counter", "{token}", "Skill context tokens"
    ),
    "gen_ai.client.token.first_token_duration": MetricSpec(
        "histogram", "s", "Time to first token"
    ),
}


# Compatibility name retained from Enterprise TelemetryRail. Request entrypoints
# own the token lifecycle so concurrent asyncio tasks remain isolated.
metrics_session_id: ContextVar[str | None] = ContextVar(
    "metrics_session_id", default=None
)
metrics_channel_id: ContextVar[str | None] = ContextVar(
    "metrics_channel_id", default=None
)


class _SessionActiveObserverState:
    """Provider-scoped callback state shared by all TelemetryMetrics facades."""

    def __init__(self, resource: Resource) -> None:
        self._resource = resource
        self._lock = Lock()
        self._observer: Callable[[], int] | None = None

    def replace(self, observer: Callable[[], int] | None) -> None:
        with self._lock:
            self._observer = observer

    def observe(self, _options: CallbackOptions) -> Iterable[Observation]:
        with self._lock:
            observer = self._observer
        if observer is None:
            return []
        try:
            active_sessions = max(int(observer()), 0)
        except Exception:
            return []
        return [
            Observation(
                active_sessions,
                attributes=_common_attributes(self._resource, None),
            )
        ]


_OBSERVER_STATES: WeakKeyDictionary[MeterProvider, _SessionActiveObserverState] = (
    WeakKeyDictionary()
)
_OBSERVER_STATES_LOCK = Lock()


class TelemetryMetrics:
    """Create and record the fixed metric catalog on one explicit provider."""

    def __init__(self, meter_provider: MeterProvider) -> None:
        if not isinstance(meter_provider, MeterProvider):
            raise TypeError("meter_provider must be a MeterProvider")

        self._resource = _provider_resource(meter_provider)
        meter = meter_provider.get_meter("jiuwenswarm.telemetry.metrics")
        self._session_active_state = _provider_observer_state(
            meter_provider,
            meter,
            self._resource,
        )
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}

        for name, spec in METRIC_SPECS.items():
            if spec.kind == "counter":
                self._counters[name] = meter.create_counter(
                    name=name,
                    unit=spec.unit,
                    description=spec.description,
                )
            elif spec.kind == "histogram":
                self._histograms[name] = meter.create_histogram(
                    name=name,
                    unit=spec.unit,
                    description=spec.description,
                )

    def add(
        self,
        name: str,
        value: int | float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Add to a catalog counter or raise a stable error for invalid use."""
        self._require_kind(name, "counter")
        self._counters[name].add(value, self._common_attributes(attributes))

    def record(
        self,
        name: str,
        value: int | float,
        attributes: Mapping[str, Any] | None = None,
    ) -> None:
        """Record a catalog histogram or raise a stable error for invalid use."""
        self._require_kind(name, "histogram")
        self._histograms[name].record(value, self._common_attributes(attributes))

    def set_session_active_observer(self, observer: Callable[[], int] | None) -> None:
        """Replace or clear the callback used by the active-session gauge."""
        if observer is not None and not callable(observer):
            raise TypeError("session active observer must be callable or None")
        self._session_active_state.replace(observer)

    @property
    def session_active_observer_identity(self) -> object:
        """Return the provider-scoped identity used by the active gauge."""
        return self._session_active_state

    @staticmethod
    def _require_kind(name: str, expected: MetricKind) -> None:
        spec = METRIC_SPECS.get(name)
        if spec is None:
            raise KeyError(f"unknown metric '{name}'")
        if spec.kind != expected:
            raise TypeError(f"metric '{name}' is {spec.kind}; expected {expected}")

    def _common_attributes(
        self, attributes: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        """Merge labels with deterministic caller < resource/context < identity precedence.

        Empty strings and ``None`` are omitted. A new mapping is always returned,
        so neither precedence resolution nor filtering mutates the caller's input.
        """
        return _common_attributes(self._resource, attributes)


def _provider_observer_state(
    meter_provider: MeterProvider,
    meter: Meter,
    resource: Resource,
) -> _SessionActiveObserverState:
    with _OBSERVER_STATES_LOCK:
        state = _OBSERVER_STATES.get(meter_provider)
        if state is not None:
            return state

        state = _SessionActiveObserverState(resource)
        spec = METRIC_SPECS["jiuwenclaw.session.active"]
        meter.create_observable_gauge(
            name="jiuwenclaw.session.active",
            callbacks=[state.observe],
            unit=spec.unit,
            description=spec.description,
        )
        _OBSERVER_STATES[meter_provider] = state
        return state


def _common_attributes(
    resource: Resource,
    attributes: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = {
        key: value
        for key, value in dict(attributes or {}).items()
        if value is not None and value != ""
    }

    claw_id = resource.attributes.get(JIUWENCLAW_CLAW_ID)
    if claw_id is not None and claw_id != "":
        merged[JIUWENCLAW_CLAW_ID] = claw_id

    session_id = metrics_session_id.get()
    if session_id:
        merged[JIUWENCLAW_SESSION_ID] = session_id

    try:
        identity = IdentityStore.get_identity()
    except Exception:
        identity = None
    if identity is not None:
        for key, value in (
            ("user_id", identity.user_id),
            ("domain_id", identity.domain_id),
            ("app_id", identity.app_id),
        ):
            if value is not None and value != "":
                merged[key] = value
    return merged


def _provider_resource(meter_provider: MeterProvider) -> Resource:
    public_resource = getattr(meter_provider, "resource", None)
    if public_resource is not None:
        if not isinstance(public_resource, Resource):
            raise TypeError("meter_provider.resource must be a Resource")
        return public_resource

    sdk_config = getattr(meter_provider, "_sdk_config", None)
    resource = getattr(sdk_config, "resource", None)
    if not isinstance(resource, Resource):
        raise TypeError("meter_provider must expose a Resource")
    return resource
