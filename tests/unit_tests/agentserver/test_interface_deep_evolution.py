# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for interface_deep evolution-related functionality."""

# pylint: disable=protected-access

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.schema.agent import AgentRequest


class DeepAdapterHarness(JiuWenClawDeepAdapter):
    """Test harness to expose protected methods."""
    
    def build_skill_evolution_rail_for_test(self, config: dict[str, Any]):
        """Expose _build_skill_evolution_rail for testing."""
        return self._build_skill_evolution_rail(config)


@pytest.fixture
def adapter():
    """Create a test adapter instance."""
    return DeepAdapterHarness()


def _setup_mocks(monkeypatch: pytest.MonkeyPatch):
    """Set up mocks for SkillEvolutionRail and related classes."""
    captured_args = []
    captured_kwargs = []
    
    def mock_skill_evolution_rail(*args, **kwargs):
        captured_args.append(args)
        captured_kwargs.append(kwargs)
        return MagicMock()
    
    monkeypatch.setattr(
        interface_deep_module,
        "SkillEvolutionRail",
        mock_skill_evolution_rail
    )
    
    # Mock other dependencies
    monkeypatch.setattr(
        interface_deep_module,
        "FileTrajectoryStore",
        lambda *args, **kwargs: MagicMock()
    )
    
    # Mock get_agent_registered_skill_dirs (replaces legacy _resolve_skill_dirs)
    monkeypatch.setattr(
        interface_deep_module,
        "get_agent_registered_skill_dirs",
        lambda: [Path("mock_skills_dir")],
    )

    # Mock _resolve_evolution_trajectory_dir - fix: use instance method signature
    def mock_resolve_evolution_trajectory_dir(self):
        return Path("/mock/trajectory/path")
    
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_resolve_evolution_trajectory_dir",
        mock_resolve_evolution_trajectory_dir
    )
    
    return captured_args, captured_kwargs


@pytest.mark.unit
def test_config_explicit_auto_save_true(adapter, monkeypatch):
    """Test config explicitly sets evolution.auto_save = true."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_save": True,
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is True


@pytest.mark.unit
def test_config_explicit_auto_save_false(adapter, monkeypatch):
    """Test config explicitly sets evolution.auto_save = false."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_save": False,
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is False


@pytest.mark.unit
def test_config_default_auto_save_true(adapter, monkeypatch):
    """Test config doesn't set evolution.auto_save, default should be true."""
    captured_args, captured_kwargs = _setup_mocks(monkeypatch)
    config = {
        "evolution": {
            "auto_scan": False,
        }
    }
    
    adapter.build_skill_evolution_rail_for_test(config)
    
    assert captured_kwargs[0].get("auto_save") is True


class _FakeEvolutionStore:
    def __init__(self, *, exists: bool = True) -> None:
        self.exists = exists

    def skill_exists(self, _skill_name: str) -> bool:
        return self.exists

    @staticmethod
    def list_skill_names() -> list[str]:
        return ["demo-skill", "other-skill"]

    @staticmethod
    def resolve_subject_payload(skill_name: str) -> dict[str, str]:
        return {"kind": "skill", "name": skill_name}


class _FakeRebuildService:
    next_context: dict[str, Any] | None = {"records": [], "overflow_index": {}}
    complete_rebuild_calls: list[dict[str, Any]] = []

    def __init__(self, *, store: Any) -> None:
        self.store = store

    async def prepare_rebuild_context(
        self,
        subject: dict[str, str],
        *,
        user_intent: str | None = None,
    ) -> dict[str, Any] | None:
        return self.next_context

    async def complete_rebuild(self, rebuild_context: dict[str, Any]) -> bool:
        self.complete_rebuild_calls.append(dict(rebuild_context))
        return bool(rebuild_context.get("archive_path"))


@pytest.mark.asyncio
async def test_evolve_rebuild_command_returns_followup(adapter, monkeypatch):
    store = _FakeEvolutionStore()
    adapter._skill_evolution_rail = SimpleNamespace(store=store)  # pylint: disable=protected-access
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **kwargs: f"rebuild {kwargs['subject']['name']} {kwargs['user_intent']}",
    )

    result = await adapter._handle_evolve_rebuild_command(  # pylint: disable=protected-access
        "/evolve_rebuild demo-skill improve examples"
    )

    assert result == {
        "action": "run_rebuild_followup",
        "followup_prompt": "rebuild demo-skill improve examples",
        "skill_name": "demo-skill",
        "subject": {"kind": "skill", "name": "demo-skill"},
        "rebuild_context": {"records": [], "overflow_index": {}},
        "result_type": "followup",
    }


@pytest.mark.asyncio
async def test_evolve_rebuild_command_requires_skill_name(adapter):
    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore())  # pylint: disable=protected-access

    result = await adapter._handle_evolve_rebuild_command("/evolve_rebuild")  # pylint: disable=protected-access

    assert result == {
        "output": "请指定 Skill 名称：`/evolve_rebuild <skill_name> [user_intent]`",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_evolve_rebuild_command_validates_skill_exists(adapter):
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        store=_FakeEvolutionStore(exists=False)
    )

    result = await adapter._handle_evolve_rebuild_command("/evolve_rebuild missing-skill")  # pylint: disable=protected-access

    assert result == {
        "output": "未找到 Skill 'missing-skill'。当前可用：demo-skill、other-skill",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_evolve_rebuild_command_handles_empty_context(adapter, monkeypatch):
    class _EmptyRebuildService(_FakeRebuildService):
        next_context = None

    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore())  # pylint: disable=protected-access
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _EmptyRebuildService)
    monkeypatch.setattr(interface_deep_module, "build_rebuild_command_prompt", lambda **_kwargs: "unused")

    result = await adapter._handle_evolve_rebuild_command("/evolve_rebuild demo-skill")  # pylint: disable=protected-access

    assert result == {
        "output": "Skill 'demo-skill' 未生成可执行的重建指令。",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_evolve_rebuild_routes_to_slash_handler(adapter, monkeypatch):
    adapter._config_cache = {"evolution": {"enabled": True}}  # pylint: disable=protected-access
    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore())  # pylint: disable=protected-access

    async def _fake_rebuild(query: str) -> dict[str, Any]:
        assert query == "/evolve_rebuild demo-skill"
        return {
            "action": "run_rebuild_followup",
            "followup_prompt": "rebuild demo-skill",
            "skill_name": "demo-skill",
            "result_type": "followup",
        }

    monkeypatch.setattr(adapter, "_handle_evolve_rebuild_command", _fake_rebuild)

    result = await adapter._handle_slash_command(  # pylint: disable=protected-access
        "/evolve_rebuild demo-skill",
        session_id="sess-rebuild",
        mode="agent.plan",
    )

    assert result is not None
    assert result["action"] == "run_rebuild_followup"
    assert result["skill_name"] == "demo-skill"


@pytest.mark.asyncio
async def test_process_message_impl_continues_followup_into_runner(adapter, monkeypatch):
    adapter._instance = SimpleNamespace(get_context_usage=lambda **_kwargs: {})  # pylint: disable=protected-access
    adapter._telemetry_rail = None  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda: True)
    monkeypatch.setattr(adapter, "_plain_chat_should_clear_stale_interrupt", lambda _request: False)
    monkeypatch.setattr(adapter, "_bind_runtime_cron_context", lambda **_kwargs: None)
    monkeypatch.setattr(adapter, "_reset_runtime_cron_context", lambda _tokens: None)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: None)
    monkeypatch.setattr(adapter, "_apply_model_to_react_agent", lambda _model: None)
    monkeypatch.setattr(adapter, "_update_runtime_config", AsyncMock())
    monkeypatch.setattr(adapter, "_untrack_session_toolkit", lambda _request_id: None)
    monkeypatch.setattr(interface_deep_module, "setup_permission_context", lambda _request: None)
    monkeypatch.setattr(interface_deep_module, "cleanup_permission_context", lambda _token: None)

    async def _fake_slash_command(_query: str, _session_id: str, _mode: str) -> dict[str, Any]:
        return {
            "action": "run_rebuild_followup",
            "followup_prompt": "review and rebuild demo-skill",
            "rebuild_context": {
                "skill_name": "demo-skill",
                "archive_path": "evolutions.v1.json",
            },
            "result_type": "followup",
        }

    seen_inputs: list[dict[str, Any]] = []
    _FakeRebuildService.complete_rebuild_calls = []

    class _FakeRunner:
        @staticmethod
        async def run_agent(agent: Any, inputs: dict[str, Any]) -> str:
            seen_inputs.append(dict(inputs))
            return "agent completed"

    monkeypatch.setattr(adapter, "_handle_slash_command", _fake_slash_command)
    monkeypatch.setattr(interface_deep_module, "Runner", _FakeRunner)
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore())  # pylint: disable=protected-access

    response = await adapter.process_message_impl(
        AgentRequest(
            request_id="req-followup",
            channel_id="web",
            session_id="sess-followup",
            params={"query": "/evolve_rebuild demo-skill", "mode": "agent.plan"},
        ),
        {"query": "/evolve_rebuild demo-skill"},
    )

    assert seen_inputs == [
        {"query": "review and rebuild demo-skill", "_invoke_turn_id": "req-followup"}
    ]
    assert response.ok is True
    assert response.payload == {"content": "agent completed"}
    assert _FakeRebuildService.complete_rebuild_calls == [
        {"skill_name": "demo-skill", "archive_path": "evolutions.v1.json"},
    ]


@pytest.mark.asyncio
async def test_process_message_impl_skips_complete_rebuild_when_agent_fails(adapter, monkeypatch):
    adapter._instance = SimpleNamespace(get_context_usage=lambda **_kwargs: {})  # pylint: disable=protected-access
    adapter._telemetry_rail = None  # pylint: disable=protected-access
    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore())  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda: True)
    monkeypatch.setattr(adapter, "_plain_chat_should_clear_stale_interrupt", lambda _request: False)
    monkeypatch.setattr(adapter, "_bind_runtime_cron_context", lambda **_kwargs: None)
    monkeypatch.setattr(adapter, "_reset_runtime_cron_context", lambda _tokens: None)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: None)
    monkeypatch.setattr(adapter, "_apply_model_to_react_agent", lambda _model: None)
    monkeypatch.setattr(adapter, "_update_runtime_config", AsyncMock())
    monkeypatch.setattr(adapter, "_untrack_session_toolkit", lambda _request_id: None)
    monkeypatch.setattr(interface_deep_module, "setup_permission_context", lambda _request: None)
    monkeypatch.setattr(interface_deep_module, "cleanup_permission_context", lambda _token: None)
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    _FakeRebuildService.complete_rebuild_calls = []

    async def _fake_slash_command(_query: str, _session_id: str, _mode: str) -> dict[str, Any]:
        return {
            "action": "run_rebuild_followup",
            "followup_prompt": "review and rebuild demo-skill",
            "rebuild_context": {
                "skill_name": "demo-skill",
                "archive_path": "evolutions.v1.json",
            },
            "result_type": "followup",
        }

    class _FailingRunner:
        @staticmethod
        async def run_agent(agent: Any, inputs: dict[str, Any]) -> str:
            raise RuntimeError("rebuild failed")

    monkeypatch.setattr(adapter, "_handle_slash_command", _fake_slash_command)
    monkeypatch.setattr(interface_deep_module, "Runner", _FailingRunner)

    with pytest.raises(RuntimeError, match="rebuild failed"):
        await adapter.process_message_impl(
            AgentRequest(
                request_id="req-followup-fail",
                channel_id="web",
                session_id="sess-followup-fail",
                params={"query": "/evolve_rebuild demo-skill", "mode": "agent.plan"},
            ),
            {"query": "/evolve_rebuild demo-skill"},
        )

    assert _FakeRebuildService.complete_rebuild_calls == []
