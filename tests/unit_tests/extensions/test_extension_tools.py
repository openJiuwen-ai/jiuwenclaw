# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

from openjiuwen.core.foundation.tool import LocalFunction
import pytest

from jiuwenclaw.extensions.extension_tool_entry import ExtensionLocalToolEntry
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.extensions.sdk.local_tool_builder import (
    extension_tool_card_id,
    make_extension_tools,
    make_tool,
)


@pytest.fixture
def clean_registry():
    ExtensionRegistry.reset_instance()
    cf = MagicMock()
    reg = ExtensionRegistry.create_instance(cf, {}, MagicMock())
    yield reg
    ExtensionRegistry.reset_instance()


def test_extension_tool_card_id_stable():
    assert extension_tool_card_id("my-ext", "hello") == "jiuwenclaw.ext.my-ext.hello"
    assert "jiuwenclaw.ext" in extension_tool_card_id("a/b", "x y")


def test_make_tool():
    def _fn(x: str) -> str:
        return x

    entry = ExtensionLocalToolEntry(
        name="demo_tool",
        description="demo",
        input_params={"x": {"type": "string"}},
        func=_fn,
        source_id="unit",
    )
    tool = make_tool(entry)
    assert isinstance(tool, LocalFunction)
    assert tool.card.name == "demo_tool"
    assert tool.card.id == extension_tool_card_id("unit", "demo_tool")


def test_register_extension_tool_validation(clean_registry: ExtensionRegistry):
    with pytest.raises(ValueError, match="name"):
        clean_registry.register_tool("", "d", {}, lambda: None)
    with pytest.raises(TypeError, match="input_params"):
        clean_registry.register_tool("a", "d", "bad", lambda: None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="func"):
        clean_registry.register_tool("a", "d", {}, "not callable")  # type: ignore[arg-type]


def test_make_extension_tools(clean_registry: ExtensionRegistry):
    clean_registry.register_tool(
        "ext_hello",
        "say hi",
        {
            "type": "object",
            "properties": {"q": {"type": "string", "description": "query"}},
            "required": ["q"],
        },
        lambda q: f"hi:{q}",
        source_id="test_plugin",
    )
    tools = make_extension_tools(clean_registry.extension_local_tool_entries)
    assert len(tools) == 1
    assert tools[0].card.name == "ext_hello"
    out = asyncio.run(tools[0].invoke({"q": "x"}))
    assert out == "hi:x"
