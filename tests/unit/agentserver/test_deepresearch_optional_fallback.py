from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def test_missing_deepresearch_runtime_keeps_base_chat_importable(monkeypatch):
    """The adapter-facing compatibility module must degrade to no tools."""
    module_name = "_deepresearch_tools_optional_fallback_test"
    module_path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "tools"
        / "deepresearch_tools.py"
    )
    monkeypatch.setitem(
        sys.modules,
        "jiuwenclaw.agentserver.tools.deepresearch.tools",
        None,
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.get_deepresearch_tools() == []
    token = module.push_deepresearch_route(
        "request",
        "officeclaw",
        "session",
        service_id="default",
        agent_id="office",
    )
    module.reset_deepresearch_route(token)
