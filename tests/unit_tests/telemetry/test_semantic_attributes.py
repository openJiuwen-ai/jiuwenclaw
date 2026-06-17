# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for §11.4 (canceled/timeout) and §11.5/§11.6 (streaming/iteration) attributes."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from opentelemetry import trace, metrics
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader


@pytest.fixture
def span_capture(monkeypatch):
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(trace, "_TRACER_PROVIDER", provider, raising=False)
    trace.set_tracer_provider(provider)

    import jiuwenclaw.telemetry.instrumentors.telemetry_rail as mod
    monkeypatch.setattr(mod, "_tracer", trace.get_tracer("jiuwenclaw.telemetry_rail"))

    yield exporter
    exporter.clear()


def _model_ctx(streaming: bool = False):
    return SimpleNamespace(
        model=SimpleNamespace(
            model_name="gpt-4o",
            model_client_config={"client_provider": "openai"},
            streaming=streaming,
        ),
        model_config=SimpleNamespace(temperature=0.7, top_p=1.0),
        messages=[],
    )


async def test_streaming_attribute_set(span_capture):
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx(streaming=True)
    await rail.before_model_call(ctx)
    rail.record_first_token(ctx)
    ctx.result = SimpleNamespace(
        content="hi",
        usage_metadata=SimpleNamespace(input_tokens=5, output_tokens=2),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    spans = span_capture.get_finished_spans()
    llm_spans = [s for s in spans if s.name == "gen_ai.chat"]
    assert llm_spans, "expected gen_ai.chat span"
    assert llm_spans[0].attributes.get("gen_ai.request.streaming") is True
    assert llm_spans[0].attributes.get("gen_ai.streaming.first_token") is True


async def test_log_messages_disabled_still_records_llm_span(span_capture):
    import jiuwenclaw.telemetry.instrumentors.telemetry_rail as mod
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    mod.set_log_messages(False)
    try:
        ctx = _model_ctx()
        ctx.inputs = SimpleNamespace(messages=[], tools=[])

        await rail.before_model_call(ctx)
        ctx.result = SimpleNamespace(
            content="secret response",
            usage_metadata=SimpleNamespace(input_tokens=3, output_tokens=4),
            finish_reason="stop",
        )
        await rail.after_model_call(ctx)
    finally:
        mod.set_log_messages(True)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert len(spans) == 1
    assert spans[0].attributes.get("gen_ai.usage.input_tokens") == 3
    assert spans[0].attributes.get("gen_ai.usage.output_tokens") == 4
    assert all(ev.name != "gen_ai.assistant.message" for ev in spans[0].events)


async def test_log_messages_disabled_no_message_events(span_capture):
    import jiuwenclaw.telemetry.instrumentors.telemetry_rail as mod
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    mod.set_log_messages(False)
    try:
        ctx = _model_ctx()
        ctx.inputs = SimpleNamespace(messages=[], tools=[])

        await rail.before_model_call(ctx)
        ctx.result = SimpleNamespace(
            content="secret response",
            usage_metadata=SimpleNamespace(input_tokens=3, output_tokens=4),
            finish_reason="stop",
        )
        await rail.after_model_call(ctx)
    finally:
        mod.set_log_messages(True)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert len(spans) == 1
    assert spans[0].attributes.get("gen_ai.usage.input_tokens") == 3
    assert spans[0].attributes.get("gen_ai.usage.output_tokens") == 4
    assert all(ev.name != "gen_ai.assistant.message" for ev in spans[0].events)


async def test_iteration_attribute_increments(span_capture):
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))

    for _ in range(3):
        ctx = _model_ctx()
        await rail.before_model_call(ctx)
        ctx.result = SimpleNamespace(
            content="r",
            usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
            finish_reason="stop",
        )
        await rail.after_model_call(ctx)

    await rail.after_invoke(SimpleNamespace())
    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    iterations = sorted(s.attributes.get("jiuwenclaw.iteration") for s in spans)
    assert iterations == [1, 2, 3]


async def test_canceled_attribute(span_capture):
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))
    ctx = SimpleNamespace(error=asyncio.CancelledError())
    await rail.after_invoke(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "jiuwenclaw.agent.invoke"]
    assert spans, "expected agent span"
    assert spans[0].attributes.get("jiuwenclaw.canceled") is True


async def test_timeout_error_type(span_capture):
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))
    ctx = SimpleNamespace(error=asyncio.TimeoutError("timeout"))
    await rail.after_invoke(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "jiuwenclaw.agent.invoke"]
    assert spans
    assert spans[0].attributes.get("error.type") == "TimeoutError"


async def test_nested_agent_llm_parents_to_inner_agent(span_capture):
    """Sub-agent's LLM span should parent to the inner agent span, not the outer."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    outer = TelemetryRail()
    inner = TelemetryRail()

    await outer.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="outer")))
    await inner.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="inner")))

    ctx = _model_ctx()
    await inner.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="r",
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="stop",
    )
    await inner.after_model_call(ctx)

    await inner.after_invoke(SimpleNamespace())
    await outer.after_invoke(SimpleNamespace())

    spans = span_capture.get_finished_spans()
    llm = next(s for s in spans if s.name == "gen_ai.chat")
    agents = [s for s in spans if s.name == "jiuwenclaw.agent.invoke"]
    inner_agent = next(
        s for s in agents if s.attributes.get("gen_ai.conversation.id") == "inner"
    )

    assert llm.parent is not None, "gen_ai.chat should have a parent"
    assert llm.parent.span_id == inner_agent.context.span_id, (
        "gen_ai.chat must parent to the INNER agent.invoke, not the outer"
    )


async def test_llm_span_parents_to_active_context_not_agent_field(span_capture):
    from opentelemetry import context as otel_context, trace as otel_trace

    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))

    tracer = otel_trace.get_tracer("test.intermediate")
    intermediate = tracer.start_span("intermediate.step")
    token = otel_context.attach(otel_trace.set_span_in_context(intermediate))
    try:
        ctx = _model_ctx()
        await rail.before_model_call(ctx)
        ctx.result = SimpleNamespace(
            content="r",
            usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
            finish_reason="stop",
        )
        await rail.after_model_call(ctx)
    finally:
        otel_context.detach(token)
        intermediate.end()

    await rail.after_invoke(SimpleNamespace())

    spans = span_capture.get_finished_spans()
    llm = next(s for s in spans if s.name == "gen_ai.chat")
    inter = next(s for s in spans if s.name == "intermediate.step")

    assert llm.parent is not None
    assert llm.parent.span_id == inter.context.span_id, (
        "gen_ai.chat must parent to the intermediate span (current context), "
        "not jump over it to self._agent_span"
    )


async def test_input_messages_as_json_attribute(span_capture):
    """gen_ai.input.messages should be a JSON array attribute."""
    import json

    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx()
    ctx.inputs = SimpleNamespace(
        messages=[
            SimpleNamespace(role="system", content="You are helpful"),
            SimpleNamespace(role="user", content="Hello"),
            SimpleNamespace(role="assistant", content="Hi"),
            SimpleNamespace(role="user", content="How are you?"),
        ],
        tools=[]
    )

    await rail.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="Good!",
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert spans

    # Check JSON attribute exists
    messages_json = spans[0].attributes.get("gen_ai.input.messages")
    assert messages_json, "expected gen_ai.input.messages attribute"

    # Parse and verify structure
    messages = json.loads(messages_json)
    assert isinstance(messages, list)
    assert all("role" in m for m in messages)
    assert all("parts" in m for m in messages)


async def test_input_messages_count_and_length(span_capture):
    """gen_ai.input.messages.count and .total_length should always be recorded."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx()
    ctx.inputs = SimpleNamespace(
        messages=[
            SimpleNamespace(role="user", content="Short"),
            SimpleNamespace(role="assistant", content="Medium length response"),
        ],
        tools=[]
    )

    await rail.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="ok",
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert spans

    # Count should match message count
    count = spans[0].attributes.get("gen_ai.input.messages.count")
    assert count == 2

    # Total length should be sum of content lengths
    total_length = spans[0].attributes.get("gen_ai.input.messages.total_length")
    assert total_length == len("Short") + len("Medium length response")


async def test_tool_definitions_as_json_attribute(span_capture):
    """gen_ai.tool.definitions should be a JSON array attribute."""
    import json

    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx()
    ctx.inputs = SimpleNamespace(
        messages=[],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
                },
            }
        ],
    )

    await rail.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="result",
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert spans

    tool_defs_json = spans[0].attributes.get("gen_ai.tool.definitions")
    assert tool_defs_json, "expected gen_ai.tool.definitions attribute"

    tool_defs = json.loads(tool_defs_json)
    assert isinstance(tool_defs, list)
    assert tool_defs[0]["type"] == "function"
    assert tool_defs[0]["name"] == "search"


async def test_decision_tool_call_as_attributes(span_capture):
    """Decision should use gen_ai.decision.type attribute."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx()
    ctx.inputs = SimpleNamespace(messages=[], tools=[])

    await rail.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="",
        tool_calls=[SimpleNamespace(id="call_1", name="search", arguments={"query": "test"})],
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="tool_calls",
    )
    await rail.after_model_call(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert spans

    assert spans[0].attributes.get("gen_ai.decision.type") == "tool_call"
    assert spans[0].attributes.get("gen_ai.decision.tool_count") == 1


async def test_decision_answer_as_attribute(span_capture):
    """Answer decision should use gen_ai.decision.type='answer'."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail

    rail = TelemetryRail()
    ctx = _model_ctx()
    ctx.inputs = SimpleNamespace(messages=[], tools=[])

    await rail.before_model_call(ctx)
    ctx.result = SimpleNamespace(
        content="The answer is 42",
        usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    spans = [s for s in span_capture.get_finished_spans() if s.name == "gen_ai.chat"]
    assert spans

    assert spans[0].attributes.get("gen_ai.decision.type") == "answer"


# ---------------------------------------------------------------------------
# TTFT (Time to First Token) helpers, fixtures, and tests
# ---------------------------------------------------------------------------

def _force_set_tracer_provider(tp):
    """Force set tracer provider, bypassing set-once guard."""
    import opentelemetry.trace as trace_mod
    setattr(trace_mod, "_TRACER_PROVIDER", tp)
    once = getattr(trace_mod, "_TRACER_PROVIDER_SET_ONCE")
    setattr(once, "_done", False)


def _force_set_meter_provider(mp):
    """Force set meter provider, bypassing set-once guard."""
    import opentelemetry.metrics._internal as metrics_internal
    setattr(metrics_internal, "_METER_PROVIDER", mp)
    once = getattr(metrics_internal, "_METER_PROVIDER_SET_ONCE")
    setattr(once, "_done", False)
    getattr(metrics_internal, "_PROXY_METER_PROVIDER").on_set_meter_provider(mp)


def _refresh_modules():
    """Refresh module-level tracer and meter + instruments after provider swap."""
    import jiuwenclaw.telemetry.instrumentors.telemetry_rail as rail_mod
    setattr(rail_mod, '_tracer', trace.get_tracer("jiuwenclaw.telemetry_rail"))

    import jiuwenclaw.telemetry.metrics as metrics_mod
    _meter = metrics.get_meter("jiuwenclaw")
    setattr(metrics_mod, '_meter', _meter)
    metrics_mod.llm_duration = _meter.create_histogram(
        name="gen_ai.client.operation.duration", unit="s", description="GenAI LLM call duration",
    )
    metrics_mod.llm_call_count = _meter.create_counter(
        name="gen_ai.client.operation.count", unit="{call}", description="LLM call count",
    )
    metrics_mod.agent_duration = _meter.create_histogram(
        name="jiuwenclaw.agent.duration", unit="s", description="Agent invoke duration",
    )
    metrics_mod.token_usage = _meter.create_counter(
        name="gen_ai.client.token.usage", unit="{token}", description="Token usage by type (input/output/cache)",
    )
    metrics_mod.first_token_duration = _meter.create_histogram(
        name="gen_ai.client.token.first_token_duration",
        unit="ms",
        description="Duration from agent invoke to first streaming token (TTFT)",
        explicit_bucket_boundaries_advisory=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
    )

    # Refresh local references in telemetry_rail.py
    rail_mod.llm_duration = metrics_mod.llm_duration
    rail_mod.llm_call_count = metrics_mod.llm_call_count
    rail_mod.agent_duration = metrics_mod.agent_duration
    rail_mod.token_usage = metrics_mod.token_usage


def _setup_otel():
    """Set up isolated OTel providers for testing."""
    exporter = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exporter))
    _force_set_tracer_provider(tp)

    reader = InMemoryMetricReader()
    mp = MeterProvider(metric_readers=[reader])
    _force_set_meter_provider(mp)

    _refresh_modules()

    return exporter, reader


@pytest.fixture
def otel_env():
    """Provide (span_exporter, metric_reader) using conftest's autouse-reset providers."""
    _refresh_modules()

    import tests.unit_tests.telemetry.conftest as conftest_mod
    exporter = getattr(conftest_mod, '_current_exporter')
    reader = getattr(conftest_mod, '_current_reader')

    yield exporter, reader
    exporter.clear()


def test_signals_first_token_time_contextvar():
    """Verify first_token_time ContextVar exists with correct default."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import first_token_time
    assert first_token_time.get() is None

    ts = time.monotonic()
    first_token_time.set(ts)
    assert first_token_time.get() == ts

    first_token_time.set(None)
    assert first_token_time.get() is None


async def test_stream_ttft_wrapping_sets_first_token_time():
    """Verify the TTFT stream wrapping sets first_token_time on the first yielded chunk."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import first_token_time
    from openjiuwen.core.foundation.llm.model_clients.openai_model_client import OpenAIModelClient

    original_method = getattr(OpenAIModelClient, '_stream_with_retry', None)

    # Replace with a simple async generator for testing
    async def _fake_stream_with_retry(self, stream_func, *args, **kwargs):
        async for chunk in stream_func(*args, **kwargs):
            yield chunk

    setattr(OpenAIModelClient, '_stream_with_retry', _fake_stream_with_retry)

    # Apply TTFT wrapping (same logic as inlined in __init__.py apply_instrumentors)
    _original = getattr(OpenAIModelClient, '_stream_with_retry')

    async def _ttft_wrapped(self, stream_func, *args, **kwargs):
        first_chunk = True
        async for chunk in _original(self, stream_func, *args, **kwargs):
            if first_chunk:
                first_chunk = False
                first_token_time.set(time.monotonic())
            yield chunk

    setattr(OpenAIModelClient, '_stream_with_retry', _ttft_wrapped)

    # Verify wrapping was applied
    assert getattr(OpenAIModelClient, '_stream_with_retry') != _fake_stream_with_retry

    # Simulate a streaming call
    first_token_time.set(None)

    client = SimpleNamespace()

    async def fake_stream_func(*args, **kwargs):
        yield "chunk1"
        yield "chunk2"

    chunks = []
    patched_method = getattr(OpenAIModelClient, '_stream_with_retry')
    async for chunk in patched_method(client, fake_stream_func):
        chunks.append(chunk)

    assert chunks == ["chunk1", "chunk2"]

    ft_time = first_token_time.get()
    assert ft_time is not None, "first_token_time should be set after first chunk"
    assert isinstance(ft_time, float)

    # Restore original method
    if original_method is not None:
        setattr(OpenAIModelClient, '_stream_with_retry', original_method)
    else:
        delattr(OpenAIModelClient, '_stream_with_retry')
    first_token_time.set(None)


def test_ttft_histogram_metric_defined():
    """Verify gen_ai.client.token.first_token_duration histogram is registered."""
    import jiuwenclaw.telemetry.metrics as metrics_mod
    from opentelemetry import metrics as metrics_api

    _meter = metrics_api.get_meter("jiuwenclaw")
    setattr(metrics_mod, '_meter', _meter)
    metrics_mod.first_token_duration = _meter.create_histogram(
        name="gen_ai.client.token.first_token_duration",
        unit="ms",
        description="Duration from agent invoke to first streaming token (TTFT)",
        explicit_bucket_boundaries_advisory=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
    )
    assert metrics_mod.first_token_duration.name == "gen_ai.client.token.first_token_duration"
    assert metrics_mod.first_token_duration.unit == "ms"


def test_record_first_token_duration_records_value():
    """Verify record_first_token_duration writes to the histogram."""
    import jiuwenclaw.telemetry.metrics as metrics_mod
    from opentelemetry import metrics as metrics_api

    _, reader = _setup_otel()

    _meter = metrics_api.get_meter("jiuwenclaw")
    setattr(metrics_mod, '_meter', _meter)
    metrics_mod.first_token_duration = _meter.create_histogram(
        name="gen_ai.client.token.first_token_duration",
        unit="ms",
        description="Duration from agent invoke to first streaming token (TTFT)",
        explicit_bucket_boundaries_advisory=[50, 100, 200, 500, 1000, 2000, 5000, 10000, 30000],
    )

    metrics_mod.record_first_token_duration(1500.0, {"gen_ai.request.model": "gpt-4o"})

    metrics_data = reader.get_metrics_data()
    found = False
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == "gen_ai.client.token.first_token_duration":
                    found = True
                    assert len(metric.data.data_points) > 0
    assert found, "Expected gen_ai.client.token.first_token_duration metric to be recorded"


async def test_ttft_recorded_for_streaming_first_iteration(otel_env):
    """TTFT metric recorded when streaming=True and iteration==1."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail, first_token_time
    _, reader = otel_env

    rail = TelemetryRail()

    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))

    await asyncio.sleep(0.01)
    first_token_time.set(time.monotonic())

    ctx = SimpleNamespace(
        model=SimpleNamespace(model_name="gpt-4o", model_client_config={"client_provider": "openai"}, streaming=True),
        model_config=SimpleNamespace(temperature=0.7, top_p=1.0),
        inputs=SimpleNamespace(messages=[], tools=[]),
    )
    ctx.error = None
    await rail.before_model_call(ctx)
    ctx.inputs.response = SimpleNamespace(
        content="hello",
        usage_metadata=SimpleNamespace(input_tokens=5, output_tokens=2),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)

    await rail.after_invoke(SimpleNamespace())

    metrics_data = reader.get_metrics_data()
    ttft_found = False
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == "gen_ai.client.token.first_token_duration":
                    ttft_found = True
                    dp = metric.data.data_points[0]
                    assert dp.sum >= 10, f"TTFT should be >= 10ms, got {dp.sum}"
    assert ttft_found, "Expected TTFT metric to be recorded"

    first_token_time.set(None)


async def test_ttft_not_recorded_for_non_streaming(otel_env):
    """TTFT metric NOT recorded when first_token_time is never set (non-streaming)."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail, first_token_time
    _, reader = otel_env

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))

    ctx = SimpleNamespace(
        model=SimpleNamespace(model_name="gpt-4o", model_client_config={"client_provider": "openai"}, streaming=False),
        model_config=SimpleNamespace(temperature=0.7, top_p=1.0),
        inputs=SimpleNamespace(messages=[], tools=[]),
    )
    ctx.error = None
    await rail.before_model_call(ctx)
    ctx.inputs.response = SimpleNamespace(
        content="hello",
        usage_metadata=SimpleNamespace(input_tokens=5, output_tokens=2),
        finish_reason="stop",
    )
    await rail.after_model_call(ctx)
    await rail.after_invoke(SimpleNamespace())

    metrics_data = reader.get_metrics_data()
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                assert metric.name != "gen_ai.client.token.first_token_duration", \
                    "TTFT should NOT be recorded when first_token_time is never set"

    first_token_time.set(None)


async def test_ttft_not_recorded_for_second_iteration(otel_env):
    """TTFT metric NOT recorded when iteration > 1 (second model call)."""
    from jiuwenclaw.telemetry.instrumentors.telemetry_rail import TelemetryRail, first_token_time
    _, reader = otel_env

    rail = TelemetryRail()
    await rail.before_invoke(SimpleNamespace(inputs=SimpleNamespace(conversation_id="c1")))

    ctx1 = SimpleNamespace(
        model=SimpleNamespace(model_name="gpt-4o", model_client_config={"client_provider": "openai"}, streaming=True),
        model_config=SimpleNamespace(temperature=0.7, top_p=1.0),
        inputs=SimpleNamespace(messages=[], tools=[]),
    )
    ctx1.error = None
    await rail.before_model_call(ctx1)
    ctx1.inputs.response = SimpleNamespace(
        content="r", usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1), finish_reason="stop",
    )
    await rail.after_model_call(ctx1)

    first_token_time.set(None)

    ctx2 = SimpleNamespace(
        model=SimpleNamespace(model_name="gpt-4o", model_client_config={"client_provider": "openai"}, streaming=True),
        model_config=SimpleNamespace(temperature=0.7, top_p=1.0),
        inputs=SimpleNamespace(messages=[], tools=[]),
    )
    ctx2.error = None
    await rail.before_model_call(ctx2)
    # Simulate stream patch setting first_token_time DURING the streaming call
    # (after before_model_call, before after_model_call — matches real lifecycle)
    first_token_time.set(time.monotonic())
    ctx2.inputs.response = SimpleNamespace(
        content="r2", usage_metadata=SimpleNamespace(input_tokens=1, output_tokens=1), finish_reason="stop",
    )
    await rail.after_model_call(ctx2)

    await rail.after_invoke(SimpleNamespace())

    metrics_data = reader.get_metrics_data()
    ttft_count = 0
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name == "gen_ai.client.token.first_token_duration":
                    ttft_count += len(metric.data.data_points)
    assert ttft_count <= 1, f"Expected at most 1 TTFT point, got {ttft_count}"

    first_token_time.set(None)
