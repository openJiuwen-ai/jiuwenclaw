# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for OtelTraceAdapter — OTEL spans → Langfuse trace dict."""

import pytest

from jiuwenswarm.evolve.ahe.otel_adapter import (
    OtelTraceAdapter,
    _ns_to_iso,
    _ns_to_ms,
    _parse_attrs,
    _parse_events,
    _parse_tool_calls_repr,
    _normalize_tool_call,
    _adapt_observation_name,
)


class TestTimeConversion:
    def test_ns_to_iso(self):
        # 2024-01-15 10:30:00 UTC = 1705308600 seconds = 1705308600_000000000 ns
        ns = 1705308600_000000000
        result = _ns_to_iso(ns)
        assert "2024-01-15" in result
        assert "10:30:00" in result

    def test_ns_to_iso_none(self):
        assert _ns_to_iso(None) == "N/A"

    def test_ns_to_ms(self):
        assert _ns_to_ms(1_000_000) == 1.0
        assert _ns_to_ms(10_000_000) == 10.0

    def test_ns_to_ms_none(self):
        assert _ns_to_ms(None) == "N/A"


class TestParseAttrs:
    def test_dict_passthrough(self):
        d = {"key": "value"}
        assert _parse_attrs(d) == d

    def test_json_string(self):
        s = '{"gen_ai.span.type": "model"}'
        result = _parse_attrs(s)
        assert result["gen_ai.span.type"] == "model"

    def test_none(self):
        assert _parse_attrs(None) == {}

    def test_invalid_json(self):
        assert _parse_attrs("not json") == {}


class TestParseEvents:
    def test_list_passthrough(self):
        lst = [{"name": "gen_ai.user.message"}]
        assert _parse_events(lst) == lst

    def test_json_string(self):
        s = '[{"name": "gen_ai.assistant.message", "attributes": {"content": "hi"}}]'
        result = _parse_events(s)
        assert len(result) == 1
        assert result[0]["name"] == "gen_ai.assistant.message"

    def test_none(self):
        assert _parse_events(None) == []


class TestAdaptObservationName:
    def test_anthropic_keyword(self):
        result = _adapt_observation_name("gen_ai.chat", {"gen_ai.system": "anthropic"}, "model")
        assert "anthropic" in result

    def test_openai_keyword(self):
        result = _adapt_observation_name("gen_ai.chat", {"gen_ai.system": "openai"}, "model")
        assert "openai" in result

    def test_non_model_unchanged(self):
        result = _adapt_observation_name("gen_ai.tool.execute", {}, "tool")
        assert result == "gen_ai.tool.execute"


class TestParseToolCallsRepr:
    def test_json_format(self):
        raw = '[{"id": "call_abc", "type": "function", "function": {"name": "bash", "arguments": "{}"}}]'
        result = _parse_tool_calls_repr(raw)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "bash"

    def test_python_repr(self):
        raw = "[ToolCall(id='call_abc', name='bash', arguments='{\"cmd\":\"ls\"}')]"
        result = _parse_tool_calls_repr(raw)
        assert len(result) == 1
        assert result[0]["function"]["name"] == "bash"

    def test_empty_string(self):
        assert _parse_tool_calls_repr("") == []

    def test_none_string(self):
        assert _parse_tool_calls_repr("None") == []


class TestNormalizeToolCall:
    def test_openai_format_passthrough(self):
        tc = {"id": "call_abc", "type": "function", "function": {"name": "bash", "arguments": "{}"}}
        result = _normalize_tool_call(tc)
        assert result == tc

    def test_anthropic_format_conversion(self):
        tc = {"id": "toolu_abc", "type": "tool_use", "name": "bash", "input": {"cmd": "ls"}}
        result = _normalize_tool_call(tc)
        assert result["function"]["name"] == "bash"
        assert result["type"] == "function"


class TestSpanToObservation:
    def _make_llm_span(self):
        return {
            "trace_id": "abc123",
            "span_id": "span-001",
            "parent_span_id": None,
            "name": "gen_ai.chat",
            "start_time_ns": 1705308600_000000000,
            "end_time_ns": 1705308610_000000000,
            "duration_ns": 10_000_000_000,
            "attributes": '{"gen_ai.span.type": "model", "gen_ai.system": "anthropic", "gen_ai.request.model": "claude-sonnet-4-6", "gen_ai.usage.total_tokens": 1500}',
            "events": '[{"name": "gen_ai.assistant.message", "attributes": {"content": "I will help you"}}]',
            "status_code": "OK",
            "status_description": "",
            "resource": '{"service.name": "jiuwenswarm"}',
        }

    def test_llm_span_type(self):
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(self._make_llm_span())
        assert obs["span_type"] == "LLM"
        assert obs["type"] == "GENERATION"

    def test_llm_span_name_adapted(self):
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(self._make_llm_span())
        assert "anthropic" in obs["name"]

    def test_llm_span_output_reconstructed(self):
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(self._make_llm_span())
        assert obs["output"]["role"] == "assistant"
        assert obs["output"]["content"] == "I will help you"

    def test_tool_span_type(self):
        span = {
            "trace_id": "abc123", "span_id": "span-002",
            "parent_span_id": "span-001", "name": "gen_ai.tool.execute: bash",
            "start_time_ns": 1705308610_000000000,
            "end_time_ns": 1705308615_000000000,
            "duration_ns": 5_000_000_000,
            "attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "bash"}',
            "events": '[]', "status_code": "OK", "status_description": "",
            "resource": '{}',
        }
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(span)
        assert obs["span_type"] == "TOOL"

    def test_agent_span_subagent_metadata(self):
        span = {
            "trace_id": "abc123", "span_id": "sub-001",
            "parent_span_id": "span-001", "name": "agent.sub_execute",
            "start_time_ns": 1705308610_000000000,
            "end_time_ns": 1705308620_000000000,
            "duration_ns": 10_000_000_000,
            "attributes": '{"gen_ai.span.type": "agent", "jiuwenclaw.agent.name": "explore"}',
            "events": '[]', "status_code": "OK", "status_description": "",
            "resource": '{}',
        }
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(span)
        assert obs["metadata"]["subagent_id"] == "sub-001"
        assert obs["metadata"]["subagent_name"] == "explore"

    def test_parent_span_id_mapped(self):
        span = self._make_llm_span()
        span["parent_span_id"] = "span-000"
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        obs = adapter._span_to_observation(span)
        assert obs["parentObservationId"] == "span-000"


class TestCollectToolDefinitions:
    def test_collects_from_tool_spans(self):
        spans = [
            {"attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "bash"}'},
            {"attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "read_file"}'},
            {"attributes": '{"gen_ai.span.type": "model"}'},  # not a tool span
        ]
        adapter = OtelTraceAdapter.__new__(OtelTraceAdapter)
        result = adapter._collect_tool_definitions(spans)
        assert len(result) == 2
        names = [d["function"]["name"] for d in result]
        assert "bash" in names
        assert "read_file" in names
        # parameters should be empty dict
        for d in result:
            assert d["function"]["parameters"] == {}
