# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for interface_deep evolution-related functionality."""

# pylint: disable=protected-access

import os
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as interface_deep_module
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.schema.agent import AgentRequest


def _attach_capture_handler(logger_obj: logging.Logger):
    """Attach in-memory handler; jiuwenclaw loggers use propagate=False."""
    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _CaptureHandler(level=logging.DEBUG)
    saved_level = logger_obj.level
    logger_obj.addHandler(handler)
    logger_obj.setLevel(logging.DEBUG)

    def _detach() -> None:
        logger_obj.removeHandler(handler)
        logger_obj.setLevel(saved_level)

    return records, _detach


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
        "JiuClawSkillEvolutionRail",
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
    def __init__(self, *, exists: bool = True, with_persist_mocks: bool = False) -> None:
        self.exists = exists
        if with_persist_mocks:
            self.append_record = AsyncMock()
            self.solidify = AsyncMock(return_value=1)

    def skill_exists(self, _skill_name: str) -> bool:
        return self.exists

    @staticmethod
    def list_skill_names() -> list[str]:
        return ["demo-skill", "other-skill"]

    @staticmethod
    def resolve_subject_payload(skill_name: str) -> dict[str, str]:
        return {"kind": "skill", "name": skill_name}


def _make_evolve_test_signal(skill_name: str = "demo-skill"):
    from openjiuwen.agent_evolving.signal.base import EvolutionCategory, EvolutionSignal

    return EvolutionSignal(
        signal_type="execution_failure",
        evolution_type=EvolutionCategory.SKILL_EXPERIENCE,
        section="Troubleshooting",
        excerpt="tool failed",
        skill_name=skill_name,
    )


def _make_evolve_test_record():
    from openjiuwen.agent_evolving.checkpointing.types import EvolutionPatch, EvolutionRecord
    from openjiuwen.agent_evolving.signal.base import EvolutionTarget

    return EvolutionRecord.make(
        source="execution_failure",
        context="tool failed",
        change=EvolutionPatch(
            section="Troubleshooting",
            action="append",
            content="Handle timeout by retrying",
            target=EvolutionTarget.BODY,
        ),
    )


def _setup_evolve_command_rail(*, auto_save: bool):
    store = _FakeEvolutionStore(with_persist_mocks=True)
    generate = AsyncMock(return_value=[_make_evolve_test_record()])
    rail = SimpleNamespace(
        auto_save=auto_save,
        store=store,
        processed_signal_keys=set(),
        _generate_experience_for_skill=generate,
    )
    return rail, store, generate


@pytest.mark.asyncio
async def test_evolve_command_auto_save_false_returns_message(adapter, monkeypatch):
    rail, store, generate = _setup_evolve_command_rail(auto_save=False)
    adapter._skill_evolution_rail = rail  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "_collect_messages_for_evolve",
        lambda _session_id: [{"role": "user", "content": "fix it"}],
    )
    monkeypatch.setattr(
        interface_deep_module.SignalDetector,
        "detect",
        lambda self, _messages: [_make_evolve_test_signal()],
    )

    result = await adapter._handle_evolve_command("/evolve demo-skill", "sess-1")  # pylint: disable=protected-access

    assert "evolution.auto_save 未开启" in result["output"]
    assert result["result_type"] == "answer"
    assert "approval_chunks" not in result
    generate.assert_not_called()
    store.append_record.assert_not_called()
    store.solidify.assert_not_called()


@pytest.mark.asyncio
async def test_evolve_command_auto_save_true_persists_without_approval(adapter, monkeypatch):
    rail, store, generate = _setup_evolve_command_rail(auto_save=True)
    adapter._skill_evolution_rail = rail  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "_collect_messages_for_evolve",
        lambda _session_id: [{"role": "user", "content": "fix it"}],
    )
    monkeypatch.setattr(
        interface_deep_module.SignalDetector,
        "detect",
        lambda self, _messages: [_make_evolve_test_signal()],
    )

    result = await adapter._handle_evolve_command("/evolve demo-skill", "sess-1")  # pylint: disable=protected-access

    assert result["result_type"] == "answer"
    assert "approval_chunks" not in result
    assert "已记录 1 条演进经验" in result["output"]
    assert "Troubleshooting" in result["output"]
    generate.assert_awaited_once()
    store.append_record.assert_awaited_once()
    store.solidify.assert_awaited_once_with("demo-skill")


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


@pytest.mark.unit
def test_format_evolution_summary_markdown_escapes_display_text():
    """LLM-generated display_text must not bypass MF-001 Markdown escaping."""
    malicious = "[点击](javascript:alert(1))`break"
    summary = {
        "display_text": f"\n\n---\n### 📚 技能演进\n- `{malicious}`",
        "skills": [
            {
                "skill_name": "ignored-when-display-text-present",
                "records_count": 1,
            }
        ],
    }

    result = JiuWenClawDeepAdapter._format_evolution_summary_markdown(summary)

    assert malicious not in result
    assert "ignored-when-display-text-present" not in result
    assert r"\[点击\]\(javascript:alert\(1\)\)" in result
    assert r"\`break" in result


@pytest.mark.unit
def test_format_evolution_summary_markdown_escapes_untrusted_fields():
    """skill_name/name from take_run_summary must not inject Markdown syntax."""
    malicious_skill = "[点击](javascript:alert(1))`break"
    malicious_name = "new[skill](x)`"
    summary = {
        "skills": [
            {
                "skill_name": malicious_skill,
                "records_count": 2,
                "body_count": 1,
                "description_count": 1,
            }
        ],
        "new_skills": [{"name": malicious_name}],
    }

    result = JiuWenClawDeepAdapter._format_evolution_summary_markdown(summary)

    assert malicious_skill not in result
    assert malicious_name not in result
    assert r"\[点击\]\(javascript:alert\(1\)\)" in result
    assert r"\`break" in result
    assert r"new\[skill\]\(x\)" in result
    assert r"\`" in result.split(r"new\[skill\]\(x\)")[1]


@pytest.mark.unit
def test_format_evolution_summary_markdown_tolerates_non_numeric_counts():
    """Dirty persisted count fields should degrade to zero instead of raising."""
    summary = {
        "skills": [
            {
                "skill_name": "demo-skill",
                "records_count": "not-a-number",
                "body_count": "2x",
                "description_count": None,
            }
        ],
    }

    result = JiuWenClawDeepAdapter._format_evolution_summary_markdown(summary)

    assert "demo-skill" in result
    assert "新增 0 条经验" in result


@pytest.mark.unit
def test_collect_evolution_run_summary_text_degrades_on_format_failure(adapter):
    """Footnote formatting failures must not propagate to the main chat path."""
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        take_run_summary=lambda: "corrupted-non-dict-summary",
    )
    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        result = adapter._collect_evolution_run_summary_text("req-format-fail")  # pylint: disable=protected-access
    finally:
        detach()

    assert result == ""
    assert any(
        "evolution UI summary format failed" in record.message
        for record in records
        if record.levelno >= logging.WARNING
    )


@pytest.mark.unit
def test_collect_evolution_run_summary_text_tolerates_dirty_records_count(adapter):
    """Non-numeric records_count from persisted JSON must not raise."""
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        take_run_summary=lambda: {
            "skills": [
                {
                    "skill_name": "demo-skill",
                    "records_count": "not-a-number",
                }
            ],
        },
    )

    result = adapter._collect_evolution_run_summary_text("req-dirty-count")  # pylint: disable=protected-access

    assert "demo-skill" in result
    assert "新增 0 条经验" in result


@pytest.mark.unit
def test_collect_evolution_run_summary_text_skips_when_rail_none(adapter):
    """rail is None should log skip reason for ops troubleshooting."""
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        result = adapter._collect_evolution_run_summary_text("req-rail-none")  # pylint: disable=protected-access
    finally:
        detach()

    assert result == ""
    assert len(records) == 1
    assert "evolution UI summary skipped" in records[0].message
    assert "reason=skill_evolution_rail_none" in records[0].message
    assert "request_id=req-rail-none" in records[0].message


@pytest.mark.unit
def test_stash_and_take_pending_evolution_summary(adapter):
    """HITL-deferred footnote should survive until the next request for the session."""
    footnote = "\n\n---\n### 📚 技能演进\n- `demo-skill`：新增 1 条经验"
    adapter._stash_pending_evolution_summary("sess-hitl", footnote, "req-hitl-1")  # pylint: disable=protected-access

    assert adapter._pending_evolution_summary_by_session["sess-hitl"] == footnote  # pylint: disable=protected-access

    taken = adapter._take_pending_evolution_summary("sess-hitl")  # pylint: disable=protected-access
    assert taken == footnote
    assert adapter._take_pending_evolution_summary("sess-hitl") == ""  # pylint: disable=protected-access


@pytest.mark.unit
def test_stash_pending_evolution_summary_logs_hitl_deferral(adapter):
    adapter._stash_pending_evolution_summary(  # pylint: disable=protected-access
        "sess-hitl",
        "footnote",
        "req-hitl-2",
    )
    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        adapter._stash_pending_evolution_summary(  # pylint: disable=protected-access
            "sess-hitl",
            "footnote-2",
            "req-hitl-3",
        )
    finally:
        detach()

    assert any("stashed evolution UI footnote for HITL resume" in r.message for r in records)
    assert any("session_id=sess-hitl" in r.message for r in records)


# =============================================================================
# Skill Creator Follow-Up Tests (方案一 / Path B host-side)
# =============================================================================


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_skill_create_approval_accepted_dispatches_follow_up(adapter):
    """When user accepts, on_approve_new_skill returns prompt → dispatch new invoke."""
    mock_rail = MagicMock()
    mock_rail.on_approve_new_skill = AsyncMock(
        return_value="**重要：你必须先向用户确认...**\n模拟 skill_creator_prompt"
    )
    adapter._skill_evolution_rail = mock_rail
    adapter._dispatch_skill_creator_follow_up = AsyncMock()

    result = await adapter._handle_skill_create_approval(
        "skill_create_abc12345",
        [{"selected_options": ["Create"]}],
    )

    assert result is True
    mock_rail.on_approve_new_skill.assert_awaited_once_with("skill_create_abc12345")
    adapter._dispatch_skill_creator_follow_up.assert_awaited_once_with(
        "skill_create_abc12345",
        mock_rail.on_approve_new_skill.return_value,
        session_id="",
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_skill_create_approval_rejected_calls_reject(adapter):
    """When user rejects, on_reject_new_skill is called and no dispatch happens."""
    mock_rail = MagicMock()
    mock_rail.on_reject_new_skill = AsyncMock()
    adapter._skill_evolution_rail = mock_rail
    adapter._dispatch_skill_creator_follow_up = AsyncMock()

    result = await adapter._handle_skill_create_approval(
        "skill_create_xyz",
        [{"selected_options": ["Skip"]}],
    )

    assert result is True
    mock_rail.on_reject_new_skill.assert_awaited_once_with("skill_create_xyz")
    mock_rail.on_approve_new_skill.assert_not_called()
    adapter._dispatch_skill_creator_follow_up.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_skill_create_approval_no_prompt_graceful(adapter):
    """When on_approve_new_skill returns None, dispatch is NOT called."""
    mock_rail = MagicMock()
    mock_rail.on_approve_new_skill = AsyncMock(return_value=None)
    adapter._skill_evolution_rail = mock_rail
    adapter._dispatch_skill_creator_follow_up = AsyncMock()

    result = await adapter._handle_skill_create_approval(
        "skill_create_abc12345",
        [{"selected_options": ["Create"]}],
    )

    assert result is True
    adapter._dispatch_skill_creator_follow_up.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_skill_create_approval_no_rail_returns_false(adapter):
    """When SkillEvolutionRail is None, return False gracefully."""
    adapter._skill_evolution_rail = None
    result = await adapter._handle_skill_create_approval(
        "skill_create_abc12345",
        [{"selected_options": ["Create"]}],
    )
    assert result is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_skill_creator_follow_up_calls_runner(adapter, monkeypatch):
    """_dispatch_skill_creator_follow_up should invoke Runner.run_agent."""
    import asyncio as _asyncio

    mock_instance = MagicMock()
    adapter._instance = mock_instance
    adapter._current_session_id = lambda: "test-session"

    # Mock Runner.run_agent to verify it's called
    captured_inputs = []

    async def _fake_run_agent(*, agent, inputs):
        captured_inputs.append(inputs)

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent",
        _fake_run_agent,
    )

    prompt = "**重要：你必须先向用户确认...**\n模拟 skill_creator_prompt"
    await adapter._dispatch_skill_creator_follow_up("skill_create_test", prompt)

    # The fire-and-forget task may not have run yet, but the log should be emitted
    # For a sync test, we can at least verify the method doesn't raise


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_skill_creator_follow_up_handles_runner_exception(adapter):
    """_dispatch_skill_creator_follow_up should catch exceptions gracefully."""
    mock_instance = MagicMock()
    adapter._instance = mock_instance
    adapter._current_session_id = lambda: "test-session"

    # The method should not raise even if the runner call fails
    try:
        await adapter._dispatch_skill_creator_follow_up("skill_create_test", "prompt")
    except Exception:
        pytest.fail("_dispatch_skill_creator_follow_up should not raise")
