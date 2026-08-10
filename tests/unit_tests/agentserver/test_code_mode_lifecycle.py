"""Tests for Code-mode rail registration and memory reload lifecycle."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.core.single_agent import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.rails.coding_memory_rail import CodingMemoryRail
from openjiuwen.harness.rails.lsp_rail import LspRail
from openjiuwen.harness.rails.task_planning_rail import TaskPlanningRail

from jiuwenclaw.agentserver.deep_agent.interface_deep import (
    JiuWenClawDeepAdapter,
    _AgentInitContext,
    _RuntimeConfigParams,
)
from jiuwenclaw.agentserver.deep_agent.rails.project_memory_rail import (
    ProjectMemoryRail,
)
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


_RAIL_BASES = {
    "CodingMemoryRail": CodingMemoryRail,
    "LspRail": LspRail,
    "ProjectMemoryRail": ProjectMemoryRail,
}


def _rail(name: str, base_name: str | None = None):
    base = _RAIL_BASES.get(base_name or name)
    if base is None:
        return type(name, (), {})()
    return object.__new__(type(name, (base,), {}))


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
    adapter._task_planning_rail = None
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
    task_planning = TaskPlanningRail()
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[project, coding, task_planning, mode]),
    )

    await adapter._register_code_mode_rails()

    assert adapter._code_mode_rails_active is True
    assert adapter._project_memory_rail is project
    assert adapter._coding_memory_rail is coding
    assert adapter._task_planning_rail is task_planning
    assert adapter._instance.register_rail.await_count == 5


@pytest.mark.asyncio
async def test_rail_identification_supports_renamed_subclasses(monkeypatch) -> None:
    adapter = _adapter()
    project = _rail("RenamedProjectMemoryRail", "ProjectMemoryRail")
    coding = _rail("RenamedCodingMemoryRail", "CodingMemoryRail")
    lsp = _rail("RenamedLspRail", "LspRail")
    mode = _rail("CodeAgentModeRail")
    adapter._build_lsp_rail = MagicMock(return_value=lsp)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_code_mode_extra_rails",
        MagicMock(return_value=[project, coding, mode]),
    )

    await adapter._register_code_mode_rails()

    assert adapter._project_memory_rail is project
    assert adapter._coding_memory_rail is coding
    assert adapter._lsp_rail is lsp
    assert adapter._code_mode_rails == [project, coding, mode]


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
async def test_code_unregistration_failure_rolls_back_previous_state() -> None:
    adapter = _adapter()
    project = _rail("ProjectMemoryRail")
    coding = _rail("CodingMemoryRail")
    mode = _rail("CodeAgentModeRail")
    adapter._project_memory_rail = project
    adapter._coding_memory_rail = coding
    adapter._code_mode_rails = [project, coding, mode]
    adapter._code_mode_rails_active = True

    async def unregister(rail) -> None:
        if rail is mode:
            raise RuntimeError("unregister failed")

    adapter._instance.unregister_rail = AsyncMock(side_effect=unregister)

    result = await adapter._unregister_code_mode_rails()

    assert result is False
    assert adapter._code_mode_rails == [project, coding, mode]
    assert adapter._code_mode_rails_active is True
    assert adapter._project_memory_rail is project
    assert adapter._coding_memory_rail is coding
    assert adapter._instance.register_rail.await_args_list[0].args == (coding,)
    assert adapter._instance.register_rail.await_args_list[1].args == (project,)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["code.plan", "code.normal"])
async def test_code_submode_keeps_code_rails_registered(mode: str) -> None:
    adapter = _adapter()
    adapter._code_mode_rails_active = True
    adapter._register_code_mode_rails = AsyncMock()
    adapter._unregister_code_mode_rails = AsyncMock()
    adapter._update_plan_mode_rails = AsyncMock()
    adapter._update_agent_mode_rails = AsyncMock()

    await adapter._update_rails_for_mode(mode)

    adapter._register_code_mode_rails.assert_awaited_once()
    adapter._unregister_code_mode_rails.assert_not_awaited()
    adapter._update_plan_mode_rails.assert_not_awaited()
    adapter._update_agent_mode_rails.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_submode_is_forwarded_to_code_mode_rail() -> None:
    adapter = _adapter()
    code_mode_rail = MagicMock()
    adapter._code_mode_rails = [code_mode_rail]
    adapter._code_mode_rails_active = True
    adapter._register_code_mode_rails = AsyncMock()

    await adapter._update_rails_for_mode("code.plan", session_id="session-1")

    code_mode_rail.set_requested_mode.assert_called_once_with(
        "code.plan",
        session_id="session-1",
    )


@pytest.mark.asyncio
async def test_code_resume_preserves_dynamic_session_submode() -> None:
    adapter = _adapter()
    code_mode_rail = MagicMock()
    adapter._code_mode_rails = [code_mode_rail]
    adapter._code_mode_rails_active = True
    adapter._register_code_mode_rails = AsyncMock()

    await adapter._update_rails_for_mode(
        "code.normal",
        session_id="session-1",
        sync_code_submode=False,
    )

    adapter._register_code_mode_rails.assert_awaited_once()
    code_mode_rail.set_requested_mode.assert_not_called()


@pytest.mark.parametrize(
    ("req_method", "params"),
    [
        (ReqMethod.CHAT_RESUME, {"mode": "code.normal", "query": ""}),
        (
            ReqMethod.CHAT_SEND,
            {
                "mode": "code.normal",
                "query": "",
                "answers": [{"selected_options": ["always_allow"]}],
            },
        ),
    ],
)
def test_interrupt_resume_does_not_replay_code_entry_mode(
    req_method: ReqMethod,
    params: dict,
) -> None:
    request = AgentRequest(
        request_id="resume-1",
        session_id="session-1",
        req_method=req_method,
        params=params,
    )

    runtime = _RuntimeConfigParams.from_agent_request(request, "code.normal")

    assert runtime.sync_code_submode is False


def test_new_chat_request_applies_code_entry_mode() -> None:
    request = AgentRequest(
        request_id="send-1",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"mode": "code.normal", "query": "hello"},
    )

    runtime = _RuntimeConfigParams.from_agent_request(request, "code.normal")

    assert runtime.sync_code_submode is True


@pytest.mark.asyncio
async def test_incomplete_code_cleanup_does_not_enter_agent_mode() -> None:
    adapter = _adapter()
    adapter._unregister_code_mode_rails = AsyncMock(return_value=False)
    adapter._update_agent_mode_rails = AsyncMock()
    adapter._last_runtime_mode = "agent.fast"

    with pytest.raises(RuntimeError, match="Mode transition aborted"):
        await adapter._update_rails_for_mode("agent.fast")

    adapter._update_agent_mode_rails.assert_not_awaited()
    assert adapter._last_runtime_mode == "code"


@pytest.mark.asyncio
async def test_failed_code_cleanup_aborts_downstream_runtime_updates() -> None:
    adapter = _adapter()
    adapter._runtime_prompt_rail = None
    adapter._last_runtime_mode = "agent.fast"
    adapter._unregister_code_mode_rails = AsyncMock(return_value=False)
    adapter._update_agent_mode_rails = AsyncMock()
    adapter._update_tools_for_mode = AsyncMock()
    adapter._update_session_tools = AsyncMock()
    adapter._refresh_acp_runtime_tools = MagicMock()
    adapter._update_prompt_for_mode = MagicMock()

    with pytest.raises(RuntimeError, match="Mode transition aborted"):
        await adapter._update_runtime_config(
            _RuntimeConfigParams(
                session_id="code-session",
                mode="agent.fast",
                request_id="request-1",
                channel_id="web",
            )
        )

    adapter._update_agent_mode_rails.assert_not_awaited()
    adapter._update_tools_for_mode.assert_not_awaited()
    adapter._update_session_tools.assert_not_awaited()
    adapter._refresh_acp_runtime_tools.assert_not_called()
    adapter._update_prompt_for_mode.assert_not_called()
    assert adapter._last_runtime_mode == "code"


def test_task_planning_rail_exposes_todo_tools_on_real_deep_agent(tmp_path: Path) -> None:
    agent = DeepAgent(card=AgentCard(name="code-test", id="code-test"))
    workspace = MagicMock()
    workspace.get_node_path.return_value = str(tmp_path / "todo")
    agent._deep_config = SimpleNamespace(
        sys_operation=MagicMock(),
        workspace=workspace,
    )
    agent.system_prompt_builder = SimpleNamespace(
        language="cn",
        add_section=MagicMock(),
        remove_section=MagicMock(),
    )
    rail = TaskPlanningRail()

    rail.init(agent)
    names = {getattr(tool, "name", "") for tool in agent.ability_manager.list()}

    try:
        assert {"todo_create", "todo_list", "todo_modify"} <= names
    finally:
        rail.uninit(agent)


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


def _agent_init_adapter() -> JiuWenClawDeepAdapter:
    """Adapter double for `_init_agent_instance_sync`, stubbing every collaborator
    except the mode-based create_code_agent / create_deep_agent selection under test.
    """
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._sync_registered_skill_dirs_snapshot = MagicMock()
    adapter._build_agent_rails = MagicMock(return_value=[])
    adapter._create_sys_operation = MagicMock(return_value=MagicMock())
    adapter._sandbox_config_fingerprint = MagicMock(return_value="fp")
    adapter._build_configured_subagents = MagicMock(return_value=[])
    adapter._resolve_prompt_language = MagicMock(return_value="cn")
    adapter._is_acp_tool_profile = MagicMock(return_value=False)
    adapter._resolve_prompt_channel = MagicMock(return_value="default")
    adapter._resolve_runtime_language = MagicMock(return_value="cn")
    adapter._instance_overrides = {}
    adapter._workspace_dir = "."
    adapter._vision_model_config = None
    adapter._audio_model_config = None
    return adapter


@pytest.mark.parametrize("mode", ["code.plan", "code.normal", "code"])
def test_code_submode_cold_start_selects_code_agent(mode: str, monkeypatch) -> None:
    """CR-001 regression: code.plan/code.normal must instantiate create_code_agent,
    not create_deep_agent, even though rails were already normalized to 'code'.
    """
    adapter = _agent_init_adapter()
    code_agent_result = object()
    create_code_agent_mock = MagicMock(return_value=code_agent_result)
    create_deep_agent_mock = MagicMock()
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.create_code_agent",
        create_code_agent_mock,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.create_deep_agent",
        create_deep_agent_mock,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_identity_prompt",
        MagicMock(return_value="prompt"),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep._agent_ras_kwargs_from_config",
        MagicMock(return_value={}),
    )
    ctx = _AgentInitContext(
        config={},
        config_base={},
        mode=mode,
        model=MagicMock(),
        agent_card=MagicMock(),
        tool_cards=[],
    )

    adapter._init_agent_instance_sync(ctx)

    create_code_agent_mock.assert_called_once()
    create_deep_agent_mock.assert_not_called()
    assert adapter._instance is code_agent_result


def test_non_code_mode_cold_start_selects_deep_agent(monkeypatch) -> None:
    adapter = _agent_init_adapter()
    deep_agent_result = object()
    create_code_agent_mock = MagicMock()
    create_deep_agent_mock = MagicMock(return_value=deep_agent_result)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.create_code_agent",
        create_code_agent_mock,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.create_deep_agent",
        create_deep_agent_mock,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep.build_identity_prompt",
        MagicMock(return_value="prompt"),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interface_deep._agent_ras_kwargs_from_config",
        MagicMock(return_value={}),
    )
    ctx = _AgentInitContext(
        config={},
        config_base={},
        mode="agent.plan",
        model=MagicMock(),
        agent_card=MagicMock(),
        tool_cards=[],
    )

    adapter._init_agent_instance_sync(ctx)

    create_deep_agent_mock.assert_called_once()
    create_code_agent_mock.assert_not_called()
    assert adapter._instance is deep_agent_result


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
