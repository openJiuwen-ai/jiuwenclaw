# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for OtelTraceAdapter._extract_tool_results_for_llm — pairing TOOL
spans back to the LLM turn that requested them.

These tests pin down the regressions flagged in code review:

  * P1  — time-window upper bound must be the next LLM span's start, not ∞.
           The caller passes only TOOL spans, so the LLM filter inside
           _next_llm_start_ns always came up empty and the window ran to the
           end of the trace, leaking later turns' tools into earlier turns.
  * P2a — results must follow the request order of the LLM's tool_calls, not
           the (unordered) iteration of a set.
  * P2b — None start/end_time_ns must not crash comparisons or sorts.
  * P2c — an LLM turn that issued no tool_calls must pull in no tool results.
  * P3  — fallback tool_call_id must be unique per tool span.
"""

from jiuwenswarm.evolve.ahe.otel_adapter import OtelTraceAdapter


def _adapter():
    # Bypass __init__ (which needs a db path / config); these methods are pure
    # functions over the span dicts we pass in.
    return OtelTraceAdapter.__new__(OtelTraceAdapter)


def _llm(span_id, start, end, tool_calls=None, content=""):
    """Build a parsed-style LLM span (dict attributes, span_type set)."""
    msg = {"role": "assistant", "content": content}
    if tool_calls is not None:
        msg["tool_calls"] = tool_calls
    return {
        "span_id": span_id,
        "parent_span_id": "agent",
        "span_type": "LLM",
        "start_time_ns": start,
        "end_time_ns": end,
        "attributes": {"gen_ai.output.messages": [msg]},
        "events": [],
    }


def _tool(span_id, start, end, name="search", call_id="", result="ok"):
    return {
        "span_id": span_id,
        # In jiuwenswarm traces tool spans are children of the AGENT span,
        # not the LLM span — so parent_span_id matching must fail here.
        "parent_span_id": "agent",
        "span_type": "TOOL",
        "start_time_ns": start,
        "end_time_ns": end,
        "attributes": {
            "gen_ai.tool.name": name,
            "gen_ai.tool.call.id": call_id,
            "gen_ai.tool.result": result,
        },
        "events": [],
    }


class TestPreservesRequestOrder:  # P2a
    def test_results_follow_tool_call_request_order(self):
        # LLM requests call_1..call_5; tool spans are stored in reverse order.
        calls = [
            {
                "id": f"call_{i}",
                "type": "function",
                "function": {"name": f"t{i}", "arguments": "{}"},
            }
            for i in range(1, 6)
        ]
        llm = _llm("llm1", 100, 200, tool_calls=calls)
        tool_spans = [
            _tool(f"ts_{i}", 200 + i, 210 + i, call_id=f"call_{i}", result=f"r{i}")
            for i in range(5, 0, -1)  # reverse: call_5 first in list
        ]
        result = _adapter()._extract_tool_results_for_llm(llm, tool_spans, [llm])
        ids = [m["tool_call_id"] for m in result]
        assert ids == ["call_1", "call_2", "call_3", "call_4", "call_5"]


class TestTimeWindowBoundedByNextLlm:  # P1
    def test_tool_after_next_llm_not_leaked_into_earlier_turn(self):
        # Both LLM turns issue a tool_call with NO id, so call.id matching
        # fails and matching falls through to the time-window strategy.
        llm1 = _llm(
            "llm1", 100, 200,
            tool_calls=[{"function": {"name": "s", "arguments": "{}"}}],
        )
        llm2 = _llm(
            "llm2", 400, 405,
            tool_calls=[{"function": {"name": "s", "arguments": "{}"}}],
        )
        t1 = _tool("t1", 210, 250, name="s")  # within llm1's window [200, 400)
        t2 = _tool("t2", 410, 450, name="s")  # within llm2's window, NOT llm1's
        tool_spans = [t1, t2]
        llm_spans = [llm1, llm2]

        r1 = _adapter()._extract_tool_results_for_llm(llm1, tool_spans, llm_spans)
        r2 = _adapter()._extract_tool_results_for_llm(llm2, tool_spans, llm_spans)

        assert len(r1) == 1, f"llm1 leaked later tools: {[m['name'] for m in r1]}"
        assert len(r2) == 1, f"llm2 missed its tool: {[m['name'] for m in r2]}"
        assert r1[0]["name"] == "s"
        assert r2[0]["name"] == "s"


class TestNoToolCallsNoResults:  # P2c
    def test_text_only_turn_pulls_no_tool_results(self):
        llm = _llm("llm1", 100, 200, tool_calls=None, content="hello")
        t1 = _tool("t1", 210, 250, name="s")
        t2 = _tool("t2", 300, 350, name="s")
        r = _adapter()._extract_tool_results_for_llm(llm, [t1, t2], [llm])
        assert r == []


class TestNoneTimestampsDoNotCrash:  # P2b
    def test_none_end_and_none_start_do_not_crash(self):
        # end_time_ns is present but None -> llm_end must coerce to 0, else
        # `None <= int` raises TypeError in the window comprehension. A TOOL
        # span with None start must also not blow up the sort (>=2 in window).
        llm = _llm(
            "llm1", 1000, None,
            tool_calls=[{"function": {"name": "s", "arguments": "{}"}}],
        )
        t1 = _tool("t1", None, 120, name="s")   # None start -> coerces to 0
        t2 = _tool("t2", 150, 160, name="s")
        r = _adapter()._extract_tool_results_for_llm(llm, [t1, t2], [llm])
        assert len(r) == 2


class TestFallbackToolCallIdUnique:  # P3
    def test_two_id_less_tools_get_distinct_ids(self):
        llm = _llm(
            "llm1", 100, 200,
            tool_calls=[{"function": {"name": "s", "arguments": "{}"}}],
        )
        t1 = _tool("span_a", 210, 220, name="search")  # no call.id
        t2 = _tool("span_b", 230, 240, name="search")  # no call.id
        r = _adapter()._extract_tool_results_for_llm(llm, [t1, t2], [llm])
        assert len(r) == 2
        ids = [m["tool_call_id"] for m in r]
        assert len(set(ids)) == 2, f"fallback ids not unique: {ids}"
