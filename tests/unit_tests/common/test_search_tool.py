# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for DenseSearchTool.invoke (v3 name-based direct-call path).

Covers the simplified invoke after dropping the legacy auto-load/evict path:
- returns matches + the v3 "call by name" note
- top_k clamped to top_k_max
- empty results → "not found" note
- ToolOutput.__str__ is clean JSON (not pydantic repr)
- trace records the search action without auto_loaded/evicted fields
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, List

import pytest


def _fake_search_fn(results: List[Dict[str, Any]]):
    async def _fn(query, limit=10, detail_level=1):
        return list(results)[:limit]

    return _fn


def _make_tool(results, language="cn", top_k_max=3):
    from jiuwenswarm.agents.harness.common.tools.search_tool import DenseSearchTool

    trace: List[Dict[str, Any]] = []

    def append_trace(session, event):
        trace.append(event)

    tool = DenseSearchTool(
        search_fn=_fake_search_fn(results),
        append_trace=append_trace,
        language=language,
        agent_id=None,
        top_k_max=top_k_max,
    )
    return tool, trace


def _two_results():
    return [
        {"name": "memory_search", "description": "search memory", "parameters": {"type": "object"}},
        {"name": "cron_create_job", "description": "create cron", "parameters": {"type": "object"}},
    ]


def test_invoke_returns_matches_and_v3_note_cn():
    tool, trace = _make_tool(_two_results(), language="cn")
    out = asyncio.run(tool.invoke({"query": "memory", "top_k": 2}))

    assert out.success
    assert out.data["count"] == 2
    assert [m["name"] for m in out.data["matches"]] == ["memory_search", "cron_create_job"]
    # v3 note (name-based direct call), NOT the legacy auto-load note.
    assert "按 name" in out.data["note"]
    assert "自动加载" not in out.data["note"]


def test_invoke_trace_has_no_legacy_fields():
    tool, trace = _make_tool(_two_results())
    asyncio.run(tool.invoke({"query": "x", "top_k": 2}))

    assert trace, "append_trace was not called"
    event = trace[0]
    assert event["action"] == "search_tools"
    assert event["query"] == "x"
    assert event["match_count"] == 2
    # legacy auto-load/evict fields must be gone after the simplification
    assert "auto_loaded" not in event
    assert "evicted" not in event
    assert "load_error" not in event


def test_invoke_clamps_top_k_to_top_k_max():
    results = [{"name": f"t{i}"} for i in range(10)]
    tool, _ = _make_tool(results, top_k_max=3)
    out = asyncio.run(tool.invoke({"query": "x", "top_k": 99}))

    assert out.success
    assert out.data["count"] == 3  # clamped


def test_invoke_no_results_returns_not_found_note():
    tool, _ = _make_tool([], language="cn")
    out = asyncio.run(tool.invoke({"query": "nonexistent"}))

    assert out.success
    assert out.data["count"] == 0
    assert out.data["matches"] == []
    assert "未找到" in out.data["note"]


def test_invoke_output_str_is_clean_json():
    tool, _ = _make_tool([{"name": "a", "description": "d"}])
    out = asyncio.run(tool.invoke({"query": "q"}))

    # _JsonToolOutput.__str__ must be valid JSON (consumed by ability_manager
    # as the ToolMessage content), not a pydantic repr like "success=True data={...}".
    payload = json.loads(str(out))
    assert payload["count"] == 1
    assert payload["matches"][0]["name"] == "a"
    assert "note" in payload
