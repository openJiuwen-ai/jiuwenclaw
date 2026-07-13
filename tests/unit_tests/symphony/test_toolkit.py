import asyncio
import json
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.symphony_toolkits import (
    SymphonyToolkit,
)
from jiuwenswarm.extensions.registry import ExtensionRegistry


class _CallbackFramework:
    @staticmethod
    def register_sync(*args, **kwargs):
        return None

    async def trigger(self, *args, **kwargs):
        return None


def setup_function():
    ExtensionRegistry.reset_instance()


def teardown_function():
    ExtensionRegistry.reset_instance()


@pytest.fixture(autouse=True)
def enabled_symphony_config(monkeypatch):
    def fake_load_symphony_config(config=None):
        if isinstance(config, dict):
            raw = config.get("symphony")
            if isinstance(raw, dict) and "enabled" in raw:
                return SimpleNamespace(enabled=bool(raw["enabled"]))
        return SimpleNamespace(enabled=True)

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.symphony_toolkits.load_symphony_config",
        fake_load_symphony_config,
    )


def _write_score_metadata(
    tmp_path,
    *,
    version="score-v1",
    created_at="2026-06-13T10:00:00+00:00",
):
    score_dir = tmp_path / "score"
    version_dir = score_dir / "versions" / version
    version_dir.mkdir(parents=True)
    (score_dir / "current.json").write_text(
        json.dumps(
            {
                "schema_version": "Symphony-score-pointer-v1",
                "version": version,
                "path": f"versions/{version}",
            }
        ),
        encoding="utf-8",
    )
    (version_dir / "score_manifest.json").write_text(
        json.dumps({"created_at": created_at}),
        encoding="utf-8",
    )
    return score_dir


def test_toolkit_calls_rpc_handler():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    seen = {}

    async def handler(params, request=None):
        seen.update(params)
        return {
            "success": True,
            "params": params,
            "presentation": {
                "markdown": "## Recommended Plan\n\nUse installed skills.",
                "mermaid": "flowchart LR\n  A",
            },
        }

    registry.register_rpc_handler("symphony.plan", handler)
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )

    result = asyncio.run(SymphonyToolkit().plan("use installed skills"))

    assert result["success"] is True
    assert result["score_status"] == {"success": True, "exists": True, "stale": False}
    assert result["score_build"] == {"rebuilt": False, "reason": "not_required"}
    assert result["content"].startswith("## Recommended Plan")
    assert "## Symphony score" not in result["content"]
    assert "Status: `fresh`" not in result["content"]
    assert "Update: `not required`" not in result["content"]
    for key in ("params", "result", "presentation", "markdown", "summary"):
        assert key not in result
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
    assert "params" not in result
    assert seen["mode"] == "fast"


def test_toolkit_passes_candidate_skill_ids_to_rpc_handler():
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

    result = asyncio.run(
        SymphonyToolkit().plan(
            "use installed skills",
            candidate_skill_ids=[" skill-a ", "", "skill-a", "skill-b"],
        )
    )

    assert result["success"] is True
    assert "params" not in result
    assert seen["candidate_skill_ids"] == ["skill-a", "skill-b"]


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
        return {
            "success": True,
            "updated": True,
            "llm_token_usage": {"total": {"total_tokens": 123}},
            "build_progress": {
                "stage": "update.done",
                "label": "done",
                "percent": 100,
                "status": "success",
                "current": 1,
                "total": 1,
                "ts": "drop-me",
                "llm_token_usage": {"total": {"total_tokens": 123}},
            },
        }

    async def plan(params, request=None):
        del request
        calls.append("plan")
        return {
            "success": True,
            "params": params,
            "presentation": {
                "markdown": "## Recommended Plan\n\nUse refreshed score.",
                "mermaid": "flowchart LR\n  A",
            },
        }

    registry.register_rpc_handler("symphony.score_status", score_status)
    registry.register_rpc_handler("symphony.build_score", build_score)
    registry.register_rpc_handler("symphony.plan", plan)

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert calls == ["score_status", "build_score", "plan"]
    assert result["success"] is True
    assert result["score_build"] == {
        "rebuilt": True,
        "success": True,
        "build_progress": {
            "stage": "update.done",
            "label": "done",
            "percent": 100,
            "status": "success",
            "current": 1,
            "total": 1,
        },
        "llm_total_tokens": 123,
    }
    assert result["content"].startswith("## Recommended Plan")
    assert "Status: `stale`" not in result["content"]
    assert "Update: `succeeded`" not in result["content"]
    assert "Build tokens: `123`" not in result["content"]


def test_toolkit_plan_succeeds_for_fresh_score(tmp_path):
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    score_dir = _write_score_metadata(tmp_path)
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {
            "success": True,
            "score_dir": str(score_dir),
            "exists": True,
            "stale": False,
            "build_progress": {
                "stage": "update.done",
                "label": "done",
                "percent": 100,
                "status": "success",
                "llm_token_usage": {"total": {"total_tokens": 999}},
            },
            "llm_token_usage": {"total": {"total_tokens": 999}},
        },
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda params, request=None: {
            "success": True,
            "params": params,
            "presentation": {
                "markdown": "## Recommended Plan\n\nUse fresh score.",
                "mermaid": "flowchart LR\n  A",
            },
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["success"] is True
    assert result["score_build"] == {
        "rebuilt": False,
        "reason": "not_required",
        "version": "score-v1",
        "score_created_at": "2026-06-13T10:00:00+00:00",
    }
    assert "build_progress" not in result["score_status"]
    assert "llm_token_usage" not in result["score_status"]
    assert "llm_total_tokens" not in result["score_build"]
    assert result["content"].startswith("## Recommended Plan")
    assert "Score created: `2026-06-13T10:00:00+00:00`" not in result["content"]


def test_toolkit_plan_succeeds_after_refreshing_stale_score():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
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

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["success"] is True


def test_toolkit_plan_returns_compact_failure_when_score_build_fails():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": True},
    )
    registry.register_rpc_handler(
        "symphony.build_score",
        lambda _params, request=None: {
            "success": False,
            "detail": "build failed",
            "llm_token_usage": {"total": {"total_tokens": 999}},
            "build_progress": {
                "stage": "update.failed",
                "label": "failed",
                "percent": 100,
                "status": "error",
                "llm_token_usage": {"total": {"total_tokens": 999}},
            },
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["success"] is False
    assert result["score_build"] == {
        "rebuilt": True,
        "success": False,
        "detail": "build failed",
        "build_progress": {
            "stage": "update.failed",
            "label": "failed",
            "percent": 100,
            "status": "error",
        },
    }
    assert "llm_token_usage" not in result["score_build"]
    assert "llm_total_tokens" not in result["score_build"]


def test_toolkit_plan_returns_failure_when_score_status_fails():
    ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )

    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert result["success"] is False
    assert "symphony.score_status" in result["detail"]


def test_toolkit_plan_preserves_display_content_after_score_summary():
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
    assert result["content"].startswith("## Recommended Plan")
    assert "## Symphony score" not in result["content"]
    assert "Detail: up to date" not in result["content"]
    assert result["mermaid"] == "flowchart LR\n  A --> B"
    for key in ("result", "presentation", "markdown", "summary"):
        assert key not in result


def test_toolkit_complete_plan_defaults_to_force_finish_display():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda _params, request=None: {
            "success": True,
            "status": "ready",
            "recommended_plans": [
                {
                    "status": "ready",
                    "steps": [{"skill_id": "skill-a"}],
                    "missing_inputs": [],
                }
            ],
            "execution_graph": {"nodes": [{"id": "skill-a"}]},
            "presentation": {"markdown": "## Plan", "mermaid": "flowchart LR\n  A"},
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose skill plan"))

    assert result["continue_after_display"] is False
    assert "followup_action" not in result


def test_toolkit_plan_returns_compact_plan_and_skill_retrieval():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    candidate_records = [
        {
            "rank": index,
            "skill_id": f"skill-{index}",
            "worker_id": f"worker-{index}",
            "resolved_payload": f"skill-{index}",
            "skill_name": f"Skill {index}",
            "score": 1.0 - index / 100,
            "source": "structured_retrieval",
            "extra": "drop",
        }
        for index in range(12)
    ]

    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {
            "success": True,
            "score_dir": "drop-me",
            "exists": True,
            "stale": False,
            "skill_count": 12,
            "build_progress": {
                "stage": "graph.resolve.progress",
                "label": "Resolving",
                "percent": 72,
                "status": "running",
                "current": 7,
                "total": 10,
                "ts": "drop-me",
                "llm_token_usage": {"total": {"total_tokens": 456}},
            },
        },
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda params, request=None: {
            "success": True,
            "score_dir": "drop-me",
            "query": "drop me",
            "mode": params.get("mode"),
            "planning_mode": "one_shot_fast",
            "llm_call_count": 1,
            "candidate_skill_count": 2,
            "candidate_edge_count": 1,
            "status": "ready",
            "recommended_plans": [
                {
                    "title": "Compact Plan",
                    "status": "ready",
                    "reason": "Use this plan.",
                    "steps": [
                        {
                            "step": 1,
                            "skill_id": "skill-1",
                            "name": "Skill 1",
                            "inputs": [{"name": "brief", "type": "text"}],
                            "outputs": [{"name": "draft", "type": "markdown"}],
                            "reason": "matches request",
                            "extra": "drop",
                        }
                    ],
                    "can_feed_edges": [
                        {
                            "source_id": "skill-1",
                            "target_id": "skill-2",
                            "confidence": 0.91,
                            "evidence": {"drop": True},
                        }
                    ],
                    "missing_inputs": [
                        {"skill_id": "skill-1", "name": "brief", "type": "text"}
                    ],
                    "produced_artifacts": [{"name": "draft", "type": "markdown"}],
                }
            ],
            "skill_retrieval": {
                "source": "input",
                "used": True,
                "candidate_skill_ids": ["skill-1", "skill-2"],
                "candidate_count": 2,
                "fallback_reason": "",
                "candidate_records": candidate_records,
            },
            "validation": {"valid": True, "drop": "debug details"},
            "execution_graph": {
                "nodes": [{"id": "skill-1", "description": "drop"}],
                "edges": [{"source": "skill-1", "target": "skill-2"}],
            },
            "presentation": {
                "markdown": "## Compact Plan\n\nUse Skill 1.",
                "mermaid": "flowchart LR\n  A",
            },
        },
    )

    result = asyncio.run(
        SymphonyToolkit().plan("compose compact skill plan", mode="fast")
    )

    assert result["success"] is True
    assert "## Compact Plan" in result["content"]
    assert result["mermaid"] == "flowchart LR\n  A"
    for key in (
        "result",
        "presentation",
        "markdown",
        "summary",
        "score_dir",
        "query",
        "execution_graph",
        "validation",
    ):
        assert key not in result
    assert result["plan"] == {
        "title": "Compact Plan",
        "status": "ready",
        "reason": "Use this plan.",
        "steps": [
            {
                "step": 1,
                "skill_id": "skill-1",
                "reason": "matches request",
                "name": "Skill 1",
            }
        ],
        "can_feed_edges": [
            {"source_id": "skill-1", "target_id": "skill-2", "confidence": 0.91}
        ],
        "missing_inputs": [
            {"skill_id": "skill-1", "name": "brief", "type": "text"}
        ],
    }
    assert result["metrics"] == {
        "planning_mode": "one_shot_fast",
        "llm_call_count": 1,
        "candidate_skill_count": 2,
        "candidate_edge_count": 1,
        "mode": "fast",
    }
    skill_retrieval = result["skill_retrieval"]
    assert "build_progress" not in result["score_status"]
    assert result["score_build"] == {"rebuilt": False, "reason": "not_required"}
    assert skill_retrieval["source"] == "input"
    assert skill_retrieval["candidate_skill_ids"] == ["skill-1", "skill-2"]
    assert skill_retrieval["candidate_count"] == 2
    assert len(skill_retrieval["candidate_records"]) == 10
    assert skill_retrieval["candidate_records"][0] == {
        "rank": 0,
        "skill_id": "skill-0",
        "skill_name": "Skill 0",
        "score": 1.0,
        "source": "structured_retrieval",
    }
    assert "worker_id" not in skill_retrieval["candidate_records"][0]
    assert "resolved_payload" not in skill_retrieval["candidate_records"][0]


def test_toolkit_no_plan_continues_for_skill_discovery():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda _params, request=None: {
            "success": True,
            "status": "no_plan",
            "recommended_plans": [{"status": "no_plan", "steps": []}],
            "execution_graph": {"nodes": []},
            "presentation": {"markdown": "## No plan", "mermaid": "flowchart LR\n  none"},
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose missing skill plan"))

    assert result["continue_after_display"] is True
    assert result["followup_action"] == "external_skill_discovery"


def test_toolkit_needs_input_does_not_continue_for_skill_discovery():
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    registry.register_rpc_handler(
        "symphony.score_status",
        lambda _params, request=None: {"success": True, "exists": True, "stale": False},
    )
    registry.register_rpc_handler(
        "symphony.plan",
        lambda _params, request=None: {
            "success": True,
            "status": "needs_input",
            "recommended_plans": [
                {
                    "status": "needs_input",
                    "steps": [],
                    "missing_inputs": [{"name": "brief", "type": "text"}],
                }
            ],
            "execution_graph": {"nodes": []},
            "presentation": {"markdown": "## Need input", "mermaid": "flowchart LR\n  none"},
        },
    )

    result = asyncio.run(SymphonyToolkit().plan("compose skill plan"))

    assert result["continue_after_display"] is False
    assert "followup_action" not in result


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


def test_toolkit_execution_respects_latest_disabled_config(monkeypatch):
    registry = ExtensionRegistry.create_instance(
        callback_framework=_CallbackFramework(),
        config={},
        logger=object(),
    )
    calls = []

    async def score_status(params, request=None):
        del params, request
        calls.append("score_status")
        return {"success": True, "exists": True, "stale": False}

    async def build_score(params, request=None):
        del params, request
        calls.append("build_score")
        return {"success": True}

    async def plan(params, request=None):
        del params, request
        calls.append("plan")
        return {"success": True}

    registry.register_rpc_handler("symphony.score_status", score_status)
    registry.register_rpc_handler("symphony.build_score", build_score)
    registry.register_rpc_handler("symphony.plan", plan)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.symphony_toolkits.load_symphony_config",
        lambda config=None: SimpleNamespace(enabled=False),
    )

    status = asyncio.run(SymphonyToolkit().score_status())
    refresh = asyncio.run(SymphonyToolkit().refresh_score())
    result = asyncio.run(SymphonyToolkit().plan("compose installed skills"))

    assert status["success"] is False
    assert status["disabled"] is True
    assert refresh["success"] is False
    assert refresh["disabled"] is True
    assert result["success"] is False
    assert result["disabled"] is True
    assert "symphony.enabled=false" in result["detail"]
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
    assert compose_tool.card.input_params["properties"]["candidate_skill_ids"] == {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "Optional installed skill worker_id values returned by "
            "skill_branch_explore. When provided, Symphony composes "
            "from these candidate skills and their eligible neighbors."
        ),
    }
    description = compose_tool.card.description
    assert "skill capabilities, skill chaining, skill ordering" in description
    assert "skill_branch_explore" in description
    assert "candidate_skill_ids" in description
    assert "search_skill to discover external skills" in description
    assert "install_skill" in description
    assert "symphony_refresh_score" in description
    assert "currently installed skills" not in description
    assert "currently installed skills" not in (
        compose_tool.card.input_params["properties"]["query"]["description"]
    )


def test_toolkit_get_tools_respects_config_snapshot():
    assert SymphonyToolkit().get_tools({"symphony": {"enabled": False}}) == []

    tool_names = [
        tool.card.name
        for tool in SymphonyToolkit().get_tools({"symphony": {"enabled": True}})
    ]

    assert "symphony_compose_score" in tool_names
