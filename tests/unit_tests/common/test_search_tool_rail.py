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

    # Avoid loading the fastembed model (95MB) in unit tests.
    monkeypatch.setattr(
        mod.tool_retrieval, "ensure_embedding_model", lambda *a, **k: None
    )
    config = SimpleNamespace(
        progressive_tool_enabled=True,
        progressive_tool_always_visible_tools=["bash", "read_file"],
        progressive_tool_default_visible_tools=[],
        progressive_tool_max_loaded_tools=16,
        language="cn",
        tool_retrieval_desc_cap=64,
        tool_retrieval_embedding_model="BAAI/bge-small-zh-v1.5",
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


def test_search_tools_runs_dense_off_event_loop(monkeypatch):
    """P1: _search_tools runs the CPU-bound dense search via asyncio.to_thread
    (off the event loop, not blocking it). Verifies the threaded path returns
    results without deadlocking."""
    import numpy as np
    from types import SimpleNamespace

    rail = _make_rail(monkeypatch)

    class FakeModel:
        def __init__(self, dim=8):
            self._dim = dim

        def embed(self, texts):
            out = []
            for t in texts:
                v = np.zeros(self._dim, dtype=float)
                for w in str(t).lower().split():
                    v[sum(ord(c) for c in w) % self._dim] += 1.0
                out.append(v)
            return out

    model = FakeModel()
    rail._embedding_model = model
    rail._cached_tool_embeddings = {"memory_search": model.embed(["memory_search"])[0]}
    rail._search_corpus = [
        SimpleNamespace(
            name="memory_search",
            description="search memory",
            parameters={"type": "object"},
        )
    ]

    results = asyncio.run(rail._search_tools("memory", 3, 3))
    assert results, "dense search returned no results"
    assert results[0]["name"] == "memory_search"
