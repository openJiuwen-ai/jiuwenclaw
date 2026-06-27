# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for per-session tool qualification helpers."""

from __future__ import annotations

import pytest
from openjiuwen.core.foundation.tool import LocalFunction, ToolCard

from jiuwenclaw.agentserver.deep_agent.tool_qualify import (
    add_tool_to_resource_mgr,
    clone_tool_for_session,
    qualify_tool_id,
    qualify_tool_instance,
    register_qualified_tool,
    reregister_qualified_tool_in_resource_mgr,
)


class _FakeOkResult:
    @staticmethod
    def is_err() -> bool:
        return False


class _FakeErrResult:
    msg = "resource already exist"

    @staticmethod
    def is_err() -> bool:
        return True

    @classmethod
    def error(cls) -> str:
        return cls.msg


def test_qualify_tool_id_is_idempotent():
    base = "skill_tool"
    agent = "jiuwenclaw_sess1"
    qualified = qualify_tool_id(base, agent)
    assert qualified == "skill_tool_jiuwenclaw_sess1"
    assert qualify_tool_id(qualified, agent) == qualified


def test_clone_tool_for_session_qualifies_card():
    card = ToolCard(
        id="memory_search",
        name="memory_search",
        description="search memory",
        input_params={"type": "object"},
    )

    async def _source(**_kwargs):
        return {"ok": True}

    source = LocalFunction(card=card, func=_source)
    cloned = clone_tool_for_session(source, "jiuwenclaw_mem")

    assert cloned.card.id == "memory_search_jiuwenclaw_mem"
    assert cloned.card.name == "memory_search"


@pytest.mark.asyncio
async def test_clone_tool_for_session_field_style_invoke():
    """memory_search-style tools: schema fields map to func kwargs via LocalFunction."""
    calls: list[dict[str, object]] = []

    async def _source(query: str, **kwargs):
        max_results = kwargs.get("maxResults")
        calls.append({"query": query, "maxResults": max_results})
        return {"results": []}

    card = ToolCard(
        id="memory_search",
        name="memory_search",
        description="search memory",
        input_params={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "maxResults": {"type": "integer"},
            },
            "required": ["query"],
        },
    )
    source = LocalFunction(card=card, func=_source)
    cloned = clone_tool_for_session(source, "jiuwenclaw_mem")

    result = await cloned.invoke({"query": "hello", "maxResults": 5})

    assert result == {"results": []}
    assert calls == [{"query": "hello", "maxResults": 5}]


@pytest.mark.asyncio
async def test_clone_tool_for_session_inputs_wrapper_style_invoke():
    """text_to_image-style tools with an explicit inputs parameter still work."""
    calls: list[dict[str, object]] = []

    async def _source(inputs: dict[str, object], **_kwargs):
        calls.append(dict(inputs))
        return "ok"

    card = ToolCard(
        id="text_to_image",
        name="text_to_image",
        description="generate image",
        input_params={
            "type": "object",
            "properties": {
                "inputs": {
                    "type": "object",
                    "properties": {"prompt": {"type": "string"}},
                },
            },
            "required": ["inputs"],
        },
    )
    source = LocalFunction(card=card, func=_source)
    cloned = clone_tool_for_session(source, "jiuwenclaw_img")

    payload = {"inputs": {"prompt": "a cat"}}
    result = await cloned.invoke(payload)

    assert result == "ok"
    assert calls == [{"prompt": "a cat"}]


def test_qualify_tool_instance_mutates_card():
    card = ToolCard(
        id="image_ocr",
        name="image_ocr",
        description="ocr",
        input_params={"type": "object"},
    )

    async def _noop(**_kwargs):
        return None

    tool = LocalFunction(card=card, func=_noop)
    new_id = qualify_tool_instance(tool, "jiuwenclaw_vis")
    assert new_id == "image_ocr_jiuwenclaw_vis"
    assert tool.card.id == new_id


def test_add_tool_to_resource_mgr_without_refresh_kwarg(monkeypatch):
    """Production ResourceMgr has no refresh=; helper must not pass it."""
    class _ResourceMgr:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def add_tool(self, tool):
            card = getattr(tool, "card", None)
            if card is not None:
                self.tools[card.id] = tool
            return _FakeOkResult()

        def get_tool(self, tool_id):
            return self.tools.get(tool_id)

        def remove_tool(self, tool_id):
            self.tools.pop(tool_id, None)

    import jiuwenclaw.agentserver.deep_agent.tool_qualify as mod

    resource_mgr = _ResourceMgr()
    monkeypatch.setattr(mod.Runner, "resource_mgr", resource_mgr)

    card = ToolCard(
        id="skill_tool",
        name="skill_tool",
        description="skill",
        input_params={"type": "object"},
    )

    async def _noop(**_kwargs):
        return {}

    tool = LocalFunction(card=card, func=_noop)
    agent = type("Agent", (), {"ability_manager": None})()

    register_qualified_tool(agent, tool, "jiuwenclaw_sess")
    assert "skill_tool_jiuwenclaw_sess" in resource_mgr.tools
    add_tool_to_resource_mgr(tool)


def test_reregister_qualified_tool_replaces_existing_entry(monkeypatch):
    class _ResourceMgr:
        def __init__(self) -> None:
            self.tools: dict[str, object] = {}

        def add_tool(self, tool):
            card = getattr(tool, "card", None)
            if card is not None and card.id in self.tools:
                return _FakeErrResult()
            if card is not None:
                self.tools[card.id] = tool
            return _FakeOkResult()

        def get_tool(self, tool_id):
            return self.tools.get(tool_id)

        def remove_tool(self, tool_id):
            self.tools.pop(tool_id, None)

    import jiuwenclaw.agentserver.deep_agent.tool_qualify as mod

    resource_mgr = _ResourceMgr()
    monkeypatch.setattr(mod.Runner, "resource_mgr", resource_mgr)

    card = ToolCard(
        id="web_search",
        name="web_search",
        description="search",
        input_params={"type": "object"},
    )

    async def _noop(**_kwargs):
        return {}

    tool = LocalFunction(card=card, func=_noop)
    qualified_id = "web_search_jiuwenclaw_sess"

    mod.reregister_qualified_tool_in_resource_mgr(tool, "jiuwenclaw_sess")
    assert qualified_id in resource_mgr.tools

    tool2 = LocalFunction(
        card=ToolCard(
            id=qualified_id,
            name="web_search",
            description="search v2",
            input_params={"type": "object"},
        ),
        func=_noop,
    )
    mod.reregister_qualified_tool_in_resource_mgr(tool2, "jiuwenclaw_sess")

    assert qualified_id in resource_mgr.tools
    assert resource_mgr.tools[qualified_id] is tool2
