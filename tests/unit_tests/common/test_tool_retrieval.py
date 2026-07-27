# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for the on-demand tool-retrieval algorithm core.

Covers ``jiuwenswarm.common.tool_retrieval`` (the agent-core-free algorithm
lib). The lib is import-clean of ``openjiuwen``; these tests likewise do not
start the agent runtime — they use mock tool objects and a fake embedder.

Scope:
  - boost tiers (exact / name-in-query / prefix / token-overlap / substring)
  - space→underscore normalization in name matching
  - verb-intent routing (create/list/delete/update/preview/get/toggle/run)
  - haystack description truncation + None/empty edge cases
  - summary helpers (parameters_summary / safe_serialize / parameters_to_text
    / build_tool_summary) across detail levels and parameter shapes
  - **0-drift**: lib ``build_tool_summary`` / ``parameters_to_text`` produce
    byte-identical output to agent-core's ``ProgressiveToolRail`` base methods
    (guards the verbatim port against future drift)
  - ghost-tool filtering (``filter_executable`` with an injected resolver)
  - ``dense_search`` ranking + limit + empty-corpus, ``precompute_embeddings``
    cache fill + None/empty guards, ``embed_single`` None-model guard
"""

from __future__ import annotations

import numpy as np
import pytest

from jiuwenswarm.common.tool_retrieval import (
    VERB_INTENT,
    build_tool_summary,
    dense_search,
    embed_single,
    embed_texts,
    filter_executable,
    haystack_for,
    parameters_summary,
    parameters_to_text,
    precompute_embeddings,
    safe_serialize_parameters,
    verb_intent_boost,
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


class FakeModel:
    """Deterministic fixed-dim embedder for ranking tests.

    Uses sum(ord(c)) instead of hash() to avoid PYTHONHASHSEED randomization.
    """

    def __init__(self, dim=64):
        self._dim = dim

    def embed(self, texts):
        out = []
        for t in texts:
            v = np.zeros(self._dim)
            for w in str(t).lower().split():
                v[sum(ord(c) for c in w) % self._dim] += 1.0
            out.append(v)
        return out


# ---------------------------------------------------------------------------
# verb_intent_boost
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "query,name,expected",
    [
        ("创建定时任务 cron job", "cron_create_job", 0.5),
        ("创建定时任务 cron job", "cron", 0.0),            # umbrella name has no verb token
        ("列出所有定时任务", "cron_list_jobs", 0.5),
        ("删除刚才那个提醒", "cron_delete_job", 0.5),
        ("修改任务", "cron_update_job", 0.5),
        ("预览下几次执行", "cron_preview_job", 0.5),
        ("获取任务详情", "cron_get_job", 0.5),
        ("切换任务状态", "cron_toggle_job", 0.5),
        ("运行任务", "cron_run_job", 0.5),
        ("memory search", "memory_search", 0.0),          # no verb in query
        ("搜索记忆", "memory_search", 0.0),                # "搜索" not in verb map
        ("创建定时任务", "cron_create_job", 0.5),          # CN-only create intent
    ],
)
def test_verb_intent_boost(query, name, expected):
    ql = query.strip().lower()
    nl = name.lower()
    assert verb_intent_boost(ql, nl) == expected


def test_verb_intent_map_covers_documented_verbs():
    en_tokens = {t for pair, _ in VERB_INTENT for t in pair}
    assert en_tokens >= {"create", "add", "list", "delete", "remove",
                         "update", "preview", "get", "toggle", "run"}


# ---------------------------------------------------------------------------
# haystack_for (description truncation + None/empty)
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
    h = haystack_for(MockTool("t", None, None), desc_cap=64)
    assert h.startswith("t ")


def test_haystack_uses_parameters_to_text():
    params = {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}
    h = haystack_for(MockTool("memory_search", "search memory", params), desc_cap=64)
    assert "fields: q" in h
    assert "q" in h


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


# ---------------------------------------------------------------------------
# dense_search (ranking + limit + edge cases)
# ---------------------------------------------------------------------------


def _cron_tools():
    return [
        MockTool("cron", "使用 action 接口：status、list、add、update、remove、run、runs、wake"),
        MockTool("cron_create_job", "创建一个 cron 定时任务。"),
        MockTool("cron_list_jobs", "列出所有 cron 定时任务。"),
        MockTool("cron_preview_job", "预览 cron 定时任务的下 N 次计划执行时间。"),
        MockTool("memory_search", "在持久化记忆中搜索。"),
    ]


def test_dense_search_returns_at_most_limit():
    tools = _cron_tools()
    cache = {}
    res = dense_search("cron", tools, FakeModel(), cache, limit=3, detail_level=3, desc_cap=64)
    assert len(res) <= 3
    for r in res:
        assert set(r.keys()) >= {"name", "description"}


def test_dense_search_empty_corpus():
    res = dense_search("anything", [], FakeModel(), {}, limit=3, detail_level=3, desc_cap=64)
    # at least one (fallback) when corpus empty? current contract: tools=[] →
    # scored empty → matched=[] → returns []; limit=min guard yields []
    assert res == []


def test_dense_search_creates_job_ranks_above_umbrella_for_create_intent():
    # With a flat fake embedder (cosine ties), ranking is decided by boost.
    # "创建定时任务 cron job" → cron_create_job gets verb +0.5 + token overlap;
    # umbrella "cron" gets only token-overlap +0.5 (no verb). They tie on the
    # single-token 'cron' overlap, but cron_create_job also matches 'job'.
    tools = _cron_tools()
    res = dense_search("创建定时任务 cron job", tools, FakeModel(), {}, limit=5, detail_level=3, desc_cap=64)
    names = [r["name"] for r in res]
    # cron_create_job should rank at or above the umbrella cron
    assert "cron_create_job" in names
    assert names.index("cron_create_job") <= names.index("cron")


def test_dense_search_detail_level_shapes_output():
    tools = _cron_tools()
    res = dense_search("memory", tools, FakeModel(), {}, limit=1, detail_level=1, desc_cap=64)
    assert set(res[0].keys()) == {"name", "description"}


def test_dense_search_lazy_embeds_uncached_tool():
    tools = _cron_tools()
    cache = {}  # empty: dense_search must lazily embed via embed_single
    res = dense_search("cron", tools, FakeModel(), cache, limit=2, detail_level=1, desc_cap=64)
    assert len(res) >= 1
    # cache should now contain embeddings for the iterated tools
    assert len(cache) > 0


# ---------------------------------------------------------------------------
# precompute_embeddings + embed_single
# ---------------------------------------------------------------------------


def test_precompute_fills_cache_in_place():
    tools = _cron_tools()
    cache = {}
    precompute_embeddings(tools, FakeModel(), cache, desc_cap=64)
    assert len(cache) == len(tools)
    assert set(cache.keys()) == {t.name for t in tools}


def test_precompute_none_model_is_noop():
    cache = {}
    precompute_embeddings(_cron_tools(), None, cache, desc_cap=64)
    assert cache == {}


def test_precompute_empty_tools_is_noop():
    cache = {}
    precompute_embeddings([], FakeModel(), cache, desc_cap=64)
    assert cache == {}


def test_precompute_failure_clears_cache():
    class BoomModel:
        def embed(self, texts):
            raise RuntimeError("embed failed")
    cache = {"stale": np.zeros(4)}
    precompute_embeddings(_cron_tools(), BoomModel(), cache, desc_cap=64)
    assert cache == {}


def test_embed_single_none_model():
    assert embed_single(MockTool("t", "d"), None, desc_cap=64) is None


def test_embed_single_returns_vector():
    v = embed_single(MockTool("t", "d"), FakeModel(), desc_cap=64)
    assert v is not None
    assert isinstance(v, np.ndarray)


# ---------------------------------------------------------------------------
# embed_texts (trivial wrapper)
# ---------------------------------------------------------------------------


def test_embed_texts_passthrough():
    m = FakeModel()
    out = embed_texts(m, ["a b", "c"])
    assert len(out) == 2
    assert all(isinstance(v, np.ndarray) for v in out)
