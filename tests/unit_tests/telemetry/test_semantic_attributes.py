# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for §11.4 (canceled/timeout) and §11.5/§11.6 (streaming/iteration) attributes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


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
