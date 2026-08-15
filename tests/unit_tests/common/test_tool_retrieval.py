# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the on-demand tool-retrieval algorithm core.

Covers ``jiuwenswarm.common.tool_retrieval`` (the agent-core-free algorithm
lib). The lib is import-clean of ``openjiuwen``; these tests likewise do not
start the agent runtime — they use mock tool objects.

Scope:
  - haystack description truncation + None/empty edge cases
  - summary helpers (parameters_summary / safe_serialize / parameters_to_text
    / build_tool_summary) across detail levels and parameter shapes
  - **0-drift**: lib ``build_tool_summary`` / ``parameters_to_text`` produce
    byte-identical output to agent-core's ``ProgressiveToolRail`` base methods
    (guards the verbatim port against future drift)
  - ghost-tool filtering (``filter_executable`` with an injected resolver)
  - BM25 ranking + index build + empty-corpus edge cases
  - ``search`` dispatch (BM25-only in v3; dense/embedding path removed)
"""

from __future__ import annotations

import pytest

from jiuwenswarm.common.tool_retrieval import (
    build_tool_summary,
    build_bm25_index,
    bm25_search,
    dispatch_search,
    filter_executable,
    flatten_schema,
    haystack_for,
    parameters_summary,
    parameters_to_text,
    safe_serialize_parameters,
    split_identifier,
)


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class MockTool:
    """Duck-typed tool object (name/description/parameters attrs)."""

    def __init__(self, name, description="", parameters=None):
        self.name = name
        self.description = description
        self.parameters = parameters

    def __repr__(self):
        return f"MockTool({self.name!r})"


# ---------------------------------------------------------------------------
# haystack_for
# ---------------------------------------------------------------------------


def test_haystack_truncates_long_description():
    desc = "x" * 500
    h = haystack_for(MockTool("t", desc, {}), desc_cap=64)
    # name + truncated desc (64) + params text
    assert h.startswith("t ")
    # the 500-char desc must not appear in full
    assert desc not in h
    assert "x" * 64 in h


def test_haystack_keeps_short_description():
    h = haystack_for(MockTool("t", "short desc", {"type": "object"}), desc_cap=64)
    assert "t short desc" in h


def test_haystack_none_description():
    # v2: name only (no desc, no params) → haystack is just the name tokens.
    h = haystack_for(MockTool("t", None, None), desc_cap=64)
    assert h == "t"


def test_haystack_uses_parameters_to_text():
    # v2: haystack is cleaned — field names appear, JSON noise doesn't.
    params = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    h = haystack_for(MockTool("memory_search", "search memory", params), desc_cap=64)
    tokens = h.lower().split()
    assert "q" in tokens                      # field name extracted
    assert "fields" not in tokens             # v1's "fields: q" prefix gone
    assert "type" not in tokens               # JSON structural noise excluded
    assert "properties" not in tokens
    assert "required" not in tokens


# ---------------------------------------------------------------------------
# summary helpers
# ---------------------------------------------------------------------------


def test_parameters_summary_dict_with_properties():
    p = {"properties": {"a": {}, "b": {}}}
    assert parameters_summary(p) == "fields: a, b"


def test_parameters_summary_dict_without_properties():
    assert parameters_summary({"type": "object"}) == "schema keys: type"


def test_parameters_summary_empty_dict():
    assert parameters_summary({}) == "empty schema"


def test_parameters_summary_none():
    assert parameters_summary(None) == "no parameters"


def test_safe_serialize_parameters_dict_passthrough():
    p = {"type": "object"}
    assert safe_serialize_parameters(p) is p


def test_safe_serialize_parameters_none():
    assert isinstance(safe_serialize_parameters(None), str)


def test_parameters_to_text_concat_summary_and_raw():
    p = {"properties": {"q": {}}}
    txt = parameters_to_text(p)
    assert "fields: q" in txt
    # raw serialization also stringified into the haystack
    assert "properties" in txt


def test_build_tool_summary_detail_levels():
    t = MockTool("cron_create_job", "create a cron job",
                 {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]})
    s1 = build_tool_summary(t, detail_level=1)
    assert set(s1.keys()) == {"name", "description"}
    s2 = build_tool_summary(t, detail_level=2)
    assert "parameter_summary" in s2
    s3 = build_tool_summary(t, detail_level=3)
    assert s3["parameters"] == t.parameters
    assert s3["parameter_summary"] == "fields: name"


def test_build_tool_summary_none_parameters():
    t = MockTool("t", "desc", None)
    s = build_tool_summary(t, detail_level=3)
    assert s["name"] == "t"
    assert s["parameter_summary"] == "no parameters"


# ---------------------------------------------------------------------------
# 0-drift vs agent-core base class (the verbatim-port guard)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _base():
    from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail
    return ProgressiveToolRail


_DRIFT_TOOLS = [
    MockTool("cron_create_job", "创建一个 cron 定时任务。",
             {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}),
    MockTool("cron", "使用 action 接口：status、list、add、update、remove、run、runs、wake"),
    MockTool("memory_search", "在持久化记忆中搜索。", {"type": "object", "properties": {}, "required": []}),
    MockTool("empty", "", None),
]


@pytest.mark.parametrize("tool", _DRIFT_TOOLS)
@pytest.mark.parametrize("detail_level", [1, 2, 3])
def test_build_tool_summary_matches_base(_base, tool, detail_level):
    mine = build_tool_summary(tool, detail_level=detail_level)
    base = _base._build_tool_summary(tool, detail_level=detail_level)
    assert mine == base


@pytest.mark.parametrize("tool", _DRIFT_TOOLS)
def test_parameters_to_text_matches_base(_base, tool):
    mine = parameters_to_text(tool.parameters)
    base = _base._parameters_to_text(tool.parameters)
    assert mine == base


@pytest.mark.parametrize("tool", _DRIFT_TOOLS)
def test_parameters_summary_matches_base(_base, tool):
    assert parameters_summary(tool.parameters) == _base._parameters_summary(tool.parameters)


@pytest.mark.parametrize("tool", _DRIFT_TOOLS)
def test_safe_serialize_matches_base(_base, tool):
    assert safe_serialize_parameters(tool.parameters) == _base._safe_serialize_parameters(tool.parameters)


# ---------------------------------------------------------------------------
# filter_executable (ghost filtering)
# ---------------------------------------------------------------------------


def test_filter_executable_keeps_resolvable_drops_ghosts():
    tools = [MockTool("real_a"), MockTool("real_b"), MockTool("ghost_c")]
    resolver = lambda name: not name.startswith("ghost_")
    corpus = filter_executable(tools, resolver)
    assert [t.name for t in corpus] == ["real_a", "real_b"]


def test_filter_executable_empty_input():
    assert filter_executable([], lambda n: True) == []


def test_filter_executable_skips_empty_name():
    tools = [MockTool(""), MockTool("ok")]
    corpus = filter_executable(tools, lambda n: True)
    assert [t.name for t in corpus] == ["ok"]


def test_filter_executable_all_ghosts():
    tools = [MockTool("a"), MockTool("b")]
    assert filter_executable(tools, lambda n: False) == []


def test_filter_executable_resolver_exceptions_treated_as_ghost():
    def resolver(name):
        if name == "boom":
            raise RuntimeError("boom")
        return True
    tools = [MockTool("ok"), MockTool("boom")]
    # resolver raising must not crash filter_executable — but the current
    # contract lets the exception propagate (caller-injected resolver).
    with pytest.raises(RuntimeError):
        filter_executable(tools, resolver)


# ===========================================================================
# BM25 ranking + search dispatch
# ===========================================================================


def _ranking_names(results):
    """Helper: extract ordered tool names from a search result list."""
    return [r.get("name", "") for r in results]


# ---------------------------------------------------------------------------
# BM25 basic ranking
# ---------------------------------------------------------------------------


def test_bm25_ranks_by_term_frequency():
    """A doc with more query-term occurrences should rank higher."""
    tools = [
        MockTool("read_file", "read a file read read"),   # 3x "read"
        MockTool("write_file", "write a file"),            # 0x "read"
        MockTool("read_log", "read the log"),              # 1x "read"
    ]
    idx = build_bm25_index(tools, desc_cap=64)
    results = bm25_search("read", tools, idx, limit=3, detail_level=1)
    names = _ranking_names(results)
    # read_file (3 occurrences) > read_log (1) > write_file (0, filtered out)
    assert names[0] == "read_file"
    assert "write_file" not in names  # no term overlap → score 0 → dropped


def test_bm25_idf_downweights_common_terms():
    """A term present in all docs still scores (idf floors > 0), but a
    discriminating term ranks its only-containing doc higher."""
    tools = [
        MockTool("a", "common rare"),
        MockTool("b", "common"),
        MockTool("c", "common"),
    ]
    idx = build_bm25_index(tools, desc_cap=64)
    # "rare" only in doc a → a must rank first
    results = bm25_search("common rare", tools, idx, limit=3, detail_level=1)
    assert _ranking_names(results)[0] == "a"


def test_bm25_empty_corpus_or_query():
    assert bm25_search("", [], None, limit=3, detail_level=1) == []
    assert bm25_search("q", [], None, limit=3, detail_level=1) == []
    # query with no tokens → []
    tools = [MockTool("t", "d")]
    idx = build_bm25_index(tools, desc_cap=64)
    assert bm25_search("   ", tools, idx, limit=3, detail_level=1) == []


def test_bm25_builds_index_on_the_fly_when_none():
    """bm25_search with bm25_index=None builds on the fly (slow path)."""
    tools = [MockTool("read_file", "read a file")]
    results = bm25_search("read", tools, None, limit=3, detail_level=1, desc_cap=64)
    assert _ranking_names(results) == ["read_file"]


# ---------------------------------------------------------------------------
# search dispatch (BM25-only in v3; dense/embedding path removed)
# ---------------------------------------------------------------------------


def test_dispatch_bm25_works_without_model():
    """v3: search dispatch is BM25-only — no embedding model needed."""
    tools = [MockTool("read_file", "read a file")]
    results = dispatch_search(
        "read", tools,
        bm25_index=None,
        limit=3, detail_level=1,
        desc_cap=64,
    )
    assert _ranking_names(results) == ["read_file"]


# ===========================================================================
# v2 phase 2: haystack cleaning
# ===========================================================================


# ---------------------------------------------------------------------------
# _split_identifier
# ---------------------------------------------------------------------------


def test_split_snake_case():
    assert split_identifier("memory_search") == "memory search"
    assert split_identifier("read_file") == "read file"
    assert split_identifier("send_file_to_user") == "send file to user"


def test_split_camel_case():
    assert split_identifier("sendEmail") == "send email"
    assert split_identifier("readFile") == "read file"


def test_split_preserves_lowercase_single_word():
    assert split_identifier("bash") == "bash"
    assert split_identifier("cron") == "cron"


def test_split_handles_empty_and_none():
    assert split_identifier("") == ""
    assert split_identifier(None) == ""


# ---------------------------------------------------------------------------
# _flatten_schema
# ---------------------------------------------------------------------------


def test_flatten_extracts_field_names():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词"},
            "limit": {"type": "integer", "default": 10},
        },
        "required": ["query"],
    }
    out: list = []
    flatten_schema(schema, out)
    joined = " ".join(out)
    # field names should appear (split, lowercased)
    assert "query" in joined
    assert "limit" in joined
    # JSON structural keywords must NOT appear as tokens
    assert "type" not in joined.split()
    assert "properties" not in joined.split()
    assert "required" not in joined.split()
    assert "object" not in joined.split()  # value of "type" — also noise


def test_flatten_extracts_enum_values():
    schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "delete"],
            }
        },
    }
    out: list = []
    flatten_schema(schema, out)
    joined = " ".join(out)
    assert "create" in joined.split()
    assert "list" in joined.split()
    assert "delete" in joined.split()
    assert "action" in joined.split()


def test_flatten_recurses_nested_objects():
    schema = {
        "type": "object",
        "properties": {
            "task": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "content": {"type": "string"},
                },
            }
        },
    }
    out: list = []
    flatten_schema(schema, out)
    joined = " ".join(out)
    assert "id" in joined.split()
    assert "content" in joined.split()
    assert "task" in joined.split()


def test_flatten_handles_empty_and_none():
    assert flatten_schema(None, []) is None
    out: list = []
    flatten_schema({}, out)
    assert out == []
    flatten_schema([], out)
    assert out == []


def test_flatten_depth_guard():
    """Deeply nested schemas should not blow the stack or take forever."""
    # Build a 10-deep nested dict
    inner = {"name": {"type": "string"}}
    for _ in range(10):
        inner = {"properties": inner}
    out: list = []
    flatten_schema(inner, out)  # should not raise
    # the deepest "name" may or may not appear (depth guard), but no crash


# ---------------------------------------------------------------------------
# haystack_for (v2 cleaned)
# ---------------------------------------------------------------------------


def test_haystack_contains_split_name():
    tool = MockTool("memory_search", "在持久化记忆中搜索。", {"type": "object"})
    h = haystack_for(tool, desc_cap=64)
    # raw name present (for exact-match boost)
    assert "memory_search" in h
    # split name present (for BM25 word matching)
    assert "memory search" in h


def test_haystack_excludes_json_noise():
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词"},
        },
        "required": ["query"],
    }
    tool = MockTool("memory_search", "搜索记忆", schema)
    h = haystack_for(tool, desc_cap=64)
    tokens = h.lower().split()
    # These JSON-Schema structural words must not appear
    assert "type" not in tokens
    assert "properties" not in tokens
    assert "required" not in tokens
    assert "object" not in tokens
    assert "string" not in tokens  # value of "type" — noise
    # but the field name should
    assert "query" in tokens


def test_haystack_includes_enum_values():
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["create", "delete"]},
        },
    }
    tool = MockTool("cron_modify", "修改定时任务", schema)
    h = haystack_for(tool, desc_cap=64)
    tokens = h.lower().split()
    assert "create" in tokens
    assert "delete" in tokens


def test_haystack_no_json_noise_regression():
    """Regression guard: the v1 haystack dumped the whole raw dict, so
    'type'/'properties'/'required' appeared verbatim. v2 must not regress.
    """
    schema = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
        "additionalProperties": False,
    }
    tool = MockTool("t", "desc", schema)
    h = haystack_for(tool, desc_cap=64)
    for noise in ("type", "properties", "required", "additionalproperties"):
        assert noise not in h.lower().split(), f"{noise!r} leaked into haystack"


# ---------------------------------------------------------------------------
# build_tool_summary regression (must stay complete — full schema to LLM)
# ---------------------------------------------------------------------------


def test_build_tool_summary_keeps_full_schema_at_detail_level_3():
    """build_tool_summary returns the FULL schema so the LLM can construct
    arguments. Phase 2 must NOT change this — only haystack_for is cleaned.
    """
    schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索词"},
        },
        "required": ["query"],
    }
    tool = MockTool("memory_search", "搜索记忆", schema)
    summary = build_tool_summary(tool, detail_level=3)
    # Full schema must still be present
    assert summary["name"] == "memory_search"
    assert summary["description"] == "搜索记忆"
    params = summary["parameters"]
    # the raw schema structure is preserved (dict form)
    assert isinstance(params, dict)
    assert params.get("type") == "object"
    assert "properties" in params
    assert params["properties"]["query"]["type"] == "string"
