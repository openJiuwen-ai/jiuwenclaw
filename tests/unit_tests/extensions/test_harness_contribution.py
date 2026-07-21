# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import sys
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from openjiuwen.agent_teams.harness.manifest import get_catalog, resolve_factory
from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    RailSpec,
    register_rail_provider,
    register_tool_provider,
)
from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness.workspace.workspace import Workspace
from pydantic import BaseModel

from jiuwenswarm.extensions.harness import (
    ExtensionBuildContext,
    HarnessContribution,
    merge_harness_contributions,
    merge_harness_specs,
    resolve_harness_contribution,
)
from jiuwenswarm.extensions.loader import ExtensionLoader, _extension_module_name
from jiuwenswarm.extensions.registry import ExtensionRegistry
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


def _tool_card(name: str) -> ToolCard:
    return ToolCard(
        id=name,
        name=name,
        description=name,
        input_params={"type": "object", "properties": {}},
    )


class _FakeTool(Tool):
    def __init__(self, name: str):
        super().__init__(_tool_card(name))

    async def invoke(self, inputs=None, **kwargs):
        return {"ok": True}

    async def stream(self, inputs=None, **kwargs):
        yield {"ok": True}


class _FakeRail(AgentRail):
    pass


class _TypedToolInput(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _reset_extension_registry() -> None:
    ExtensionRegistry.reset_instance()
    yield
    ExtensionRegistry.reset_instance()


def test_harness_contribution_validates_spec_types() -> None:
    with pytest.raises(TypeError, match="BuiltinToolSpec"):
        HarnessContribution(tools=[object()])
    with pytest.raises(TypeError, match="RailSpec"):
        HarnessContribution(rails=[object()])
    with pytest.raises(TypeError, match="JSON-serializable"):
        HarnessContribution(
            tools=[BuiltinToolSpec(type="bad", params={"value": object()})]
        )


def test_registry_collects_contributors_and_isolates_failures() -> None:
    log = MagicMock()
    registry = ExtensionRegistry.create_instance(None, {}, log)
    registry.register_harness_contributor(
        "ok",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type="extension.test.tool")]
        ),
    )

    def _broken(_context):
        raise RuntimeError("boom")

    registry.register_harness_contributor("broken", _broken)
    registry.register_harness_contributor("disabled", lambda context: None)

    collected = registry.collect_harness_contributions(
        ExtensionBuildContext(mode="agent")
    )

    assert [item.name for item in collected] == ["ok"]
    assert registry.list_harness_contributors() == ["ok", "broken", "disabled"]
    log.warning.assert_called_once()
    assert "broken" in log.warning.call_args.args[1]


def test_registry_revalidates_mutated_contribution_and_skips_optional() -> None:
    log = MagicMock()
    registry = ExtensionRegistry.create_instance(None, {}, log)
    contribution = HarnessContribution(
        tools=[BuiltinToolSpec(type="extension.mutated.tool")]
    )
    contribution.tools.append(object())
    registry.register_harness_contributor("mutated", lambda context: contribution)

    assert (
        registry.collect_harness_contributions(ExtensionBuildContext(mode="agent"))
        == []
    )
    assert "BuiltinToolSpec" in str(log.warning.call_args.args[2])


def test_registry_snapshots_specs_before_returning_them() -> None:
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    spec = BuiltinToolSpec(type="extension.snapshot.tool", params={"value": 1})
    contribution = HarnessContribution(tools=[spec])
    registry.register_harness_contributor("snapshot", lambda context: contribution)

    collected = registry.collect_harness_contributions(
        ExtensionBuildContext(mode="agent")
    )
    spec.params["value"] = 2
    contribution.tools.clear()

    assert collected[0].contribution.tools[0].params == {"value": 1}


def test_registry_fails_closed_for_mutated_required_contribution() -> None:
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    contribution = HarnessContribution(
        tools=[BuiltinToolSpec(type="extension.required.mutated")]
    )
    contribution.tools[0].params["invalid"] = object()
    registry.register_harness_contributor(
        "required-mutated",
        lambda context: contribution,
        failure_policy="raise",
    )

    with pytest.raises(RuntimeError, match="required harness contributor"):
        registry.collect_harness_contributions(ExtensionBuildContext(mode="agent"))


def test_registry_rejects_invalid_and_duplicate_names() -> None:
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())

    with pytest.raises(ValueError, match="name is required"):
        registry.register_harness_contributor("", lambda context: None)
    with pytest.raises(TypeError, match="must be callable"):
        registry.register_harness_contributor("bad", object())

    async def _async_contributor(context):
        return None

    with pytest.raises(TypeError, match="must be synchronous"):
        registry.register_harness_contributor("async", _async_contributor)
    with pytest.raises(ValueError, match="failure_policy"):
        registry.register_harness_contributor(
            "policy",
            lambda context: None,
            failure_policy="ignore",
        )

    registry.register_harness_contributor("same", lambda context: None)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_harness_contributor("same", lambda context: None)

    registry.unregister_harness_contributor("same")
    assert registry.list_harness_contributors() == []


def test_registry_rejects_awaitable_contributor_result() -> None:
    log = MagicMock()
    registry = ExtensionRegistry.create_instance(None, {}, log)

    async def _build_later():
        return HarnessContribution()

    registry.register_harness_contributor(
        "awaitable-result",
        lambda context: _build_later(),
    )

    assert (
        registry.collect_harness_contributions(ExtensionBuildContext(mode="agent"))
        == []
    )
    assert "synchronously" in str(log.warning.call_args.args[2])


def test_required_contributor_failure_is_fail_closed() -> None:
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())

    def _broken(context):
        raise RuntimeError("required failed")

    registry.register_harness_contributor(
        "required",
        _broken,
        failure_policy="raise",
    )

    with pytest.raises(RuntimeError, match="required harness contributor"):
        registry.collect_harness_contributions(ExtensionBuildContext(mode="agent"))


def test_required_contributor_rejects_empty_contribution() -> None:
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "required-empty",
        lambda context: HarnessContribution(),
        failure_policy="raise",
    )

    with pytest.raises(RuntimeError, match="required harness contributor"):
        registry.collect_harness_contributions(ExtensionBuildContext(mode="agent"))


def test_merge_harness_contributions_removes_exact_duplicates() -> None:
    tool = BuiltinToolSpec(type="extension.merge.tool", params={"x": 1})
    rail = RailSpec(type="extension.merge.rail", params={"x": 1})

    merged = merge_harness_contributions(
        [
            HarnessContribution(tools=[tool], rails=[rail]),
            HarnessContribution(tools=[tool, tool], rails=[rail]),
        ]
    )

    assert merged.tools == [tool]
    assert merged.rails == [rail]

    base_tools, base_rails = merge_harness_specs(
        tools=[tool],
        rails=[rail],
        contribution=merged,
    )
    assert base_tools == [tool]
    assert base_rails == [rail]


def test_resolve_harness_contribution_uses_agent_core_providers() -> None:
    tool_type = "extension.resolve.tool"
    rail_type = "extension.resolve.rail"
    observed: list[tuple[str, dict, object]] = []
    tool = _FakeTool("extension_resolve_tool")
    rail = _FakeRail()

    def _tool_provider(params, context):
        observed.append(("tool", params, context))
        return [tool]

    def _rail_provider(params, context):
        observed.append(("rail", params, context))
        return rail

    register_tool_provider(tool_type, _tool_provider)
    register_rail_provider(rail_type, _rail_provider)
    context = ExtensionBuildContext(
        language="en",
        mode="code",
        project_dir="/project",
    )

    resolved = resolve_harness_contribution(
        HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type, params={"a": 1})],
            rails=[RailSpec(type=rail_type, params={"b": 2})],
        ),
        context=context,
    )

    assert resolved.tools == [tool]
    assert resolved.rails == [rail]
    assert observed == [
        ("rail", {"b": 2}, context),
        ("tool", {"a": 1}, context),
    ]


def test_deep_adapter_mounts_extension_tool_and_rail_atomically(monkeypatch) -> None:
    tool_type = "extension.adapter.tool"
    rail_type = "extension.adapter.rail"
    tool_name = "extension_adapter_tool"
    tool = _FakeTool(tool_name)
    rail = _FakeRail()
    observed_contexts = []

    register_tool_provider(tool_type, lambda params, context: tool)
    register_rail_provider(rail_type, lambda params, context: rail)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())

    def _contributor(context):
        observed_contexts.append(context)
        return HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
            rails=[RailSpec(type=rail_type)],
        )

    registry.register_harness_contributor("adapter", _contributor)
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")
    workspace = Workspace(root_path=".", language="cn")

    try:
        tools, rails = adapter._build_extension_harness_resources(
            mode="code",
            config_base={"extension_test": True},
            agent_card=AgentCard(id="agent-id", name="agent"),
            workspace=workspace,
            model=MagicMock(),
            sys_operation=MagicMock(),
            existing_tool_cards=[],
        )
        assert tools == [tool]
        assert rails == [rail]
        assert observed_contexts[0].mode == "code"
        assert observed_contexts[0].workspace is workspace
        assert observed_contexts[0].config == {"extension_test": True}
    finally:
        Runner.resource_mgr.remove_tool(tool_name)


def test_deep_adapter_accepts_registered_tool_card(monkeypatch) -> None:
    tool_type = "extension.adapter.registered_tool"
    registered_tool = _FakeTool("extension_registered_tool")
    registered_tool.card.stateless = True
    tool_card = registered_tool.card

    register_tool_provider(tool_type, lambda params, context: tool_card)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "registered-tool",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    Runner.resource_mgr.add_tool(registered_tool)
    try:
        tools, rails = adapter._build_extension_harness_resources(
            mode="agent",
            config_base={},
            agent_card=AgentCard(id="agent-id", name="agent"),
            workspace=Workspace(root_path=".", language="cn"),
            model=MagicMock(),
            sys_operation=MagicMock(),
            existing_tool_cards=[],
        )

        assert tools == [tool_card]
        assert rails == []
    finally:
        Runner.resource_mgr.remove_tool(tool_card.id)


def test_resolve_accepts_equivalent_tool_card_with_model_input_schema() -> None:
    tool_type = "extension.typed.registered_tool"
    registered_tool = _FakeTool("extension_typed_registered_tool")
    registered_tool.card.input_params = _TypedToolInput
    registered_tool.card.stateless = True
    reference = registered_tool.card.model_copy(deep=True)

    register_tool_provider(tool_type, lambda params, context: reference)
    Runner.resource_mgr.add_tool(registered_tool)
    try:
        resolved = resolve_harness_contribution(
            HarnessContribution(tools=[BuiltinToolSpec(type=tool_type)]),
            context=ExtensionBuildContext(mode="agent"),
        )

        assert resolved.tools == [registered_tool.card]
    finally:
        Runner.resource_mgr.remove_tool(registered_tool.card.id)


def test_deep_adapter_rejects_unregistered_tool_card(monkeypatch) -> None:
    tool_type = "extension.adapter.missing_registered_tool"
    tool_card = _tool_card("extension_missing_registered_tool")

    register_tool_provider(tool_type, lambda params, context: tool_card)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "missing-registered-tool",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    tools, rails = adapter._build_extension_harness_resources(
        mode="agent",
        config_base={},
        agent_card=AgentCard(id="agent-id", name="agent"),
        workspace=Workspace(root_path=".", language="cn"),
        model=MagicMock(),
        sys_operation=MagicMock(),
        existing_tool_cards=[],
    )

    assert tools == []
    assert rails == []


def test_deep_adapter_rejects_spoofed_tool_card(monkeypatch) -> None:
    tool_type = "extension.adapter.spoofed_tool"
    registered_tool = _FakeTool("extension_dangerous_tool")
    registered_tool.card.stateless = True
    spoofed_card = _tool_card("extension_benign_tool")
    spoofed_card.id = registered_tool.card.id
    spoofed_card.stateless = True

    register_tool_provider(tool_type, lambda params, context: spoofed_card)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "spoofed-tool",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    Runner.resource_mgr.add_tool(registered_tool)
    try:
        tools, rails = adapter._build_extension_harness_resources(
            mode="agent",
            config_base={},
            agent_card=AgentCard(id="agent-id", name="agent"),
            workspace=Workspace(root_path=".", language="cn"),
            model=MagicMock(),
            sys_operation=MagicMock(),
            existing_tool_cards=[],
        )

        assert tools == []
        assert rails == []
    finally:
        Runner.resource_mgr.remove_tool(registered_tool.card.id)


def test_optional_tool_conflict_skips_whole_contribution(monkeypatch) -> None:
    tool_type = "extension.adapter.optional_conflict"
    rail_type = "extension.adapter.optional_conflict_rail"
    tool = _FakeTool("extension_conflicting_tool")
    rail = _FakeRail()

    register_tool_provider(tool_type, lambda params, context: tool)
    register_rail_provider(rail_type, lambda params, context: rail)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "optional-conflict",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
            rails=[RailSpec(type=rail_type)],
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    tools, rails = adapter._build_extension_harness_resources(
        mode="agent",
        config_base={},
        agent_card=AgentCard(id="agent-id", name="agent"),
        workspace=Workspace(root_path=".", language="cn"),
        model=MagicMock(),
        sys_operation=MagicMock(),
        existing_tool_cards=[_tool_card("extension_conflicting_tool")],
    )

    assert tools == []
    assert rails == []


def test_required_tool_conflict_fails_closed(monkeypatch) -> None:
    tool_type = "extension.adapter.required_conflict"
    tool = _FakeTool("extension_required_conflicting_tool")

    register_tool_provider(tool_type, lambda params, context: tool)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "required-conflict",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
        failure_policy="raise",
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    with pytest.raises(RuntimeError, match="duplicates an existing agent tool"):
        adapter._build_extension_harness_resources(
            mode="agent",
            config_base={},
            agent_card=AgentCard(id="agent-id", name="agent"),
            workspace=Workspace(root_path=".", language="cn"),
            model=MagicMock(),
            sys_operation=MagicMock(),
            existing_tool_cards=[_tool_card("extension_required_conflicting_tool")],
        )


def test_empty_optional_provider_does_not_hide_later_required_spec(monkeypatch) -> None:
    tool_type = "extension.adapter.retry_empty_provider"
    tool = _FakeTool("extension_retry_empty_provider_tool")
    calls = 0

    def _provider(params, context):
        nonlocal calls
        calls += 1
        return None if calls == 1 else tool

    register_tool_provider(tool_type, _provider)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "optional-empty-provider",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
    )
    registry.register_harness_contributor(
        "required-after-empty",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
        ),
        failure_policy="raise",
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    tools, rails = adapter._build_extension_harness_resources(
        mode="agent",
        config_base={},
        agent_card=AgentCard(id="agent-id", name="agent"),
        workspace=Workspace(root_path=".", language="cn"),
        model=MagicMock(),
        sys_operation=MagicMock(),
        existing_tool_cards=[],
    )

    assert calls == 2
    assert tools == [tool]
    assert rails == []


def test_runtime_tool_owner_ids_isolate_without_changing_agent_identity() -> None:
    first_adapter = JiuWenSwarmDeepAdapter()
    second_adapter = JiuWenSwarmDeepAdapter()
    first_adapter.mark_as_session_scoped("session-a")
    second_adapter.mark_as_session_scoped("session-b")

    first_owner_id = first_adapter._build_runtime_tool_owner_id()
    second_owner_id = second_adapter._build_runtime_tool_owner_id()
    assert first_owner_id != second_owner_id
    assert first_owner_id == first_adapter._build_runtime_tool_owner_id()
    first_root_id = JiuWenSwarmDeepAdapter()._build_runtime_tool_owner_id()
    second_root_id = JiuWenSwarmDeepAdapter()._build_runtime_tool_owner_id()
    assert first_root_id != second_root_id

    tool_name = f"extension_session_tool_{uuid.uuid4().hex}"
    first_tool = _FakeTool(tool_name)
    second_tool = _FakeTool(tool_name)
    first_manager = AbilityManager(owner_id=first_owner_id)
    second_manager = AbilityManager(owner_id=second_owner_id)

    try:
        first_manager.add_ability(first_tool.card, first_tool)
        second_manager.add_ability(second_tool.card, second_tool)

        assert first_tool.card.id != second_tool.card.id
        assert Runner.resource_mgr.get_tool(first_tool.card.id) is first_tool
        assert Runner.resource_mgr.get_tool(second_tool.card.id) is second_tool
    finally:
        first_manager.teardown_tools()
        second_manager.teardown_tools()


@pytest.mark.asyncio
async def test_adapter_cleanup_tears_down_stateful_tools() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter.mark_as_session_scoped("cleanup-session")
    owner_id = adapter._build_runtime_tool_owner_id()
    manager = AbilityManager(owner_id=owner_id)
    tool = _FakeTool(f"extension_cleanup_tool_{uuid.uuid4().hex}")
    instance = MagicMock(ability_manager=manager, stop=AsyncMock())
    adapter._instance = instance
    manager.add_ability(tool.card, tool)
    registered_id = tool.card.id

    await adapter.cleanup()

    assert Runner.resource_mgr.get_tool(registered_id) is None
    instance.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_adapter_cleanup_unregisters_extension_rails() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter.mark_as_session_scoped("cleanup-rail-session")
    rail = _FakeRail()
    instance = MagicMock(
        ability_manager=MagicMock(teardown_tools=MagicMock()),
        stop=AsyncMock(),
        is_registered_rail=MagicMock(return_value=True),
        unregister_rail=AsyncMock(),
    )
    adapter._instance = instance
    adapter._extension_rails = [rail]

    await adapter.cleanup()

    instance.unregister_rail.assert_awaited_once_with(rail)
    assert adapter._extension_rails == []


@pytest.mark.asyncio
async def test_mount_conflict_with_rail_tool_does_not_remove_host_tool() -> None:
    adapter = JiuWenSwarmDeepAdapter()
    owner_id = adapter._build_runtime_tool_owner_id()
    manager = AbilityManager(owner_id=owner_id)
    tool_name = f"extension_host_conflict_{uuid.uuid4().hex}"
    host_tool = _FakeTool(tool_name)
    extension_tool = _FakeTool(tool_name)
    manager.add_ability(host_tool.card, host_tool)
    adapter._instance = MagicMock(ability_manager=manager)
    adapter._extension_tools = [extension_tool]

    try:
        with pytest.raises(RuntimeError, match="duplicates an initialized agent tool"):
            adapter._mount_extension_tools([extension_tool])

        await adapter._teardown_extension_harness_resources()
        assert manager.get(tool_name) is host_tool.card
        assert Runner.resource_mgr.get_tool(host_tool.card.id) is host_tool
    finally:
        manager.teardown_tools()


@pytest.mark.asyncio
async def test_deep_create_instance_passes_extension_resources_to_factory(
    monkeypatch,
    tmp_path,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep

    base_tool = _tool_card("base_tool")
    extension_tool = _FakeTool("extension_create_tool")
    base_rail = object()
    extension_rail = object()
    created_instance = MagicMock(
        ensure_initialized=AsyncMock(),
    )
    adapter = JiuWenSwarmDeepAdapter()

    monkeypatch.setattr(interface_deep, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr(
        interface_deep,
        "get_config",
        lambda: {
            "react": {
                "agent_name": "test-agent",
                "workspace_dir": str(tmp_path),
            }
        },
    )
    monkeypatch.setattr(interface_deep, "PromptAttachmentLoader", MagicMock())
    monkeypatch.setattr(adapter, "set_checkpoint", AsyncMock())
    monkeypatch.setattr(adapter, "_refresh_multimodal_configs", lambda config: None)
    monkeypatch.setattr(adapter, "_create_model", lambda config: object())
    monkeypatch.setattr(
        adapter,
        "_get_tool_cards",
        AsyncMock(return_value=[base_tool]),
    )
    monkeypatch.setattr(
        adapter,
        "_build_agent_rails",
        lambda config, config_base, mode: [base_rail],
    )
    monkeypatch.setattr(adapter, "_create_sys_operation", lambda: object())
    monkeypatch.setattr(
        adapter,
        "_build_configured_subagents",
        lambda model, config, config_base: ([], False),
    )
    build_extensions = MagicMock(return_value=([extension_tool], [extension_rail]))
    monkeypatch.setattr(
        adapter,
        "_build_extension_harness_resources",
        build_extensions,
    )
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")
    monkeypatch.setattr(adapter, "_resolve_prompt_language", lambda: "en")
    monkeypatch.setattr(adapter, "_resolve_enable_task_loop", lambda *args: True)
    monkeypatch.setattr(
        adapter,
        "_resolve_enable_read_image_multimodal",
        lambda config: False,
    )
    monkeypatch.setattr(adapter, "_seed_runtime_cwd", MagicMock())
    monkeypatch.setattr(adapter, "_sync_a2x_runtime_state", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_register_mcp_servers_from_config",
        AsyncMock(),
    )
    monkeypatch.setattr(adapter, "_load_active_packages", AsyncMock())
    monkeypatch.setattr(adapter, "load_user_rails", AsyncMock())
    mount_extensions = MagicMock()
    monkeypatch.setattr(adapter, "_mount_extension_tools", mount_extensions)
    create_agent = MagicMock(return_value=created_instance)
    monkeypatch.setattr(interface_deep, "create_deep_agent", create_agent)

    await adapter.create_instance(mode="agent")

    assert build_extensions.call_args.kwargs["mode"] == "agent"
    assert build_extensions.call_args.kwargs["sub_mode"] is None
    mount_extensions.assert_called_once_with([extension_tool])
    factory_kwargs = create_agent.call_args.kwargs
    assert factory_kwargs["card"].id == "jiuwenswarm"
    assert factory_kwargs["tools"] == [base_tool]
    assert factory_kwargs["ability_owner_id"] == adapter._build_runtime_tool_owner_id()
    assert factory_kwargs["rails"][0] is base_rail
    assert factory_kwargs["rails"][1] is extension_rail


@pytest.mark.asyncio
async def test_code_create_instance_passes_extension_resources_in_code_mode(
    monkeypatch,
    tmp_path,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_code

    base_tool = _tool_card("base_code_tool")
    extension_tool = _FakeTool("extension_code_tool")
    base_rail = object()
    extension_rail = object()
    created_instance = MagicMock(
        ensure_initialized=AsyncMock(),
        _registered_rails=[],
    )
    adapter = interface_code.JiuwenSwarmCodeAdapter()

    monkeypatch.setattr(
        interface_code,
        "get_config",
        lambda: {
            "react": {
                "agent_name": "code-agent",
                "workspace_dir": str(tmp_path),
            }
        },
    )
    monkeypatch.setattr(
        interface_code,
        "get_agent_workspace_dir",
        lambda: tmp_path / "agent-workspace",
    )
    monkeypatch.setattr(
        interface_code,
        "_set_workspace_coding_memory_directory",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(interface_code, "build_code_system_prompt", lambda: "prompt")
    monkeypatch.setattr(adapter, "set_checkpoint", AsyncMock())
    monkeypatch.setattr(adapter, "_refresh_multimodal_configs", lambda config: None)
    monkeypatch.setattr(adapter, "_create_model", lambda config: object())
    monkeypatch.setattr(
        adapter,
        "_get_tool_cards",
        AsyncMock(return_value=[base_tool]),
    )
    monkeypatch.setattr(
        adapter,
        "_build_agent_rails",
        lambda config, config_base, mode: [base_rail],
    )
    monkeypatch.setattr(adapter, "_create_sys_operation", lambda: object())
    monkeypatch.setattr(
        adapter,
        "_build_configured_subagents",
        lambda model, config, config_base: ([], False),
    )
    build_extensions = MagicMock(return_value=([extension_tool], [extension_rail]))
    monkeypatch.setattr(
        adapter,
        "_build_extension_harness_resources",
        build_extensions,
    )
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")
    monkeypatch.setattr(adapter, "_seed_runtime_cwd", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_register_mcp_servers_from_config",
        AsyncMock(),
    )
    monkeypatch.setattr(adapter, "load_user_rails", AsyncMock())
    mount_extensions = MagicMock()
    monkeypatch.setattr(adapter, "_mount_extension_tools", mount_extensions)
    create_agent = MagicMock(return_value=created_instance)
    monkeypatch.setattr(interface_code, "create_deep_agent", create_agent)

    await adapter.create_instance(
        {"project_dir": str(tmp_path)},
        mode="code",
    )

    assert build_extensions.call_args.kwargs["mode"] == "code"
    assert build_extensions.call_args.kwargs["sub_mode"] is None
    mount_extensions.assert_called_once_with([extension_tool])
    factory_kwargs = create_agent.call_args.kwargs
    assert factory_kwargs["card"].id == "jiuwenswarm"
    assert factory_kwargs["tools"] == [base_tool]
    assert factory_kwargs["ability_owner_id"] == adapter._build_runtime_tool_owner_id()
    assert factory_kwargs["rails"][0] is base_rail
    assert factory_kwargs["rails"][1] is extension_rail


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_stage",
    ["ensure", "mcp", "package", "user_rail"],
)
async def test_deep_create_failure_tears_down_extension_resources(
    monkeypatch,
    tmp_path,
    failure_stage: str,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_deep

    failure = RuntimeError(f"{failure_stage} failed")
    created_instance = MagicMock(
        ensure_initialized=AsyncMock(
            side_effect=failure if failure_stage == "ensure" else None,
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()

    monkeypatch.setattr(interface_deep, "load_dotenv", lambda **kwargs: None)
    monkeypatch.setattr(
        interface_deep,
        "get_config",
        lambda: {
            "react": {
                "agent_name": "test-agent",
                "workspace_dir": str(tmp_path),
            }
        },
    )
    monkeypatch.setattr(interface_deep, "PromptAttachmentLoader", MagicMock())
    monkeypatch.setattr(adapter, "set_checkpoint", AsyncMock())
    monkeypatch.setattr(adapter, "_refresh_multimodal_configs", lambda config: None)
    monkeypatch.setattr(adapter, "_create_model", lambda config: object())
    monkeypatch.setattr(adapter, "_get_tool_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        adapter,
        "_build_agent_rails",
        lambda config, config_base, mode: [],
    )
    monkeypatch.setattr(adapter, "_create_sys_operation", lambda: object())
    monkeypatch.setattr(
        adapter,
        "_build_configured_subagents",
        lambda model, config, config_base: ([], False),
    )
    monkeypatch.setattr(
        adapter,
        "_build_extension_harness_resources",
        MagicMock(return_value=([], [_FakeRail()])),
    )
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")
    monkeypatch.setattr(adapter, "_resolve_prompt_language", lambda: "en")
    monkeypatch.setattr(adapter, "_resolve_enable_task_loop", lambda *args: True)
    monkeypatch.setattr(
        adapter,
        "_resolve_enable_read_image_multimodal",
        lambda config: False,
    )
    monkeypatch.setattr(adapter, "_seed_runtime_cwd", MagicMock())
    monkeypatch.setattr(adapter, "_sync_a2x_runtime_state", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_register_mcp_servers_from_config",
        AsyncMock(side_effect=failure if failure_stage == "mcp" else None),
    )
    monkeypatch.setattr(
        adapter,
        "_load_active_packages",
        AsyncMock(side_effect=failure if failure_stage == "package" else None),
    )
    monkeypatch.setattr(
        adapter,
        "load_user_rails",
        AsyncMock(side_effect=failure if failure_stage == "user_rail" else None),
    )
    teardown = AsyncMock()
    monkeypatch.setattr(adapter, "_teardown_failed_create_resources", teardown)
    monkeypatch.setattr(
        interface_deep,
        "create_deep_agent",
        MagicMock(return_value=created_instance),
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        await adapter.create_instance(mode="agent")

    teardown.assert_awaited_once_with(log_prefix="JiuWenSwarmDeepAdapter")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_stage", ["ensure", "mcp", "user_rail"])
async def test_code_create_failure_tears_down_extension_resources(
    monkeypatch,
    tmp_path,
    failure_stage: str,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface_code

    failure = RuntimeError(f"{failure_stage} failed")
    created_instance = MagicMock(
        ensure_initialized=AsyncMock(
            side_effect=failure if failure_stage == "ensure" else None,
        ),
        _registered_rails=[],
    )
    adapter = interface_code.JiuwenSwarmCodeAdapter()

    monkeypatch.setattr(
        interface_code,
        "get_config",
        lambda: {
            "react": {
                "agent_name": "code-agent",
                "workspace_dir": str(tmp_path),
            }
        },
    )
    monkeypatch.setattr(
        interface_code,
        "get_agent_workspace_dir",
        lambda: tmp_path / "agent-workspace",
    )
    monkeypatch.setattr(
        interface_code,
        "_set_workspace_coding_memory_directory",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(interface_code, "build_code_system_prompt", lambda: "prompt")
    monkeypatch.setattr(adapter, "set_checkpoint", AsyncMock())
    monkeypatch.setattr(adapter, "_refresh_multimodal_configs", lambda config: None)
    monkeypatch.setattr(adapter, "_create_model", lambda config: object())
    monkeypatch.setattr(adapter, "_get_tool_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        adapter,
        "_build_agent_rails",
        lambda config, config_base, mode: [],
    )
    monkeypatch.setattr(adapter, "_create_sys_operation", lambda: object())
    monkeypatch.setattr(
        adapter,
        "_build_configured_subagents",
        lambda model, config, config_base: ([], False),
    )
    monkeypatch.setattr(
        adapter,
        "_build_extension_harness_resources",
        MagicMock(return_value=([], [_FakeRail()])),
    )
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")
    monkeypatch.setattr(adapter, "_seed_runtime_cwd", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_register_mcp_servers_from_config",
        AsyncMock(side_effect=failure if failure_stage == "mcp" else None),
    )
    monkeypatch.setattr(
        adapter,
        "load_user_rails",
        AsyncMock(side_effect=failure if failure_stage == "user_rail" else None),
    )
    teardown = AsyncMock()
    monkeypatch.setattr(adapter, "_teardown_failed_create_resources", teardown)
    monkeypatch.setattr(
        interface_code,
        "create_deep_agent",
        MagicMock(return_value=created_instance),
    )

    with pytest.raises(RuntimeError, match=f"{failure_stage} failed"):
        await adapter.create_instance(
            {"project_dir": str(tmp_path)},
            mode="code",
        )

    teardown.assert_awaited_once_with(log_prefix="JiuwenSwarmCodeAdapter")


def test_deep_adapter_drops_whole_contribution_when_rail_build_fails(
    monkeypatch,
) -> None:
    tool_type = "extension.atomic.tool"
    rail_type = "extension.atomic.rail"
    tool_name = "extension_atomic_tool"
    tool = _FakeTool(tool_name)

    register_tool_provider(tool_type, lambda params, context: tool)

    def _broken_rail(params, context):
        raise RuntimeError("rail failed")

    register_rail_provider(rail_type, _broken_rail)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "atomic",
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=tool_type)],
            rails=[RailSpec(type=rail_type)],
        ),
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    tools, rails = adapter._build_extension_harness_resources(
        mode="agent",
        config_base={},
        agent_card=AgentCard(id="agent-id", name="agent"),
        workspace=Workspace(root_path=".", language="cn"),
        model=MagicMock(),
        sys_operation=MagicMock(),
        existing_tool_cards=[],
    )

    assert tools == []
    assert rails == []
    assert Runner.resource_mgr.get_tool(tool_name) is None


def test_deep_adapter_fails_closed_for_required_contribution(monkeypatch) -> None:
    rail_type = "extension.required.rail"

    def _broken_rail(params, context):
        raise RuntimeError("required rail failed")

    register_rail_provider(rail_type, _broken_rail)
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        "required-rail",
        lambda context: HarnessContribution(
            rails=[RailSpec(type=rail_type)],
        ),
        failure_policy="raise",
    )
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")

    with pytest.raises(RuntimeError, match="required extension harness"):
        adapter._build_extension_harness_resources(
            mode="agent",
            config_base={},
            agent_card=AgentCard(id="agent-id", name="agent"),
            workspace=Workspace(root_path=".", language="cn"),
            model=MagicMock(),
            sys_operation=MagicMock(),
            existing_tool_cards=[],
        )


def test_loader_accepts_search_path_that_is_an_extension_root(tmp_path) -> None:
    root = tmp_path / "direct_extension"
    root.mkdir()
    root.joinpath("extension.py").write_text(
        "async def register_extensions(registry):\n    return None\n",
        encoding="utf-8",
    )
    loader = ExtensionLoader(ExtensionRegistry.create_instance(None, {}, MagicMock()))

    loader.add_search_path(root)

    assert loader.discover_extension_roots() == [root]


@pytest.mark.asyncio
async def test_loaded_extension_harness_factory_remains_importable(tmp_path) -> None:
    extension_name = f"manifest_{uuid.uuid4().hex}"
    element_name = f"extension.{extension_name}.tool"
    root = tmp_path / extension_name
    root.mkdir()
    (root / "extension.py").write_text(
        "\n".join(
            [
                "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                f"@harness_element(kind=ElementKind.TOOL, name={element_name!r}, description='test')",
                "def build_tool(params, context):",
                "    return None",
                "async def register_extensions(registry):",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )
    loader = ExtensionLoader(ExtensionRegistry.create_instance(None, {}, MagicMock()))

    loaded = await loader.load_extension(root)

    qualified_name = _extension_module_name(root)
    assert loaded.metadata.id == extension_name
    assert loaded.metadata.name == extension_name
    assert loaded.metadata.version == "unknown"
    assert qualified_name in sys.modules
    # Resolution proves the descriptor's factory_ref can import the dynamically
    # loaded extension module after ExtensionLoader.load_extension has returned.
    descriptor = get_catalog()[element_name]
    assert resolve_factory(descriptor.factory_ref)({}, None) is None


@pytest.mark.asyncio
async def test_same_named_extension_directories_use_distinct_modules(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    first_root = tmp_path / "first" / "shared_name"
    second_root = tmp_path / "second" / "shared_name"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    first_element = f"extension.same_name.first.{suffix}"
    second_element = f"extension.same_name.second.{suffix}"

    for root, element_name, value in (
        (first_root, first_element, "first"),
        (second_root, second_element, "second"),
    ):
        root.joinpath("extension.py").write_text(
            "\n".join(
                [
                    "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                    f"@harness_element(kind=ElementKind.TOOL, name={element_name!r}, description='test')",
                    "def build_tool(params, context):",
                    f"    return {value!r}",
                    "async def register_extensions(registry):",
                    "    return None",
                ]
            ),
            encoding="utf-8",
        )

    loader = ExtensionLoader(ExtensionRegistry.create_instance(None, {}, MagicMock()))
    await loader.load_extension(first_root)
    await loader.load_extension(second_root)

    first_module = _extension_module_name(first_root)
    second_module = _extension_module_name(second_root)
    assert first_module != second_module
    assert first_module in sys.modules
    assert second_module in sys.modules
    first_factory = resolve_factory(get_catalog()[first_element].factory_ref)
    second_factory = resolve_factory(get_catalog()[second_element].factory_ref)
    assert first_factory({}, None) == "first"
    assert second_factory({}, None) == "second"


@pytest.mark.asyncio
async def test_unresolvable_extension_factory_rolls_back_during_load(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    element_name = f"extension.local_factory.{suffix}"
    root = tmp_path / f"local_factory_{suffix}"
    root.mkdir()
    root.joinpath("extension.py").write_text(
        "\n".join(
            [
                "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                "def declare():",
                f"    @harness_element(kind=ElementKind.TOOL, name={element_name!r}, description='bad')",
                "    def build_tool(params, context):",
                "        return None",
                "declare()",
                "async def register_extensions(registry):",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )
    loader = ExtensionLoader(ExtensionRegistry.create_instance(None, {}, MagicMock()))

    with pytest.raises(AttributeError):
        await loader.load_extension(root)

    assert element_name not in get_catalog()
    assert _extension_module_name(root) not in sys.modules


@pytest.mark.asyncio
async def test_extension_set_directory_failure_rolls_back_registration(
    tmp_path,
) -> None:
    suffix = uuid.uuid4().hex
    element_name = f"extension.set_dir.{suffix}"
    contributor_name = f"set-dir-{suffix}"
    root = tmp_path / f"set_dir_{suffix}"
    root.mkdir()
    root.joinpath("extension.py").write_text(
        "\n".join(
            [
                "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                "from jiuwenswarm.extensions import HarnessContribution",
                f"@harness_element(kind=ElementKind.TOOL, name={element_name!r}, description='bad')",
                "def build_tool(params, context):",
                "    return None",
                "class BrokenExtension:",
                "    def set_extension_dir(self, root):",
                "        raise RuntimeError('set dir failed')",
                "async def register_extensions(registry):",
                f"    registry.register_harness_contributor({contributor_name!r}, lambda context: HarnessContribution())",
                "    return BrokenExtension()",
            ]
        ),
        encoding="utf-8",
    )
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    loader = ExtensionLoader(registry)

    with pytest.raises(RuntimeError, match="set dir failed"):
        await loader.load_extension(root)

    assert element_name not in get_catalog()
    assert contributor_name not in registry.list_harness_contributors()
    assert _extension_module_name(root) not in sys.modules


@pytest.mark.asyncio
async def test_failed_extension_rolls_back_catalog_and_contributors(tmp_path) -> None:
    suffix = uuid.uuid4().hex
    bad_root = tmp_path / f"a_bad_{suffix}"
    good_root = tmp_path / f"b_good_{suffix}"
    bad_element = f"extension.bad.{suffix}"
    good_element = f"extension.good.{suffix}"
    bad_root.mkdir()
    good_root.mkdir()
    bad_root.joinpath("extension.py").write_text(
        "\n".join(
            [
                "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                "from jiuwenswarm.extensions import HarnessContribution",
                f"@harness_element(kind=ElementKind.TOOL, name={bad_element!r}, description='bad')",
                "def build_tool(params, context):",
                "    return None",
                "async def register_extensions(registry):",
                "    registry.register_harness_contributor('bad-contributor', lambda context: HarnessContribution())",
                "    raise RuntimeError('bad extension')",
            ]
        ),
        encoding="utf-8",
    )
    good_root.joinpath("extension.py").write_text(
        "\n".join(
            [
                "from openjiuwen.agent_teams.harness.manifest import ElementKind, harness_element",
                f"@harness_element(kind=ElementKind.TOOL, name={good_element!r}, description='good')",
                "def build_tool(params, context):",
                "    return None",
                "async def register_extensions(registry):",
                "    return None",
            ]
        ),
        encoding="utf-8",
    )
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    loader = ExtensionLoader(registry)

    with pytest.raises(RuntimeError, match="bad extension"):
        await loader.load_extension(bad_root)

    assert bad_element not in get_catalog()
    assert "bad-contributor" not in registry.list_harness_contributors()

    await loader.load_extension(good_root)
    descriptor = get_catalog()[good_element]
    assert resolve_factory(descriptor.factory_ref)({}, None) is None


@pytest.mark.asyncio
async def test_failed_extension_restores_contributors_and_removes_helpers(
    tmp_path,
) -> None:
    suffix = uuid.uuid4().hex
    root = tmp_path / f"rollback_{suffix}"
    root.mkdir()
    helper_name = f"extension_helper_{suffix}"
    contributor_name = f"shared-{suffix}"
    original_type = f"extension.original.{suffix}"
    replacement_type = f"extension.replacement.{suffix}"
    root.joinpath(f"{helper_name}.py").write_text(
        "VALUE = 'loaded'\n",
        encoding="utf-8",
    )
    root.joinpath("extension.py").write_text(
        "\n".join(
            [
                "import importlib.util",
                "import sys",
                "from pathlib import Path",
                "from jiuwenswarm.extensions import HarnessContribution",
                "from openjiuwen.agent_teams.schema.deep_agent_spec import BuiltinToolSpec",
                f"helper_name = {helper_name!r}",
                "helper_path = Path(__file__).with_name(helper_name + '.py')",
                "helper_spec = importlib.util.spec_from_file_location(helper_name, helper_path)",
                "helper = importlib.util.module_from_spec(helper_spec)",
                "sys.modules[helper_name] = helper",
                "helper_spec.loader.exec_module(helper)",
                "async def register_extensions(registry):",
                f"    registry.unregister_harness_contributor({contributor_name!r})",
                f"    registry.register_harness_contributor({contributor_name!r}, lambda context: HarnessContribution(tools=[BuiltinToolSpec(type={replacement_type!r})]))",
                "    raise RuntimeError('rollback requested')",
            ]
        ),
        encoding="utf-8",
    )
    registry = ExtensionRegistry.create_instance(None, {}, MagicMock())
    registry.register_harness_contributor(
        contributor_name,
        lambda context: HarnessContribution(
            tools=[BuiltinToolSpec(type=original_type)]
        ),
        failure_policy="raise",
    )
    loader = ExtensionLoader(registry)

    with pytest.raises(RuntimeError, match="rollback requested"):
        await loader.load_extension(root)

    collected = registry.collect_harness_contributions(
        ExtensionBuildContext(mode="agent")
    )
    assert collected[0].contribution.tools[0].type == original_type
    assert collected[0].failure_policy == "raise"
    assert helper_name not in sys.modules
