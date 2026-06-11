import asyncio
from types import SimpleNamespace

from jiuwenswarm.agents.harness.common.tools.symphony_toolkits import (
    SymphonyToolkit,
)
from jiuwenswarm.agents.harness.common.tools.symphony_status_events import (
    begin_symphony_status_events,
    reset_symphony_status_events,
)
from jiuwenswarm.extensions.registry import ExtensionRegistry


class _CallbackFramework:
    @staticmethod
    def register_sync(*args, **kwargs):
        return None

    async def trigger(self, *args, **kwargs):
        return None


class _StreamSession:
    def __init__(self):
        self.chunks = []

    async def write_stream(self, chunk):
        self.chunks.append(chunk)


def setup_function():
    ExtensionRegistry.reset_instance()


def teardown_function():
    ExtensionRegistry.reset_instance()


def test_toolkit_calls_rpc_handler():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    seen = {}

    async def handler(params, request=None):
        seen.update(params)
        return {"success": True, "params": params}

    registry.register_rpc_handler("symphony.plan", handler)
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )

    result = asyncio.run(SymphonyToolkit().plan("use installed skills"))

    assert result["success"] is True
    assert result["params"] == {"query": "use installed skills"}
    assert result["score_status"] == {"success": True, "exists": True, "stale": False}
    assert "## Symphony score" in result["content"]
    assert "Status: `fresh`" in result["content"]
    assert result["summary"] == result["content"]
    assert seen["query"] == "use installed skills"


def test_toolkit_passes_fast_mode_to_rpc_handler():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    seen = {}

    async def handler(params, request=None):
        del request
        seen.update(params)
        return {"success": True, "params": params}

    registry.register_rpc_handler("symphony.plan", handler)
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )

    result = asyncio.run(SymphonyToolkit().plan("use installed skills", mode="fast"))

    assert result["success"] is True
    assert result["params"] == {"query": "use installed skills", "mode": "fast"}
    assert seen["mode"] == "fast"


def test_toolkit_reports_missing_handler():
    ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )

    result = asyncio.run(SymphonyToolkit().score_status())

    assert result["success"] is False
    assert "symphony.score_status" in result["detail"]


def test_toolkit_plan_refreshes_stale_score_before_planning():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    calls = []

    async def score_status(params, request=None):
        del params, request
        calls.append("score_status")
        return {"success": True, "exists": True, "stale": True}

    async def build_score(params, request=None):
        del params, request
        calls.append("build_score")
        return {"success": True, "updated": True}

    async def plan(params, request=None):
        del request
        calls.append("plan")
        return {"success": True, "params": params}

    registry.register_rpc_handler("symphony.score_status", score_status)
    registry.register_rpc_handler("symphony.build_score", build_score)
    registry.register_rpc_handler("symphony.plan", plan)

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert calls == ["score_status", "build_score", "plan"]
    assert result["success"] is True
    assert result["score_build"] == {"success": True, "updated": True}
    assert "Status: `stale`" in result["content"]
    assert "Update: `succeeded`" in result["content"]


def test_toolkit_plan_emits_status_events_for_fresh_score():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    session = _StreamSession()
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda params, request=None: {"success": True, "params": params},
    )

    token = begin_symphony_status_events(session, "parent-call")
    try:
        result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))
    finally:
        reset_symphony_status_events(token)

    assert result["success"] is True
    assert [chunk.type for chunk in session.chunks] == [
        "chat.symphony_status",
        "chat.symphony_status",
    ]
    payloads = [chunk.payload for chunk in session.chunks]
    assert [payload["phase"] for payload in payloads] == [
        "checking_score",
        "planning",
    ]
    assert all(payload["source"] == "symphony_compose_score" for payload in payloads)
    assert all(payload["operation_id"] == "parent-call" for payload in payloads)
    assert all(payload["status"] == "in_progress" for payload in payloads)


def test_toolkit_plan_emits_refresh_status_for_stale_score():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    session = _StreamSession()
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": True},
    )
    registry.register_rpc_handler(
        "symphony.build_score",
        lambda _params, request=None: {"success": True, "updated": True},
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda params, request=None: {"success": True, "params": params},
    )

    token = begin_symphony_status_events(session, "parent-call")
    try:
        result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))
    finally:
        reset_symphony_status_events(token)

    assert result["success"] is True
    assert [chunk.type for chunk in session.chunks] == [
        "chat.symphony_status",
        "chat.symphony_status",
        "chat.symphony_status",
    ]
    assert [chunk.payload["phase"] for chunk in session.chunks] == [
        "checking_score",
        "building_score",
        "planning",
    ]


def test_toolkit_plan_emits_failed_status_before_stopping():
    ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    session = _StreamSession()

    token = begin_symphony_status_events(session, "parent-call")
    try:
        result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))
    finally:
        reset_symphony_status_events(token)

    assert result["success"] is False
    assert [chunk.type for chunk in session.chunks] == [
        "chat.symphony_status",
        "chat.symphony_status",
    ]
    result_payload = session.chunks[-1].payload
    assert result_payload["phase"] == "checking_score"
    assert result_payload["status"] == "failed"
    assert "symphony.score_status" in result_payload["detail"]


def test_toolkit_plan_preserves_plan_markdown_after_score_summary():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {
            "success": True,
            "exists": True,
            "stale": False,
            "reason": "up to date",
        },
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda _params, request=None: {
            "success": True,
            "presentation": {
                "markdown": "## Recommended Plan\n\nUse skill A, then skill B.",
                "mermaid": "flowchart LR\n  A --> B",
            },
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["direct_display"] is True
    assert result["display_format"] == "markdown"
    assert result["content"].startswith("## Symphony score")
    assert "Detail: up to date" in result["content"]
    assert "## Recommended Plan" in result["content"]
    assert result["mermaid"] == "flowchart LR\n  A --> B"
    assert result["markdown"] == result["content"]
    assert result["summary"] == result["content"]


def test_toolkit_plan_stops_when_score_status_fails():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    calls = []

    async def plan(params, request=None):
        del params, request
        calls.append("plan")
        return {"success": True}

    registry.register_rpc_handler("symphony.plan", plan)

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["success"] is False
    assert "symphony.score_status failed" in result["detail"]
    assert calls == []


def test_toolkit_get_tools_respects_symphony_enabled(monkeypatch):
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.symphony_toolkits.load_symphony_config",
        lambda: SimpleNamespace(enabled=False),
    )

    assert SymphonyToolkit().get_tools() == []

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.symphony_toolkits.load_symphony_config",
        lambda: SimpleNamespace(enabled=True),
    )

    tool_names = [tool.card.name for tool in SymphonyToolkit().get_tools()]
    assert "symphony_compose_score" in tool_names
    compose_tool = next(
        tool for tool in SymphonyToolkit().get_tools()
        if tool.card.name == "symphony_compose_score"
    )
    assert compose_tool.card.input_params["properties"]["mode"]["enum"] == ["fast"]
