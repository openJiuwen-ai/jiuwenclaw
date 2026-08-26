"""Code profile integration with the parent-owned permission composition."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)

from jiuwenswarm.agents.harness.common.rails.permissions.auto_permission_rail import (
    AutoPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.code.rails.code_agent_mode_rail import CodeAgentModeRail
from jiuwenswarm.agents.harness.code.rails.code_plan_pre_permission_guard_rail import (
    CodePlanPrePermissionGuardRail,
)
from jiuwenswarm.agents.harness.code.rails.code_task_planning_rail import (
    CodeTaskPlanningRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_context_rail import (
    RootContextRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue_rail import (
    RootPermissionCompletionRail,
    RootPermissionQueueRail,
)
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)
from jiuwenswarm.server.runtime.agent_adapter import interface_code, interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_code import (
    JiuwenSwarmCodeAdapter,
)


_CODE_PROFILE_BUILDERS = """
_build_runtime_prompt_rail _build_response_prompt_rail _build_skill_retrieval_prompt_rail
_build_security_rail _build_lsp_rail_via_config _build_project_memory_rail
_build_filesystem_rail _build_coding_memory_rail _build_memory_forbidden_rail
_build_agent_mode_rail _build_structured_ask_user_rail _build_confirm_interrupt_rail
_build_context_processor_rail _build_code_task_planning_rail _build_code_agent_rail
_build_plan_approval_rail
""".split()


def _adapter(monkeypatch: pytest.MonkeyPatch) -> JiuwenSwarmCodeAdapter:
    adapter = JiuwenSwarmCodeAdapter()
    adapter._model = MagicMock()
    adapter._sys_operation = MagicMock()
    adapter._config_cache = {}
    adapter._config_base_cache = {}
    for name in _CODE_PROFILE_BUILDERS:
        monkeypatch.setattr(adapter, name, lambda *args, **kwargs: object())
    monkeypatch.setattr(
        interface_deep, "load_hooks_config", lambda config: SimpleNamespace(events=())
    )
    return adapter


def _manual_permission() -> PermissionInterruptRail:
    return object.__new__(PermissionInterruptRail)


def _auto_permission(
    adapter: JiuwenSwarmCodeAdapter,
) -> AutoPermissionInterruptRail:
    rail = object.__new__(AutoPermissionInterruptRail)
    rail.sys_operation = adapter._sys_operation
    rail.priority = PermissionInterruptRail.priority
    return rail


def test_code_auto_capability_uses_single_agent_scope() -> None:
    adapter = JiuwenSwarmCodeAdapter()

    assert adapter._auto_permission_enabled_for_config(
        {"enabled": True, "mode": "auto"},
        composition_scope="single_agent",
    )
    assert not adapter._auto_permission_enabled_for_config(
        {"enabled": True, "mode": "auto"},
        composition_scope="team_member",
    )
    adapter._session_instance_sub_mode = "auto_harness"
    assert not adapter._auto_permission_enabled_for_config(
        {"enabled": True, "mode": "auto"},
        composition_scope="single_agent",
    )
    assert "_build_agent_rails" not in JiuwenSwarmCodeAdapter.__dict__


def test_unknown_composition_scope_fails_before_profile_factories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch)
    build_profile = MagicMock()
    monkeypatch.setattr(adapter, "_build_profile_rail_specs", build_profile)

    with pytest.raises(RuntimeError, match="agent_composition_scope_invalid"):
        adapter._build_agent_rails(
            {},
            {"permissions": {"enabled": True, "mode": "auto"}},
            mode="code",
            composition_scope="unknown",
        )

    build_profile.assert_not_called()


@pytest.mark.parametrize("mode", ["code.normal", "code.plan"])
def test_code_profile_uses_parent_required_auto_composition(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    adapter = _adapter(monkeypatch)
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        interface_deep,
        "build_permission_rail",
        lambda **kwargs: calls.append(kwargs) or _auto_permission(adapter),
    )

    rails = adapter._build_agent_rails(
        {},
        {"permissions": {"enabled": True, "mode": "auto"}},
        mode=mode,
        composition_scope="single_agent",
    )

    assert sum(isinstance(rail, RootPermissionQueueRail) for rail in rails) == 1
    assert sum(isinstance(rail, RootContextRail) for rail in rails) == 1
    assert sum(isinstance(rail, RootPermissionCompletionRail) for rail in rails) == 1
    assert sum(isinstance(rail, JiuSwarmStreamEventRail) for rail in rails) == 1
    assert sum(isinstance(rail, AutoPermissionInterruptRail) for rail in rails) == 1
    assert sum(isinstance(rail, CodePlanPrePermissionGuardRail) for rail in rails) == 1
    assert CodePlanPrePermissionGuardRail.priority > PermissionInterruptRail.priority
    assert PermissionInterruptRail.priority == CodeTaskPlanningRail.priority
    assert CodeAgentModeRail.priority < PermissionInterruptRail.priority
    assert adapter._enable_auto_permission is True
    assert calls[0]["enable_auto_permission"] is True
    assert calls[0]["sys_operation"] is adapter._sys_operation


@pytest.mark.parametrize("scope", ["team_root", "team_member", "auto_harness"])
def test_excluded_scope_keeps_profile_rails_without_auto_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    adapter = _adapter(monkeypatch)
    adapter._sys_operation = None
    monkeypatch.setattr(
        interface_deep, "build_permission_rail", lambda **kwargs: _manual_permission()
    )

    rails = adapter._build_agent_rails(
        {},
        {"permissions": {"enabled": True, "mode": "auto"}},
        mode="code",
        composition_scope=scope,
    )

    assert not any(isinstance(rail, AutoPermissionInterruptRail) for rail in rails)
    assert not any(isinstance(rail, RootPermissionQueueRail) for rail in rails)
    assert not any(isinstance(rail, RootContextRail) for rail in rails)
    assert not any(isinstance(rail, RootPermissionCompletionRail) for rail in rails)
    assert sum(isinstance(rail, JiuSwarmStreamEventRail) for rail in rails) == 1
    assert adapter._enable_auto_permission is False


@pytest.mark.asyncio
async def test_code_assigns_owner_facts_before_parent_composition(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    adapter = JiuwenSwarmCodeAdapter()
    adapter.mark_as_session_scoped("code-session")
    provider = object()
    agent = SimpleNamespace(_registered_rails=[])
    agent.ensure_initialized = AsyncMock()
    monkeypatch.setattr(adapter, "set_checkpoint", AsyncMock())
    monkeypatch.setattr(interface_code, "get_config", lambda: {"react": {}})
    monkeypatch.setattr(adapter, "_create_model", lambda config: MagicMock())
    monkeypatch.setattr(adapter, "_get_tool_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_create_sys_operation", lambda: provider)
    monkeypatch.setattr(adapter, "_prepare_browser_runtime_security", MagicMock())

    def build_rails(*args, **kwargs):
        assert adapter._sys_operation is provider
        assert adapter._permission_workspace_root == tmp_path.resolve()
        assert kwargs["composition_scope"] == "single_agent"
        return []

    monkeypatch.setattr(adapter, "_build_agent_rails", build_rails)
    monkeypatch.setattr(
        adapter, "_build_configured_subagents", lambda *args: (None, False)
    )
    monkeypatch.setattr(interface_code, "create_deep_agent", lambda **kwargs: agent)
    monkeypatch.setattr(adapter, "_seed_runtime_cwd", MagicMock())
    monkeypatch.setattr(adapter, "_ensure_cron_tools_registered", MagicMock())
    monkeypatch.setattr(adapter, "_register_mcp_servers_from_config", AsyncMock())
    monkeypatch.setattr(adapter, "load_user_rails", AsyncMock())

    await adapter.create_instance(
        {"project_dir": str(tmp_path)},
        mode="code",
        sub_mode="normal",
    )

    assert adapter._sys_operation is provider
    adapter._prepare_browser_runtime_security.assert_called_once()
