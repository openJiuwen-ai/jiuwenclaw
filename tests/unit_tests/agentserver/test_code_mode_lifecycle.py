"""Tests for Code-mode rail registration and memory reload lifecycle."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import (
    JiuWenClawDeepAdapter,
)


def _rail(name: str):
    return type(name, (), {})()


def _adapter() -> JiuWenClawDeepAdapter:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._instance = MagicMock()
    adapter._instance.register_rail = AsyncMock()
    adapter._instance.unregister_rail = AsyncMock()
    adapter._code_mode_rails = []
    adapter._code_mode_rails_active = False
    adapter._lsp_rail = None
    adapter._project_memory_rail = None
    adapter._coding_memory_rail = None
    adapter._instance_overrides = {}
    adapter._workspace_dir = "."
    adapter._resolve_runtime_language = MagicMock(return_value="cn")
    adapter._build_lsp_rail = MagicMock(return_value=_rail("LspRail"))
    return adapter


@pytest.mark.asyncio
async def test_switching_to_code_registers_code_rails(monkeypatch) -> None:
    adapter = _adapter()
    project = _rail("ProjectMemoryRail")
    coding = _rail("CodingMemoryRail")
    mode = _rail("CodeAgentModeRail")
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[project, coding, mode]),
    )

    await adapter._register_code_mode_rails()

    assert adapter._code_mode_rails_active is True
    assert adapter._project_memory_rail is project
    assert adapter._coding_memory_rail is coding
    assert adapter._instance.register_rail.await_count == 4


@pytest.mark.asyncio
async def test_leaving_code_unregisters_code_rails() -> None:
    adapter = _adapter()
    project = _rail("ProjectMemoryRail")
    coding = _rail("CodingMemoryRail")
    mode = _rail("CodeAgentModeRail")
    adapter._project_memory_rail = project
    adapter._coding_memory_rail = coding
    adapter._code_mode_rails = [project, coding, mode]
    adapter._lsp_rail = _rail("LspRail")
    adapter._code_mode_rails_active = True

    await adapter._unregister_code_mode_rails()

    assert adapter._code_mode_rails_active is False
    assert adapter._code_mode_rails == []
    assert adapter._lsp_rail is None
    assert adapter._instance.unregister_rail.await_count == 4


@pytest.mark.asyncio
async def test_memory_reload_removes_disabled_memory_rails(monkeypatch) -> None:
    adapter = _adapter()
    project = _rail("ProjectMemoryRail")
    coding = _rail("CodingMemoryRail")
    mode = _rail("CodeAgentModeRail")
    adapter._project_memory_rail = project
    adapter._coding_memory_rail = coding
    adapter._code_mode_rails = [project, coding, mode]
    adapter._code_mode_rails_active = True
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[mode]),
    )

    await adapter._reload_code_mode_memory_rails({})

    assert adapter._project_memory_rail is None
    assert adapter._coding_memory_rail is None
    assert adapter._code_mode_rails == [mode]
    assert adapter._instance.unregister_rail.await_count == 2


@pytest.mark.asyncio
async def test_memory_reload_reenables_memory_rails_without_duplicate_plan_rails(
    monkeypatch,
) -> None:
    adapter = _adapter()
    mode = _rail("CodeAgentModeRail")
    project = _rail("ProjectMemoryRail")
    coding = _rail("CodingMemoryRail")
    adapter._code_mode_rails = [mode]
    adapter._code_mode_rails_active = True
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[project, coding, mode]),
    )

    await adapter._reload_code_mode_memory_rails({})

    assert adapter._project_memory_rail is project
    assert adapter._coding_memory_rail is coding
    assert adapter._code_mode_rails == [mode, project, coding]
    assert adapter._instance.register_rail.await_count == 2


@pytest.mark.asyncio
async def test_memory_reload_unregister_failure_retains_old_state(monkeypatch) -> None:
    adapter = _adapter()
    old_project = _rail("ProjectMemoryRail")
    mode = _rail("CodeAgentModeRail")
    adapter._project_memory_rail = old_project
    adapter._code_mode_rails = [old_project, mode]
    adapter._code_mode_rails_active = True
    adapter._instance.unregister_rail.side_effect = RuntimeError("unregister failed")
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[mode]),
    )

    await adapter._reload_code_mode_memory_rails({})

    assert adapter._project_memory_rail is old_project
    assert adapter._code_mode_rails == [old_project, mode]
    adapter._instance.register_rail.assert_not_awaited()


@pytest.mark.asyncio
async def test_memory_reload_register_failure_rolls_back_old_state(monkeypatch) -> None:
    adapter = _adapter()
    old_project = _rail("ProjectMemoryRail")
    new_project = _rail("ProjectMemoryRail")
    mode = _rail("CodeAgentModeRail")
    adapter._project_memory_rail = old_project
    adapter._code_mode_rails = [old_project, mode]
    adapter._code_mode_rails_active = True
    adapter._instance.register_rail.side_effect = [
        RuntimeError("register failed"),
        None,
    ]
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[new_project, mode]),
    )

    await adapter._reload_code_mode_memory_rails({})

    assert adapter._project_memory_rail is old_project
    assert adapter._code_mode_rails == [old_project, mode]
    assert adapter._instance.register_rail.await_count == 2


@pytest.mark.asyncio
async def test_memory_reload_workspace_switch_unloads_old_and_registers_new(
    tmp_path,
    monkeypatch,
) -> None:
    adapter = _adapter()
    old_workspace = tmp_path / "old"
    new_workspace = tmp_path / "new"
    old_coding = _rail("CodingMemoryRail")
    old_coding._coding_memory_dir = str(old_workspace / "coding_memory")
    new_coding = _rail("CodingMemoryRail")
    new_coding._coding_memory_dir = str(new_workspace / "coding_memory")
    mode = _rail("CodeAgentModeRail")
    adapter._coding_memory_rail = old_coding
    adapter._code_mode_rails = [old_coding, mode]
    adapter._code_mode_rails_active = True
    adapter._workspace_dir = str(new_workspace)

    def build_desired(adapter_arg, *_args, **_kwargs):
        adapter_arg._coding_memory_rail = new_coding
        return [new_coding, mode]

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        build_desired,
    )

    await adapter._reload_code_mode_memory_rails({})

    assert adapter._instance.unregister_rail.await_args_list[0].args == (old_coding,)
    assert adapter._instance.register_rail.await_args_list[0].args == (new_coding,)
    assert adapter._coding_memory_rail is new_coding
    assert adapter._code_mode_rails == [mode, new_coding]


@pytest.mark.asyncio
async def test_non_code_mode_update_does_not_register_code_rails() -> None:
    adapter = _adapter()
    adapter._code_mode_rails_active = False
    adapter._update_plan_mode_rails = AsyncMock()

    await adapter._update_rails_for_mode("agent.plan")

    adapter._update_plan_mode_rails.assert_awaited_once()
    adapter._instance.register_rail.assert_not_awaited()


def test_coding_memory_builder_caches_a_valid_embedding_rail(
    tmp_path,
    monkeypatch,
) -> None:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._coding_memory_rail = None
    adapter._workspace_dir = str(tmp_path)
    shared = object()
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.CodingMemoryRail",
        lambda **_kwargs: shared,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.EmbeddingConfig",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        lambda: {"preferred_language": "en"},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.memory.config.get_embed_config",
        lambda: {"api_key": "key", "base_url": "url", "model": "model"},
    )

    first = adapter._build_coding_memory_rail()
    second = adapter._build_coding_memory_rail()

    assert first is shared
    assert second is first


def test_coding_memory_builder_skips_missing_embedding_config(monkeypatch) -> None:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._coding_memory_rail = None
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.memory.config.get_embed_config",
        dict,
    )

    assert adapter._build_coding_memory_rail() is None


def test_coding_memory_builder_recreates_after_old_workspace_cache(
    tmp_path,
    monkeypatch,
) -> None:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    old = _rail("CodingMemoryRail")
    old._coding_memory_dir = str(tmp_path / "old" / "coding_memory")
    adapter._coding_memory_rail = old
    adapter._workspace_dir = str(tmp_path / "new")
    new = _rail("CodingMemoryRail")
    new._coding_memory_dir = str(tmp_path / "new" / "coding_memory")
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.CodingMemoryRail",
        lambda **_kwargs: new,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.EmbeddingConfig",
        lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.get_config",
        lambda: {"preferred_language": "en"},
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.memory.config.get_embed_config",
        lambda: {"api_key": "key", "base_url": "url", "model": "model"},
    )

    assert adapter._build_coding_memory_rail() is new
    assert adapter._coding_memory_rail is new


def test_configured_code_agent_reuses_main_coding_memory(monkeypatch) -> None:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    shared = object()
    adapter._coding_memory_rail = shared
    adapter._code_mode_rails_active = True
    adapter._code_mode_workspace = str(Path.cwd())
    adapter._workspace_dir = "."
    adapter._resolve_runtime_language = lambda: "en"
    adapter._browser_runtime_enabled = lambda: False
    captured = {}

    def capture_code_agent(_model, **kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_agent_config",
        capture_code_agent,
    )
    result = adapter._build_configured_subagents(
        None,
        {"subagents": {"code_agent": {"enabled": True}}},
        {"modes": {"code": {"memory": {"enabled": True}}}},
    )

    assert result
    assert captured["rails"][1] is shared


def test_code_assembly_keeps_base_rails_and_excludes_code_rails_from_plan(
    monkeypatch,
) -> None:
    adapter = JiuWenClawDeepAdapter(workspace_dir=".")
    config = {"modes": {"code": {"memory": {"enabled": False}}}}
    adapter._coding_memory_rail = _rail("CodingMemoryRail")
    adapter._project_memory_rail = _rail("ProjectMemoryRail")

    code_names = {
        type(rail).__name__
        for rail in adapter._build_agent_rails(
            {"agent_name": "test"},
            config,
            mode="code",
        )
    }
    assert adapter._coding_memory_rail is None
    assert adapter._project_memory_rail is None
    plan_names = {
        type(rail).__name__
        for rail in adapter._build_agent_rails(
            {"agent_name": "test"},
            config,
            mode="agent.plan",
        )
    }

    assert {"FileSystemRail", "PermissionInterruptRail", "LspRail"} <= code_names
    assert {
        "CodeAgentModeRail",
        "CodeConfirmInterruptRail",
        "PlanApprovalInterruptRail",
    } <= code_names
    new_coding = _rail("CodingMemoryRail")
    monkeypatch.setattr(adapter, "_build_coding_memory_rail", lambda: new_coding)
    adapter._build_agent_rails(
        {"agent_name": "test"},
        {"modes": {"code": {"memory": {"enabled": True}}},
         "preferred_language": "en"},
        mode="code",
    )
    assert adapter._coding_memory_rail is new_coding
    assert not (
        {
            "CodeAgentModeRail",
            "CodeConfirmInterruptRail",
            "PlanApprovalInterruptRail",
        }
        & plan_names
    )
