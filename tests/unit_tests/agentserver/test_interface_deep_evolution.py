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
from jiuwenclaw.schema.agent import AgentRequest, AgentResponseChunk


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


@pytest.mark.unit
def test_build_skill_evolution_rail_wires_file_trajectory_store(adapter, monkeypatch):
    """trajectory_store must be passed into JiuClawSkillEvolutionRail for disk persistence."""
    captured_args: list[tuple] = []
    captured_kwargs: list[dict] = []
    captured_trajectory_paths: list[Path] = []

    def mock_skill_evolution_rail(*args, **kwargs):
        captured_args.append(args)
        captured_kwargs.append(kwargs)
        return MagicMock()

    def mock_file_trajectory_store(path: Path):
        captured_trajectory_paths.append(path)
        return MagicMock(name="trajectory_store_instance")

    monkeypatch.setattr(
        interface_deep_module,
        "JiuClawSkillEvolutionRail",
        mock_skill_evolution_rail,
    )
    monkeypatch.setattr(
        interface_deep_module,
        "FileTrajectoryStore",
        mock_file_trajectory_store,
    )
    monkeypatch.setattr(
        interface_deep_module,
        "get_agent_registered_skill_dirs",
        lambda: [Path("mock_skills_dir")],
    )

    mock_trajectory_dir = Path("/mock/trajectory/path")
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_resolve_evolution_trajectory_dir",
        lambda self: mock_trajectory_dir,
    )

    adapter.build_skill_evolution_rail_for_test({"evolution": {"auto_scan": False}})

    assert captured_trajectory_paths == [mock_trajectory_dir]
    assert captured_kwargs[0]["trajectory_store"]._mock_name == "trajectory_store_instance"


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

    patch = EvolutionPatch(
        section="Troubleshooting",
        action="append",
        content="Handle timeout by retrying",
        target=EvolutionTarget.BODY,
    )
    # enterprise-dev: make(EvolutionRecordSpec(...)); older: make(source=..., ...)
    try:
        from openjiuwen.agent_evolving.checkpointing.types import EvolutionRecordSpec

        return EvolutionRecord.make(
            EvolutionRecordSpec(
                source="execution_failure",
                context="tool failed",
                change=patch,
            )
        )
    except (ImportError, TypeError):
        return EvolutionRecord.make(
            source="execution_failure",
            context="tool failed",
            change=patch,
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


async def _mock_signal_detect(_self, _messages):
    return [_make_evolve_test_signal()]


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
        _mock_signal_detect,
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
        _mock_signal_detect,
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
    prepare_calls: list[dict[str, Any]] = []

    def __init__(self, *, store: Any, **kwargs: Any) -> None:
        self.store = store
        self.kwargs = kwargs

    async def prepare_rebuild_context(
        self,
        subject: dict[str, str],
        *,
        user_intent: str | None = None,
        record_ids: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any] | None:
        self.prepare_calls.append(
            {
                "subject": dict(subject),
                "user_intent": user_intent,
                "record_ids": list(record_ids) if record_ids is not None else None,
                "kwargs": dict(kwargs),
            }
        )
        return self.next_context

    async def complete_rebuild(self, rebuild_context: dict[str, Any]) -> bool:
        self.complete_rebuild_calls.append(dict(rebuild_context))
        return bool(rebuild_context.get("archive_path"))


def _mock_disk_evolution_store(adapter, monkeypatch, store: Any | None = None) -> Any:
    """Wire prepare/finalize to a fake disk store (no SkillEvolutionRail)."""
    disk_store = store if store is not None else _FakeEvolutionStore()
    monkeypatch.setattr(adapter, "_get_disk_evolution_store", lambda: disk_store)
    return disk_store


@pytest.mark.asyncio
async def test_prepare_rebuild_followup_returns_followup(adapter, monkeypatch):
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    _FakeRebuildService.prepare_calls = []
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **kwargs: f"rebuild {kwargs['subject']['name']} {kwargs['user_intent']}",
    )

    result = await adapter._prepare_rebuild_followup(  # pylint: disable=protected-access
        "demo-skill",
        user_intent="improve examples",
    )

    assert result == {
        "action": "run_rebuild_followup",
        "followup_prompt": "rebuild demo-skill improve examples",
        "skill_name": "demo-skill",
        "subject": {"kind": "skill", "name": "demo-skill"},
        "rebuild_context": {"records": [], "overflow_index": {}},
        "result_type": "followup",
    }
    assert _FakeRebuildService.prepare_calls == [
        {
            "subject": {"kind": "skill", "name": "demo-skill"},
            "user_intent": "improve examples",
            "record_ids": None,
            "kwargs": {"min_score": 0.5},
        }
    ]


@pytest.mark.asyncio
async def test_prepare_rebuild_followup_requires_skill_name(adapter, monkeypatch):
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)

    result = await adapter._prepare_rebuild_followup("")  # pylint: disable=protected-access

    assert result == {
        "output": "未指定 Skill 名称，无法自动重建版本。",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_prepare_rebuild_followup_validates_skill_exists(adapter, monkeypatch):
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch, _FakeEvolutionStore(exists=False))

    result = await adapter._prepare_rebuild_followup("missing-skill")  # pylint: disable=protected-access

    assert result == {
        "output": "未找到 Skill 'missing-skill'。当前可用：demo-skill、other-skill",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_prepare_rebuild_followup_handles_empty_context(adapter, monkeypatch):
    class _EmptyRebuildService(_FakeRebuildService):
        next_context = None

    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _EmptyRebuildService)
    monkeypatch.setattr(interface_deep_module, "build_rebuild_command_prompt", lambda **_kwargs: "unused")

    result = await adapter._prepare_rebuild_followup("demo-skill")  # pylint: disable=protected-access

    assert result == {
        "output": "Skill 'demo-skill' 未生成可执行的重建指令。",
        "result_type": "error",
    }


@pytest.mark.asyncio
async def test_prepare_and_finalize_rebuild_without_rail(adapter, monkeypatch):
    """prepare/finalize must work with rail=None via disk EvolutionStore."""
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    _FakeRebuildService.prepare_calls = []
    _FakeRebuildService.complete_rebuild_calls = []
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )

    prepared = await adapter._prepare_rebuild_followup("demo-skill")  # pylint: disable=protected-access
    assert prepared["result_type"] == "followup"
    assert adapter._skill_evolution_rail is None  # pylint: disable=protected-access

    finalized = await adapter._finalize_rebuild_followup(prepared)  # pylint: disable=protected-access
    assert finalized["cleared"] is True
    assert _FakeRebuildService.complete_rebuild_calls
    assert adapter._skill_evolution_rail is None  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_evolve_rebuild_slash_returns_removed_message(adapter):
    adapter._config_cache = {"evolution": {"enabled": True}}  # pylint: disable=protected-access
    adapter._skill_evolution_rail = SimpleNamespace(store=_FakeEvolutionStore(), auto_save=True)  # pylint: disable=protected-access

    result = await adapter._handle_slash_command(  # pylint: disable=protected-access
        "/evolve_rebuild demo-skill",
        session_id="sess-rebuild",
        mode="agent.plan",
    )

    assert result is not None
    assert result["result_type"] == "answer"
    assert "/evolve_rebuild" in result["output"]
    assert "已移除" in result["output"]


@pytest.mark.asyncio
async def test_evolve_command_does_not_prepare_rebuild(adapter, monkeypatch):
    rail, store, generate = _setup_evolve_command_rail(auto_save=True)
    adapter._skill_evolution_rail = rail  # pylint: disable=protected-access
    prepare = AsyncMock()
    monkeypatch.setattr(adapter, "_prepare_rebuild_followup", prepare)
    monkeypatch.setattr(
        adapter,
        "_collect_messages_for_evolve",
        lambda _session_id: [{"role": "user", "content": "fix it"}],
    )
    monkeypatch.setattr(
        interface_deep_module.SignalDetector,
        "detect",
        _mock_signal_detect,
    )

    result = await adapter._handle_evolve_command("/evolve demo-skill", "sess-1")  # pylint: disable=protected-access

    assert result["result_type"] == "answer"
    assert "已记录 1 条演进经验" in result["output"]
    store.append_record.assert_awaited_once()
    store.solidify.assert_awaited_once_with("demo-skill")
    prepare.assert_not_awaited()
    generate.assert_awaited_once()


def test_make_rebuild_service_passes_llm_params(adapter, monkeypatch):
    """_make_rebuild_service must inject llm/model/language for changelog classification."""
    captured: dict[str, Any] = {}

    class _CapturingService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _CapturingService)
    adapter._model = "fake_model"  # pylint: disable=protected-access
    adapter._model_request_config = SimpleNamespace(model="test-model")  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")

    adapter._make_rebuild_service(store=object())  # pylint: disable=protected-access

    assert captured["llm"] == "fake_model"
    assert captured["model"] == "test-model"
    assert captured["language"] == "en"


def test_make_rebuild_service_warns_on_model_fallback(adapter, monkeypatch):
    """Unresolved model name should fall back with a warning for ops visibility."""
    captured: dict[str, Any] = {}

    class _CapturingService:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _CapturingService)
    adapter._model = "fake_model"  # pylint: disable=protected-access
    adapter._model_request_config = None  # pylint: disable=protected-access
    adapter._config_cache = {}  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        adapter._make_rebuild_service(store=object())  # pylint: disable=protected-access
    finally:
        detach()

    assert captured["model"] == "gpt-4"
    assert captured["language"] == "cn"
    assert any(
        "model name unresolved" in record.getMessage() and record.levelno == logging.WARNING
        for record in records
    )


@pytest.mark.asyncio
async def test_finalize_rebuild_followup_completes_rebuild(adapter, monkeypatch):
    _FakeRebuildService.complete_rebuild_calls = []
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)

    finalized = await adapter._finalize_rebuild_followup(  # pylint: disable=protected-access
        {
            "action": "run_rebuild_followup",
            "rebuild_context": {
                "skill_name": "demo-skill",
                "archive_path": "evolutions.v1.json",
            },
            "result_type": "followup",
        }
    )

    assert finalized["cleared"] is True
    assert _FakeRebuildService.complete_rebuild_calls == [
        {"skill_name": "demo-skill", "archive_path": "evolutions.v1.json"},
    ]


@pytest.mark.asyncio
async def test_generate_evolution_merge_version_success(adapter, monkeypatch):
    _FakeRebuildService.prepare_calls = []
    _FakeRebuildService.complete_rebuild_calls = []
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )

    async def _fake_follow_up_stream(**_kwargs):
        yield AgentResponseChunk(
            request_id="req-merge",
            channel_id="web",
            payload={"event_type": "chat.delta", "content": "rewriting"},
            is_complete=False,
        )

    monkeypatch.setattr(adapter, "_iter_skill_creator_follow_up_stream", _fake_follow_up_stream)
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    stream_ctx = adapter.MergeVersionStreamContext(
        base_inputs={"query": "hello"},
        stream_request_id="req-merge",
        channel_id="web",
        session_id="sess-merge",
    )

    result = await adapter.generate_evolution_merge_version(
        skill_name="demo-skill",
        skill_path="/tmp/demo-skill/SKILL.md",
        record_ids=["ev_1"],
        user_intent="tighten docs",
        stream_ctx=stream_ctx,
    )

    assert result["ok"] is True
    assert result["cleared"] is True
    assert result["archive_path"] == "evolutions.v1.json"
    assert result["skill_path"] == "/tmp/demo-skill/SKILL.md"
    assert len(stream_ctx.chunks) == 1
    assert _FakeRebuildService.prepare_calls == [
        {
            "subject": {"kind": "skill", "name": "demo-skill"},
            "user_intent": "tighten docs",
            "record_ids": ["ev_1"],
            "kwargs": {"min_score": 0.5},
        }
    ]
    assert _FakeRebuildService.complete_rebuild_calls
    assert _FakeRebuildService.complete_rebuild_calls[0]["skill_md_path"] == "/tmp/demo-skill/SKILL.md"


@pytest.mark.asyncio
async def test_generate_evolution_merge_version_skips_complete_on_rewrite_failure(
    adapter, monkeypatch,
):
    _FakeRebuildService.prepare_calls = []
    _FakeRebuildService.complete_rebuild_calls = []
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )

    async def _failing_follow_up_stream(**_kwargs):
        yield AgentResponseChunk(
            request_id="req-fail-merge",
            channel_id="web",
            payload={"event_type": "chat.error", "error": "boom"},
            is_complete=False,
        )

    monkeypatch.setattr(adapter, "_iter_skill_creator_follow_up_stream", _failing_follow_up_stream)
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    stream_ctx = adapter.MergeVersionStreamContext(
        base_inputs={"query": "hello"},
        stream_request_id="req-fail-merge",
        channel_id="web",
        session_id="sess-fail-merge",
    )

    result = await adapter.generate_evolution_merge_version(
        skill_name="demo-skill",
        stream_ctx=stream_ctx,
    )

    assert result["ok"] is False
    assert "融合重写" in result["error"]
    assert _FakeRebuildService.prepare_calls
    assert _FakeRebuildService.complete_rebuild_calls == []


@pytest.mark.asyncio
async def test_handle_skills_evolution_rebuild_rpc(adapter, monkeypatch, tmp_path: Path):
    skill_md = tmp_path / "demo-skill" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# demo\n", encoding="utf-8")
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    adapter._registered_skill_dirs = [str(tmp_path)]  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "generate_evolution_merge_version",
        AsyncMock(
            return_value={
                "ok": True,
                "skill_name": "demo-skill",
                "skill_path": str(skill_md),
                "archive_path": "evolutions.v1.json",
                "new_version": "1.2.0",
                "cleared": True,
            }
        ),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    payload = await adapter.handle_skills_evolution_rebuild(
        {
            "name": "demo-skill",
            "skill_path": str(skill_md),
            "record_ids": ["ev_1", "ev_2"],
            "user_intent": "merge notes",
        }
    )

    assert payload == {
        "success": True,
        "name": "demo-skill",
        "skill_path": str(skill_md),
        "archive_path": "evolutions.v1.json",
        "new_version": "1.2.0",
        "cleared": True,
    }
    adapter.generate_evolution_merge_version.assert_awaited_once_with(
        skill_name="demo-skill",
        skill_path=str(skill_md.resolve()),
        record_ids=["ev_1", "ev_2"],
        user_intent="merge notes",
        min_score=0.5,
    )


@pytest.mark.asyncio
async def test_handle_skills_evolution_rebuild_rejects_path_traversal(adapter, monkeypatch, tmp_path: Path):
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    adapter._registered_skill_dirs = [str(tmp_path)]  # pylint: disable=protected-access
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )
    generate = AsyncMock()
    monkeypatch.setattr(adapter, "generate_evolution_merge_version", generate)

    with pytest.raises(ValueError, match="skill_path not in allowed directories"):
        await adapter.handle_skills_evolution_rebuild(
            {
                "name": "demo-skill",
                "skill_path": str(tmp_path / ".." / "evil.md"),
            }
        )
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_skills_evolution_rebuild_rpc_rejects_failure(adapter, monkeypatch):
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "generate_evolution_merge_version",
        AsyncMock(return_value={"ok": False, "error": "no experiences"}),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    with pytest.raises(ValueError, match="no experiences"):
        await adapter.handle_skills_evolution_rebuild({"name": "demo-skill"})


@pytest.mark.asyncio
async def test_generate_evolution_merge_version_warns_on_partial_finalize_failure(
    adapter, monkeypatch,
):
    """CR-003: rewrite ok + finalize fail must log partial-failure warning."""
    adapter._skill_evolution_rail = None  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )
    monkeypatch.setattr(adapter, "_execute_merge_version_rewrite", AsyncMock(return_value=True))
    monkeypatch.setattr(
        adapter,
        "_finalize_rebuild_followup",
        AsyncMock(return_value={"cleared": False, "error": "complete_rebuild failed"}),
    )

    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        result = await adapter.generate_evolution_merge_version(skill_name="demo-skill")
    finally:
        detach()

    assert result["ok"] is False
    assert "complete_rebuild failed" in result["error"]
    assert any(
        "merge version partial failure" in record.getMessage() and record.levelno == logging.WARNING
        for record in records
    )

@pytest.mark.asyncio
async def test_collect_evolution_summary_stashes_auto_rebuild_skills(adapter):
    summary = {
        "skills": [
            {"skill_name": "demo-skill", "records_count": 1},
            {"skill_name": "other-skill", "records_count": 2},
        ],
        "new_skills": [],
    }
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        auto_save=True,
        take_run_summary=lambda: summary,
    )

    text = adapter._collect_evolution_run_summary_text("req-1")  # pylint: disable=protected-access

    assert text
    assert adapter._pending_auto_rebuild_skills == ["demo-skill", "other-skill"]  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_collect_evolution_summary_skips_stash_when_auto_save_false(adapter):
    summary = {"skills": [{"skill_name": "demo-skill", "records_count": 1}], "new_skills": []}
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        auto_save=False,
        take_run_summary=lambda: summary,
    )

    adapter._collect_evolution_run_summary_text("req-2")  # pylint: disable=protected-access

    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_iter_auto_rebuild_followups_prepare_and_finalize(adapter, monkeypatch):
    _FakeRebuildService.prepare_calls = []
    _FakeRebuildService.complete_rebuild_calls = []
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )

    async def _fake_follow_up_stream(**_kwargs):
        yield AgentResponseChunk(
            request_id="req-auto",
            channel_id="web",
            payload={"event_type": "chat.delta", "content": "rewriting"},
            is_complete=False,
        )

    monkeypatch.setattr(adapter, "_iter_skill_creator_follow_up_stream", _fake_follow_up_stream)
    # auto_save gate still reads rail; store I/O uses disk EvolutionStore.
    adapter._skill_evolution_rail = SimpleNamespace(auto_save=True)  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    adapter._pending_auto_rebuild_skills = ["demo-skill"]  # pylint: disable=protected-access

    chunks = [
        chunk
        async for chunk in adapter._iter_auto_rebuild_followups(  # pylint: disable=protected-access
            base_inputs={"query": "hello"},
            stream_request_id="req-auto",
            channel_id="web",
            session_id="sess-auto",
            hitl_pending=False,
        )
    ]

    assert any(
        isinstance(c.payload, dict)
        and "自动生成新版本" in str(c.payload.get("content", ""))
        for c in chunks
    )
    assert _FakeRebuildService.prepare_calls
    assert _FakeRebuildService.complete_rebuild_calls == [
        {
            "skill_name": "demo-skill",
            "archive_path": "evolutions.v1.json",
            "records": [],
            "overflow_index": {},
        }
    ]
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_iter_auto_rebuild_followups_skips_on_hitl(adapter, monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(adapter, "generate_evolution_merge_version", generate)
    adapter._skill_evolution_rail = SimpleNamespace(auto_save=True, store=_FakeEvolutionStore())  # pylint: disable=protected-access
    adapter._pending_auto_rebuild_skills = ["demo-skill"]  # pylint: disable=protected-access

    chunks = [
        chunk
        async for chunk in adapter._iter_auto_rebuild_followups(  # pylint: disable=protected-access
            base_inputs={"query": "hello"},
            stream_request_id="req-hitl",
            channel_id="web",
            session_id="sess-hitl",
            hitl_pending=True,
        )
    ]

    assert chunks == []
    generate.assert_not_awaited()
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_iter_auto_rebuild_followups_skips_when_auto_save_false(adapter, monkeypatch):
    generate = AsyncMock()
    monkeypatch.setattr(adapter, "generate_evolution_merge_version", generate)
    adapter._skill_evolution_rail = SimpleNamespace(auto_save=False, store=_FakeEvolutionStore())  # pylint: disable=protected-access
    adapter._pending_auto_rebuild_skills = ["demo-skill"]  # pylint: disable=protected-access

    chunks = [
        chunk
        async for chunk in adapter._iter_auto_rebuild_followups(  # pylint: disable=protected-access
            base_inputs={"query": "hello"},
            stream_request_id="req-off",
            channel_id="web",
            session_id="sess-off",
            hitl_pending=False,
        )
    ]

    assert chunks == []
    generate.assert_not_awaited()
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.asyncio
async def test_iter_auto_rebuild_followups_skips_complete_on_followup_error(adapter, monkeypatch):
    _FakeRebuildService.prepare_calls = []
    _FakeRebuildService.complete_rebuild_calls = []
    _FakeRebuildService.next_context = {
        "skill_name": "demo-skill",
        "archive_path": "evolutions.v1.json",
        "records": [],
        "overflow_index": {},
    }
    monkeypatch.setattr(interface_deep_module, "ExperienceRebuildService", _FakeRebuildService)
    monkeypatch.setattr(
        interface_deep_module,
        "build_rebuild_command_prompt",
        lambda **_kwargs: "rebuild demo-skill",
    )

    async def _failing_follow_up_stream(**_kwargs):
        yield AgentResponseChunk(
            request_id="req-fail",
            channel_id="web",
            payload={"event_type": "chat.error", "error": "boom"},
            is_complete=False,
        )

    monkeypatch.setattr(adapter, "_iter_skill_creator_follow_up_stream", _failing_follow_up_stream)
    adapter._skill_evolution_rail = SimpleNamespace(auto_save=True)  # pylint: disable=protected-access
    _mock_disk_evolution_store(adapter, monkeypatch)
    adapter._pending_auto_rebuild_skills = ["demo-skill"]  # pylint: disable=protected-access

    _ = [
        chunk
        async for chunk in adapter._iter_auto_rebuild_followups(  # pylint: disable=protected-access
            base_inputs={"query": "hello"},
            stream_request_id="req-fail",
            channel_id="web",
            session_id="sess-fail",
            hitl_pending=False,
        )
    ]

    assert _FakeRebuildService.prepare_calls
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
    """When on_approve_new_skill returns None, dispatch is NOT called and result is False."""
    mock_rail = MagicMock()
    mock_rail.on_approve_new_skill = AsyncMock(return_value=None)
    adapter._skill_evolution_rail = mock_rail
    adapter._dispatch_skill_creator_follow_up = AsyncMock()

    result = await adapter._handle_skill_create_approval(
        "skill_create_abc12345",
        [{"selected_options": ["Create"]}],
    )

    assert result is False
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
    import asyncio

    mock_instance = MagicMock()
    adapter._instance = mock_instance
    adapter._current_session_id = lambda: "test-session"

    # Mock Runner.run_agent to verify it's called
    captured_inputs = []

    async def _fake_run_agent(*, agent, inputs, session=None):
        captured_inputs.append({"agent": agent, "inputs": inputs, "session": session})

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent",
        _fake_run_agent,
    )

    prompt = "**重要：你必须先向用户确认...**\n模拟 skill_creator_prompt"
    await adapter._dispatch_skill_creator_follow_up("skill_create_test", prompt)
    pending = list(adapter._pending_follow_ups)
    assert len(pending) == 1
    await asyncio.gather(*pending)
    await asyncio.sleep(0)

    assert len(captured_inputs) == 1
    assert captured_inputs[0]["agent"] is mock_instance
    assert captured_inputs[0]["inputs"]["query"] == prompt
    assert captured_inputs[0]["inputs"]["conversation_id"] == "test-session"
    assert captured_inputs[0]["session"] == "test-session"
    assert len(adapter._pending_follow_ups) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_skill_creator_follow_up_retains_task_against_gc(adapter, monkeypatch):
    """Pending follow-up tasks must stay referenced until done (not silently GC'd)."""
    import asyncio
    import gc

    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def _slow_run_agent(*, agent, inputs, session=None):
        started.set()
        await release.wait()
        finished.set()

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent",
        _slow_run_agent,
    )
    adapter._instance = MagicMock()
    adapter._current_session_id = lambda: "gc-session"

    await adapter._dispatch_skill_creator_follow_up("skill_create_gc", "prompt")
    await asyncio.wait_for(started.wait(), timeout=1.0)
    assert len(adapter._pending_follow_ups) == 1

    gc.collect()
    assert len(adapter._pending_follow_ups) == 1
    assert not finished.is_set()

    release.set()
    await asyncio.wait_for(finished.wait(), timeout=1.0)
    pending = list(adapter._pending_follow_ups)
    if pending:
        await asyncio.gather(*pending)
    # done_callback is scheduled via call_soon; yield so discard runs.
    await asyncio.sleep(0)
    assert len(adapter._pending_follow_ups) == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_skill_creator_follow_up_handles_runner_exception(adapter, monkeypatch):
    """_dispatch_skill_creator_follow_up should catch exceptions gracefully."""
    import asyncio

    async def _boom(*, agent, inputs, session=None):
        raise RuntimeError("runner failed")

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent",
        _boom,
    )
    adapter._instance = MagicMock()
    adapter._current_session_id = lambda: "test-session"

    try:
        await adapter._dispatch_skill_creator_follow_up("skill_create_test", "prompt")
        await asyncio.sleep(0)
    except Exception:
        pytest.fail("_dispatch_skill_creator_follow_up should not raise")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iter_skill_creator_follow_up_stream_yields_parsed_chunks(adapter, monkeypatch):
    """Normal streaming path should yield parsed AgentResponseChunk payloads."""

    async def _fake_stream(agent, inputs):
        yield SimpleNamespace(kind="delta", text="hello")
        yield None
        yield SimpleNamespace(kind="delta", text="world")

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent_streaming",
        _fake_stream,
    )
    adapter._instance = MagicMock()
    adapter._parse_stream_chunk_with_source = MagicMock(
        side_effect=lambda chunk: None
        if chunk is None
        else {"event_type": "chat.delta", "content": chunk.text}
    )

    chunks = [
        c
        async for c in adapter._iter_skill_creator_follow_up_stream(
            base_inputs={"query": "orig"},
            prompt="create skill",
            skill_create_request_id="skill_create_1",
            stream_request_id="req-1",
            channel_id="ch-1",
            session_id="sess-1",
        )
    ]

    assert len(chunks) == 2
    assert chunks[0].request_id == "req-1"
    assert chunks[0].channel_id == "ch-1"
    assert chunks[0].payload == {"event_type": "chat.delta", "content": "hello"}
    assert chunks[1].payload == {"event_type": "chat.delta", "content": "world"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iter_skill_creator_follow_up_stream_empty(adapter, monkeypatch):
    """Empty stream should yield no chunks."""

    async def _empty_stream(agent, inputs):
        if False:  # pragma: no cover - keep async generator shape
            yield None

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent_streaming",
        _empty_stream,
    )
    adapter._instance = MagicMock()
    adapter._parse_stream_chunk_with_source = MagicMock(return_value={"event_type": "chat.delta"})

    chunks = [
        c
        async for c in adapter._iter_skill_creator_follow_up_stream(
            base_inputs={},
            prompt="create skill",
            skill_create_request_id="skill_create_empty",
            stream_request_id="req-empty",
            channel_id="ch",
            session_id="sess",
        )
    ]
    assert chunks == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_iter_skill_creator_follow_up_stream_error(adapter, monkeypatch):
    """Streaming failures should yield a chat.error chunk instead of raising."""

    async def _failing_stream(agent, inputs):
        raise RuntimeError("stream boom")
        if False:  # pragma: no cover
            yield None

    monkeypatch.setattr(
        interface_deep_module.Runner,
        "run_agent_streaming",
        _failing_stream,
    )
    adapter._instance = MagicMock()

    chunks = [
        c
        async for c in adapter._iter_skill_creator_follow_up_stream(
            base_inputs={},
            prompt="create skill",
            skill_create_request_id="skill_create_err",
            stream_request_id="req-err",
            channel_id="ch-err",
            session_id="sess-err",
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].payload["event_type"] == "chat.error"
    assert "stream boom" in chunks[0].payload["error"]


@pytest.mark.unit
def test_current_session_id_logs_debug_on_exception(adapter):
    """_current_session_id should debug-log when card access raises."""
    records, detach = _attach_capture_handler(interface_deep_module.logger)
    try:
        broken = MagicMock()
        type(broken).card = property(lambda self: (_ for _ in ()).throw(RuntimeError("card gone")))
        adapter._instance = broken
        assert adapter._current_session_id() == ""
        assert any(
            "failed to resolve current session_id" in r.getMessage() for r in records
        )
    finally:
        detach()


# =============================================================================
# /evolve message collection tests
# =============================================================================


def _setup_collect_messages_mocks(
    adapter: DeepAdapterHarness,
    *,
    buffer_messages: list[dict[str, str]],
    qa_caches: dict[str, list[dict[str, str]]] | None = None,
    qa_ids: list[str] | None = None,
) -> MagicMock:
    """Wire context_engine mocks for _collect_messages_for_evolve."""
    qa_caches = qa_caches or {}
    history = MagicMock()
    history.get.side_effect = lambda qa_id: qa_caches.get(qa_id)
    history.recent_qa_ids.return_value = qa_ids if qa_ids is not None else list(qa_caches.keys())

    context = MagicMock()
    context.get_messages.return_value = buffer_messages
    context.get_session_ref.return_value = None
    context.context_id.return_value = "ctx-1"

    context_engine = MagicMock()
    context_engine.get_context.return_value = context
    context_engine.get_history_qa_buffer.return_value = history

    react_agent = MagicMock()
    react_agent.context_engine = context_engine

    instance = MagicMock()
    instance.react_agent = react_agent
    adapter._instance = instance
    return history


@pytest.mark.unit
def test_collect_messages_for_evolve_prepends_qa_history_when_buffer_nonempty(adapter):
    """QA history must be prepended even when the current buffer is non-empty."""
    buffer = [{"role": "user", "content": "current turn"}]
    qa_caches = {"qa-1": [{"role": "user", "content": "old turn"}]}
    _setup_collect_messages_mocks(
        adapter,
        buffer_messages=buffer,
        qa_caches=qa_caches,
        qa_ids=["qa-1"],
    )

    result = adapter._collect_messages_for_evolve("sess-1")  # pylint: disable=protected-access

    assert len(result) == 2
    assert result[0]["content"] == "old turn"
    assert result[1]["content"] == "current turn"


@pytest.mark.unit
def test_collect_messages_for_evolve_buffer_only_when_no_qa_history(adapter):
    """When QA history is empty, only buffer messages are returned."""
    buffer = [{"role": "user", "content": "only buffer"}]
    _setup_collect_messages_mocks(adapter, buffer_messages=buffer, qa_caches={}, qa_ids=[])

    result = adapter._collect_messages_for_evolve("sess-2")  # pylint: disable=protected-access

    assert len(result) == 1
    assert result[0]["content"] == "only buffer"


@pytest.mark.unit
def test_collect_messages_for_evolve_dedups_overlapping_messages(adapter):
    """Overlapping QA history and buffer messages should be deduplicated."""
    duplicate = {"role": "user", "content": "same message"}
    buffer = [duplicate]
    qa_caches = {"qa-1": [dict(duplicate)]}
    _setup_collect_messages_mocks(
        adapter,
        buffer_messages=buffer,
        qa_caches=qa_caches,
        qa_ids=["qa-1"],
    )

    result = adapter._collect_messages_for_evolve("sess-3")  # pylint: disable=protected-access

    assert len(result) == 1
    assert result[0]["content"] == "same message"
