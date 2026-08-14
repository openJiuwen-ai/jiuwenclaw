# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression test: JiuWenProgressiveToolRail keeps inputs.tools stable across turns.

The core v3 promise is "tools[] 全程恒定" (constant across turns for
prompt-cache stability). ``before_model_call`` filters ``inputs.tools`` to
keep only meta + always-visible + session-visible; search never mutates
``session_visible`` (``load_fn=None``), so the filtered ``tools[]`` must be
identical on every turn. This test guards the filter port after decoupling
from openjiuwen's ``ProgressiveToolRail`` base class.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, List
from unittest.mock import MagicMock

import pytest


def _make_tool(name: str) -> Any:
    """A duck-typed tool object exposing a ``.name`` (not a dict)."""
    m = MagicMock()
    m.name = name
    return m


class _DummyBuilder:
    """Stand-in for SystemPromptBuilder; accepts add_section/remove_section."""

    def add_section(self, section) -> None:
        pass

    def remove_section(self, name) -> None:
        pass


class _DummySession:
    """Minimal session with get_state/update_state."""

    def __init__(self, visible: List[str] | None = None) -> None:
        self._state: dict = {}
        if visible is not None:
            self._state["__progressive_visible_tool_names__"] = list(visible)

    def get_state(self, key):
        return self._state.get(key)

    def update_state(self, mapping: dict) -> None:
        self._state.update(mapping)


class _DummyInputs:
    def __init__(self, tools: List[Any]) -> None:
        self.tools = tools


class _DummyCtx:
    def __init__(self, agent, session, inputs) -> None:
        self.agent = agent
        self.session = session
        self.inputs = inputs


def _names(tools) -> List[str]:
    return [str(getattr(t, "name", "") or "") for t in tools]


def _make_rail(monkeypatch):
    from jiuwenswarm.agents.harness.common.rails import search_tool_rail as mod

    config = SimpleNamespace(
        progressive_tool_enabled=True,
        progressive_tool_always_visible_tools=["bash", "read_file"],
        progressive_tool_default_visible_tools=[],
        progressive_tool_max_loaded_tools=16,
        language="cn",
        tool_retrieval_desc_cap=64,
        tool_retrieval_top_k_max=3,
    )
    rail = mod.JiuWenProgressiveToolRail(config)

    # _get_prompt_builder does an isinstance(SystemPromptBuilder) check; stub it
    # so the test doesn't need a real SystemPromptBuilder.
    monkeypatch.setattr(rail, "_get_prompt_builder", lambda ctx: _DummyBuilder())

    # Stub the nav/rules section builders (they touch openjiuwen prompt imports
    # + _get_real_tool_infos); this test focuses purely on the tools[] filter.
    async def _noop_nav(self, session):
        return None

    monkeypatch.setattr(type(rail), "_build_navigation_section", _noop_nav)
    monkeypatch.setattr(
        type(rail), "_build_progressive_tool_rules_section", lambda self: None
    )
    return rail


def _full_tool_set():
    return [
        _make_tool("bash"),
        _make_tool("read_file"),
        _make_tool("search_tools"),        # meta (DenseSearchTool)
        _make_tool("memory_search"),        # hidden
        _make_tool("cron_create_job"),      # hidden
        _make_tool("send_file_to_user"),   # hidden
    ]


def test_tools_list_filters_hidden_and_stays_constant_across_turns(monkeypatch):
    rail = _make_rail(monkeypatch)
    rail._meta_tool_names = {"search_tools"}  # the registered DenseSearchTool
    # always_visible_tools == {"bash", "read_file"} (from config above)

    # session_visible starts empty (baseline only); search never mutates it
    # (load_fn=None), so it stays empty across turns.
    session = _DummySession(visible=[])

    # Turn 1
    inputs1 = _DummyInputs(tools=_full_tool_set())
    ctx1 = _DummyCtx(agent=MagicMock(), session=session, inputs=inputs1)
    asyncio.run(rail.before_model_call(ctx1))
    after_turn1 = _names(inputs1.tools)

    # Hidden tools must be filtered out; only meta + baseline remain.
    assert set(after_turn1) == {"bash", "read_file", "search_tools"}, after_turn1
    assert "memory_search" not in after_turn1
    assert "cron_create_job" not in after_turn1
    assert "send_file_to_user" not in after_turn1

    # Turn 2 — same session_visible (search doesn't mutate it); a fresh inputs
    # with the SAME full tool set must filter to an IDENTICAL tools[].
    inputs2 = _DummyInputs(tools=_full_tool_set())
    ctx2 = _DummyCtx(agent=MagicMock(), session=session, inputs=inputs2)
    asyncio.run(rail.before_model_call(ctx2))
    after_turn2 = _names(inputs2.tools)

    assert after_turn2 == after_turn1, (
        f"tools[] not stable across turns (cache-stability broken): "
        f"t1={after_turn1} t2={after_turn2}"
    )


def test_loaded_tool_would_enter_tools_list_if_session_visible_mutated(monkeypatch):
    """Guard the filter semantics: IF session_visible grew (it shouldn't under
    load_fn=None, but the filter must honor it if it ever did), the loaded tool
    would be kept. This documents the contract the stability test relies on."""
    rail = _make_rail(monkeypatch)
    rail._meta_tool_names = {"search_tools"}

    # Simulate a (hypothetical) session where memory_search was loaded.
    session = _DummySession(visible=["memory_search"])
    inputs = _DummyInputs(tools=_full_tool_set())
    ctx = _DummyCtx(agent=MagicMock(), session=session, inputs=inputs)
    asyncio.run(rail.before_model_call(ctx))
    after = _names(inputs.tools)

    assert set(after) == {"bash", "read_file", "search_tools", "memory_search"}, after
    assert "cron_create_job" not in after


def test_search_tools_runs_bm25_off_event_loop(monkeypatch):
    """P1: _search_tools runs the CPU-bound BM25 search via asyncio.to_thread
    (off the event loop, not blocking it). Verifies the threaded path returns
    results without deadlocking."""
    from types import SimpleNamespace

    rail = _make_rail(monkeypatch)
    rail._search_corpus = [
        SimpleNamespace(
            name="memory_search",
            description="search memory",
            parameters={"type": "object"},
        )
    ]

    results = asyncio.run(rail._search_tools("memory", 3, 3))
    assert results, "BM25 search returned no results"
    assert results[0]["name"] == "memory_search"


def test_search_tools_name_fast_path_recovers_tool_missing_from_corpus(monkeypatch):
    """坑2: query 是工具名时，即使 _search_corpus 不含该工具（ghost 过滤
    误杀 / 注册时序），也能从 _cached_all_tool_infos 兜底返回。复现
    send_file_to_user 4 次搜索都搜不到的场景：导航能列出但 search 搜不到。"""
    rail = _make_rail(monkeypatch)
    tool_info = SimpleNamespace(
        name="send_file_to_user",
        description="send file to user",
        parameters={"type": "object"},
    )
    # 导航/隐藏摘要读 _cached_all_tool_infos → 能列出 send_file_to_user
    rail._cached_all_tool_infos = [tool_info]
    # 但 _search_corpus 不含它（ghost 过滤误杀 + sig 缓存锁死）
    rail._search_corpus = []

    results = asyncio.run(rail._search_tools("send_file_to_user", 3, 3))

    assert len(results) == 1, f"expected send_file_to_user, got {results}"
    assert results[0]["name"] == "send_file_to_user"


def test_search_tools_name_fast_path_ignores_non_name_query(monkeypatch):
    """坑2: query 不是工具名（语义查询、无下划线）时不触发 name fast-path。"""
    rail = _make_rail(monkeypatch)
    rail._cached_all_tool_infos = [
        SimpleNamespace(name="send_file_to_user", description="", parameters={})
    ]
    rail._search_corpus = []
    # "发送文件" 不含下划线 → 不是工具名 → name fast-path 返回 []

    results = asyncio.run(rail._search_tools("发送文件", 3, 3))

    assert results == [], "name fast-path should not fire for non-tool-name query"


def test_build_executable_corpus_refilters_when_sig_unchanged(monkeypatch):
    """坑1: sig 缓存已移除——工具名字集合没变（sig 相同）也要重新过滤，
    让运行时工具被误杀后能自愈，不被锁死。"""
    from jiuwenswarm.agents.harness.common.rails import search_tool_rail as mod

    rail = _make_rail(monkeypatch)
    rail._cached_all_tool_infos = [
        SimpleNamespace(name="read_file", description="", parameters={}),
        SimpleNamespace(name="send_file_to_user", description="send file", parameters={}),
    ]

    call_count = {"n": 0}

    def fake_filter(tools_list, resolver):
        call_count["n"] += 1
        return list(tools_list)

    monkeypatch.setattr(mod.tool_retrieval, "filter_executable", fake_filter)

    ctx = _DummyCtx(agent=MagicMock(), session=_DummySession(), inputs=_DummyInputs([]))

    rail._build_executable_corpus(ctx)
    assert call_count["n"] == 1
    first_corpus = rail._search_corpus
    assert first_corpus is not None and len(first_corpus) == 2

    # Second call: _cached_all_tool_infos unchanged → sig identical.
    # Before the fix: sig cache hit → return → filter_executable NOT called
    #   again (n stays 1) and _search_corpus NOT reassigned.
    # After the fix: re-filters → filter_executable called again (n=2) and
    #   _search_corpus reassigned.
    rail._build_executable_corpus(ctx)
    assert call_count["n"] == 2, "sig cache skipped re-filter (ghost misclassification locked in)"
    assert rail._search_corpus is not first_corpus, "corpus not reassigned (cache returned early)"


# ---------------------------------------------------------------------------
# Hidden tool summary format (v2 phase 3: each tool carries its description)
# ---------------------------------------------------------------------------

def _make_tool_with_desc(name: str, description: str) -> Any:
    """Duck-typed tool with name + description (for hidden-summary tests)."""
    t = SimpleNamespace(name=name, description=description, parameters={"type": "object"})
    return t


def _setup_hidden_rail(monkeypatch, *, visible: List[str] | None = None,
                       always_visible: List[str] | None = None) -> Any:
    """Rail with stubbed _get_real_tool_infos reading _cached_all_tool_infos.

    Mirrors _make_rail but leaves nav/rules builders intact so
    _build_hidden_tool_summary runs for real.
    """
    from jiuwenswarm.agents.harness.common.rails import search_tool_rail as mod

    config = SimpleNamespace(
        progressive_tool_enabled=True,
        progressive_tool_always_visible_tools=always_visible or ["bash", "read_file"],
        progressive_tool_default_visible_tools=[],
        progressive_tool_max_loaded_tools=16,
        language="cn",
        tool_retrieval_desc_cap=64,
        tool_retrieval_top_k_max=3,
    )
    rail = mod.JiuWenProgressiveToolRail(config)
    # _get_real_tool_infos reads _cached_all_tool_infos (set per-test).
    return rail


def test_hidden_summary_lists_each_tool_with_description_cn(monkeypatch):
    """v2 phase 3: hidden summary shows each tool's name + description on its
    own indented line, not just a flat names list."""
    rail = _setup_hidden_rail(monkeypatch, always_visible=["bash", "read_file"])
    rail._meta_tool_names = {"search_tools"}
    rail._cached_all_tool_infos = [
        _make_tool_with_desc("bash", "执行 shell 命令"),       # always-visible
        _make_tool_with_desc("read_file", "读取文件"),         # always-visible
        _make_tool_with_desc("search_tools", "检索候选工具"),  # meta
        _make_tool_with_desc("search_agent_run", "专注搜索的子agent"),  # hidden → search 类
        _make_tool_with_desc("memory_search", "搜索记忆条目"),            # hidden → memory 类
    ]

    entries = asyncio.run(rail._build_hidden_tool_summary(_DummySession(visible=[]), language="cn"))

    # Only the two hidden tools should appear.
    joined = "\n".join(entries)
    assert "search_agent_run" in joined
    assert "memory_search" in joined
    # Each hidden tool on its own indented line with its description.
    assert "  - search_agent_run：专注搜索的子agent" in joined, joined
    assert "  - memory_search：搜索记忆条目" in joined, joined
    # Category header lines present.
    assert "网络搜索" in joined
    assert "记忆系统" in joined
    # The flat "工具：{names}" form must be gone.
    assert "工具：" not in joined


def test_hidden_summary_falls_back_on_empty_description(monkeypatch):
    """Empty description → _tool_summary_for_navigation returns the fallback
    'No summary available.' rather than an empty/broken line."""
    rail = _setup_hidden_rail(monkeypatch, always_visible=["bash"])
    rail._meta_tool_names = {"search_tools"}
    rail._cached_all_tool_infos = [
        _make_tool_with_desc("bash", "执行命令"),       # always-visible
        _make_tool_with_desc("search_tools", "检索"),   # meta
        _make_tool_with_desc("ghost_tool", ""),         # hidden, empty desc
    ]

    entries = asyncio.run(rail._build_hidden_tool_summary(_DummySession(visible=[]), language="cn"))

    joined = "\n".join(entries)
    assert "  - ghost_tool：No summary available." in joined, joined


def test_hidden_summary_en_branch_uses_ascii_colon(monkeypatch):
    """English branch formats with ':' (ascii) and 2-space indent, not the
    CN full-width '：'."""
    rail = _setup_hidden_rail(monkeypatch, always_visible=["bash", "read_file"])
    rail._meta_tool_names = {"search_tools"}
    rail._cached_all_tool_infos = [
        _make_tool_with_desc("bash", "run shell"),       # always-visible
        _make_tool_with_desc("read_file", "read a file"),# always-visible
        _make_tool_with_desc("search_tools", "search"), # meta
        _make_tool_with_desc("search_agent_run", "search subagent"),  # hidden
    ]

    entries = asyncio.run(rail._build_hidden_tool_summary(_DummySession(visible=[]), language="en"))

    joined = "\n".join(entries)
    # English header + 2-space indent + ascii colon.
    assert "### Hidden Tools" in joined
    assert "  - search_agent_run: search subagent" in joined, joined
    # CN full-width colon must not leak into the EN branch.
    assert "：" not in joined
