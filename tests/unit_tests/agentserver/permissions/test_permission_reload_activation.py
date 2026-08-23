"""Permission activation and rail lifecycle contracts for adapter reloads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.rail.base import AgentCallbackEvent
from openjiuwen.harness import DeepAgent, DeepAgentConfig
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.auto_permission_rail import (
    AutoPermissionInterruptRail,
)
from jiuwenswarm.server.runtime.agent_adapter.browser_runtime_security import (
    BrowserRuntimeSecurityProfile,
)
from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)

_ENFORCED_BROWSER_PROFILE = BrowserRuntimeSecurityProfile(
    network_guard_enforced=True,
    guard_provider="owned-guard",
)


class _PermissionRail:
    def __init__(self, name: str) -> None:
        self.name = name
        self.updated_configs: list[dict[str, object]] = []
        self.callback_started = asyncio.Event()
        self.callback_release = asyncio.Event()

    def update_config(self, config: dict[str, object]) -> None:
        self.updated_configs.append(config)

    async def hold_callback(self) -> None:
        self.callback_started.set()
        await self.callback_release.wait()


class _ManualRail(_PermissionRail):
    pass


class _AutoRail(_PermissionRail):
    pass


class _ReloadRuntime:
    def __init__(self, rails: list[_PermissionRail] | None = None) -> None:
        self.registered = list(rails or [])
        self.live_callbacks = list(rails or [])
        self.events: list[tuple[str, object]] = []
        self.configured: list[object] = []
        self.fail_register: set[_PermissionRail] = set()
        self.fail_unregister: set[_PermissionRail] = set()
        self.fail_configure = False
        self.require_permission_rail = True
        self.unregister_entered = asyncio.Event()
        self.wait_for_callback: _PermissionRail | None = None

    def find_rails_by_type(self, rail_types: tuple[type, ...]) -> list[object]:
        return [rail for rail in self.registered if isinstance(rail, rail_types)]

    def is_registered_rail(self, rail: object) -> bool:
        return rail in self.registered

    async def register_rail(self, rail: _PermissionRail) -> None:
        self.events.append(("register", rail))
        if rail in self.fail_register:
            raise RuntimeError("register failed")
        self.registered.append(rail)
        self.live_callbacks.append(rail)

    async def unregister_rail(self, rail: _PermissionRail) -> None:
        self.events.append(("unregister", rail))
        if rail in self.registered:
            self.registered.remove(rail)
        if rail in self.fail_unregister:
            raise RuntimeError("unregister failed")
        if self.wait_for_callback is rail:
            self.unregister_entered.set()
            await rail.callback_release.wait()
        if rail in self.live_callbacks:
            self.live_callbacks.remove(rail)

    def configure(self, config: object) -> None:
        self.events.append(("configure", config))
        if self.fail_configure:
            raise RuntimeError("configure failed")
        self.configured.append(config)


def _new_adapter(
    runtime: _ReloadRuntime,
    *,
    permission_rail: _PermissionRail | None,
    auto_enabled: bool,
) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = runtime
    adapter._permission_rail = permission_rail
    adapter._enable_auto_permission = auto_enabled
    adapter.mark_as_session_scoped("session-1")
    adapter._model = MagicMock(name="model")
    adapter._sys_operation = MagicMock(name="sys_operation")
    return adapter


async def _reload(
    monkeypatch: pytest.MonkeyPatch,
    adapter: JiuWenSwarmDeepAdapter,
    config: dict[str, object],
    *,
    candidates: list[_PermissionRail],
    require_permission_rail: bool | None = None,
) -> MagicMock:
    runtime = adapter._instance
    permission_config = config.get("permissions", {})
    runtime.require_permission_rail = (
        bool(
            isinstance(permission_config, dict)
            and permission_config.get("enabled") is True
        )
        if require_permission_rail is None
        else require_permission_rail
    )
    other_rail = object()

    def build_candidate(*args: object, **kwargs: object) -> _PermissionRail:
        del args
        candidate_type = _AutoRail if kwargs["enable_auto_permission"] else _ManualRail
        candidate = candidate_type(f"candidate-{len(candidates)}")
        candidates.append(candidate)
        runtime.events.append(("prepare_candidate", candidate))
        return candidate

    deep_config = SimpleNamespace(
        rails=[other_rail, *runtime.find_rails_by_type((_ManualRail, _AutoRail))],
        permissions={"must": "be removed"},
    )
    make_config = MagicMock(return_value=deep_config)
    monkeypatch.setattr(interface_deep, "clear_config_cache", MagicMock())
    monkeypatch.setattr(interface_deep, "build_permission_rail", build_candidate)
    monkeypatch.setattr(
        "openjiuwen.core.memory.lite.manager.aclose_memory_manager_cache",
        AsyncMock(),
    )
    monkeypatch.setattr(
        adapter,
        "_permission_rail_types",
        MagicMock(return_value=(_ManualRail, _AutoRail)),
    )
    monkeypatch.setattr(adapter, "_refresh_multimodal_configs", MagicMock())
    monkeypatch.setattr(adapter, "_create_model", MagicMock(return_value=object()))
    monkeypatch.setattr(adapter, "_prepare_browser_runtime_security", MagicMock())
    monkeypatch.setattr(adapter, "_sync_multimodal_tools_for_runtime", MagicMock())
    monkeypatch.setattr(adapter, "_sync_paid_search_tool_for_runtime", MagicMock())
    monkeypatch.setattr(adapter, "_sync_symphony_tools_for_runtime", MagicMock())
    monkeypatch.setattr(adapter, "_sync_skill_retrieval_tools_for_runtime", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_sync_skill_retrieval_prompt_rail_for_runtime",
        AsyncMock(),
    )
    monkeypatch.setattr(
        adapter,
        "_filesystem_rail_enabled_for_profile",
        MagicMock(return_value=True),
    )
    monkeypatch.setattr(adapter, "_get_current_agent_rails", MagicMock(return_value=[]))
    monkeypatch.setattr(adapter, "load_user_rails", AsyncMock())
    monkeypatch.setattr(adapter, "_make_deep_agent_config", make_config)
    monkeypatch.setattr(
        adapter, "_omit_unchanged_reload_fields", MagicMock(return_value=({}, {}))
    )
    monkeypatch.setattr(adapter, "_restore_omitted_reload_fields", MagicMock())
    monkeypatch.setattr(adapter, "_commit_reload_fingerprints", MagicMock())
    monkeypatch.setattr(
        adapter,
        "_sync_active_evolution_review_agent_after_reload",
        MagicMock(),
    )
    monkeypatch.setattr(adapter, "_sync_mcp_servers_for_runtime", AsyncMock())
    monkeypatch.setattr(adapter, "_mark_session_adapters_stale_for_reload", MagicMock())
    monkeypatch.setattr(adapter, "_handle_memory_rail_by_config", AsyncMock())

    await adapter.reload_agent_config(config, {})
    return make_config


def _config(*, enabled: object, mode: str) -> dict[str, object]:
    return {
        "react": {"agent_name": "main_agent"},
        "permissions": {"enabled": enabled, "mode": mode},
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "mode", "expected_auto", "expected_type"),
    [
        (True, "auto", True, _AutoRail),
        (True, "manual", False, _ManualRail),
        (False, "auto", False, type(None)),
        (1, "auto", False, type(None)),
    ],
)
async def test_reload_uses_exact_activation_boundary_and_clean_projection(
    monkeypatch: pytest.MonkeyPatch,
    enabled: object,
    mode: str,
    expected_auto: bool,
    expected_type: type,
) -> None:
    runtime = _ReloadRuntime()
    adapter = _new_adapter(runtime, permission_rail=None, auto_enabled=False)
    candidates: list[_PermissionRail] = []

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=enabled, mode=mode),
        candidates=candidates,
    )

    assert adapter._enable_auto_permission is expected_auto
    assert isinstance(adapter._permission_rail, expected_type)
    configured = runtime.configured[-1]
    assert configured.permissions is None
    assert not any(
        isinstance(rail, (_ManualRail, _AutoRail)) for rail in configured.rails
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("instance_mode", "instance_sub_mode", "enabled", "expected_type"),
    [
        ("team", None, True, type(None)),
        ("team", None, False, type(None)),
        ("code", "team", True, type(None)),
        ("code", "team", False, type(None)),
        ("agent", "auto_harness", True, type(None)),
        ("agent", "auto_harness", False, type(None)),
    ],
)
async def test_reload_does_not_install_generic_permission_for_excluded_scope(
    monkeypatch: pytest.MonkeyPatch,
    instance_mode: str,
    instance_sub_mode: str | None,
    enabled: bool,
    expected_type: type,
) -> None:
    runtime = _ReloadRuntime()
    adapter = _new_adapter(runtime, permission_rail=None, auto_enabled=False)
    adapter._session_instance_mode = instance_mode
    adapter._session_instance_sub_mode = instance_sub_mode
    candidates: list[_PermissionRail] = []

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=enabled, mode="auto"),
        candidates=candidates,
        require_permission_rail=False,
    )

    assert adapter._enable_auto_permission is False
    assert isinstance(adapter._permission_rail, expected_type)
    assert candidates == []
    assert not any(isinstance(candidate, _AutoRail) for candidate in candidates)


@pytest.mark.asyncio
async def test_same_type_reload_updates_in_place_without_registration_churn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _AutoRail("existing")
    runtime = _ReloadRuntime([existing])
    adapter = _new_adapter(runtime, permission_rail=existing, auto_enabled=True)

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=True, mode="auto"),
        candidates=[],
    )

    assert existing.updated_configs == [{"enabled": True, "mode": "auto"}]
    assert not [
        event for event in runtime.events if event[0] in {"register", "unregister"}
    ]
    assert adapter._permission_rail is existing


@pytest.mark.asyncio
async def test_reload_refreshes_browser_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _AutoRail("existing")
    runtime = _ReloadRuntime([existing])
    adapter = _new_adapter(runtime, permission_rail=existing, auto_enabled=True)
    adapter._browser_runtime_security_profile = _ENFORCED_BROWSER_PROFILE
    original_set_profile = adapter._set_permission_browser_runtime_security_profile

    def record_profile(rail: object, profile: object) -> None:
        runtime.events.append(
            ("browser_profile", bool(getattr(profile, "network_guard_enforced", False)))
        )
        original_set_profile(rail, profile)

    monkeypatch.setattr(
        adapter,
        "_set_permission_browser_runtime_security_profile",
        record_profile,
    )

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=True, mode="auto"),
        candidates=[],
    )

    assert [event for event in runtime.events if event[0] == "browser_profile"] == [
        ("browser_profile", True),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("old_type", "old_auto", "new_mode", "new_type", "new_auto"),
    [
        (_ManualRail, False, "auto", _AutoRail, True),
        (_AutoRail, True, "manual", _ManualRail, False),
    ],
)
async def test_mode_transition_configures_then_replaces_without_overlap(
    monkeypatch: pytest.MonkeyPatch,
    old_type: type[_PermissionRail],
    old_auto: bool,
    new_mode: str,
    new_type: type[_PermissionRail],
    new_auto: bool,
) -> None:
    old = old_type("old")
    runtime = _ReloadRuntime([old])
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=old_auto)
    adapter._browser_runtime_security_profile = _ENFORCED_BROWSER_PROFILE
    candidates: list[_PermissionRail] = []

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=True, mode=new_mode),
        candidates=candidates,
    )

    candidate = candidates[0]
    assert isinstance(candidate, new_type)
    event_names = [event[0] for event in runtime.events]
    assert event_names.index("prepare_candidate") < event_names.index("configure")
    assert event_names.index("configure") < event_names.index("unregister")
    assert event_names.index("unregister") < event_names.index("register")
    assert runtime.registered == [candidate]
    assert runtime.live_callbacks == [candidate]
    assert adapter._permission_rail is candidate
    assert adapter._enable_auto_permission is new_auto


@pytest.mark.asyncio
async def test_disabling_permissions_configures_before_retiring_live_rail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _AutoRail("old-auto")
    runtime = _ReloadRuntime([old])
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=True)
    adapter._browser_runtime_security_profile = _ENFORCED_BROWSER_PROFILE

    await _reload(
        monkeypatch,
        adapter,
        _config(enabled=False, mode="auto"),
        candidates=[],
    )

    event_names = [event[0] for event in runtime.events]
    assert event_names.index("configure") < event_names.index("unregister")
    assert adapter._permission_rail is None
    assert adapter._enable_auto_permission is False
    assert runtime.registered == []
    assert runtime.live_callbacks == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "mode", "terminal_type"),
    [
        (True, "manual", _ManualRail),
        (False, "auto", type(None)),
    ],
)
async def test_reload_retains_auto_wrapper_until_permission_queue_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
    enabled: bool,
    mode: str,
    terminal_type: type,
) -> None:
    existing = _AutoRail("existing-auto")
    runtime = _ReloadRuntime([existing])
    adapter = _new_adapter(runtime, permission_rail=existing, auto_enabled=True)
    pending = [True]
    monkeypatch.setattr(
        adapter._root_permission_queue,
        "has_live",
        lambda **_kwargs: pending[0],
    )
    config = _config(enabled=enabled, mode=mode)

    await _reload(monkeypatch, adapter, config, candidates=[])

    assert adapter._permission_rail is existing
    assert adapter._enable_auto_permission is False
    assert existing.updated_configs[-1] == config["permissions"]
    assert runtime.registered == [existing]
    assert not [event for event in runtime.events if event[0] == "unregister"]

    pending[0] = False
    candidates: list[_PermissionRail] = []
    await _reload(monkeypatch, adapter, config, candidates=candidates)

    assert isinstance(adapter._permission_rail, terminal_type)
    assert existing not in runtime.registered


@pytest.mark.asyncio
async def test_registration_failure_never_overlaps_old_and_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=False)
    candidates: list[_PermissionRail] = []

    original_register = runtime.register_rail

    async def fail_new_candidate(rail: _PermissionRail) -> None:
        runtime.fail_register.add(rail)
        await original_register(rail)

    runtime.register_rail = fail_new_candidate  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="register failed"):
        await _reload(
            monkeypatch,
            adapter,
            _config(enabled=True, mode="auto"),
            candidates=candidates,
        )

    assert len(runtime.configured) == 1
    assert runtime.registered == []
    assert runtime.live_callbacks == []
    assert candidates[0] not in runtime.registered
    assert adapter._permission_rail is old
    assert adapter._enable_auto_permission is False


@pytest.mark.asyncio
async def test_configure_failure_removes_candidate_and_preserves_old_rail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    runtime.fail_configure = True
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=False)
    candidates: list[_PermissionRail] = []

    with pytest.raises(RuntimeError, match="configure failed"):
        await _reload(
            monkeypatch,
            adapter,
            _config(enabled=True, mode="auto"),
            candidates=candidates,
        )

    assert runtime.registered == [old]
    assert adapter._permission_rail is old
    assert adapter._enable_auto_permission is False


@pytest.mark.asyncio
async def test_configure_failure_does_not_touch_permission_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    runtime.fail_configure = True
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=False)
    candidates: list[_PermissionRail] = []
    with pytest.raises(RuntimeError, match="configure failed"):
        await _reload(
            monkeypatch,
            adapter,
            _config(enabled=True, mode="auto"),
            candidates=candidates,
        )

    assert adapter._permission_rail is old
    assert adapter._enable_auto_permission is False
    assert old in runtime.live_callbacks
    assert not [
        event for event in runtime.events if event[0] in {"register", "unregister"}
    ]


@pytest.mark.asyncio
async def test_failed_old_retirement_never_registers_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    runtime.fail_unregister.add(old)
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=False)
    candidates: list[_PermissionRail] = []

    with pytest.raises(RuntimeError, match="unregister failed"):
        await _reload(
            monkeypatch,
            adapter,
            _config(enabled=True, mode="auto"),
            candidates=candidates,
        )

    candidate = candidates[0]
    assert adapter._permission_rail is old
    assert adapter._enable_auto_permission is False
    assert old not in runtime.registered
    assert old in runtime.live_callbacks
    assert candidate not in runtime.registered
    assert candidate not in runtime.live_callbacks


@pytest.mark.asyncio
async def test_built_root_rejects_permission_delta_before_any_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = runtime
    adapter._permission_rail = old
    adapter._config_base_cache = _config(enabled=True, mode="manual")
    apply_snapshot = AsyncMock()
    monkeypatch.setattr(adapter, "_apply_reload_config_snapshot", apply_snapshot)

    with pytest.raises(RuntimeError, match="root_permission_reload_requires_restart"):
        await adapter.reload_agent_config(_config(enabled=True, mode="auto"), {})

    apply_snapshot.assert_not_awaited()
    assert runtime.registered == [old]
    assert runtime.live_callbacks == [old]
    assert adapter._permission_rail is old


@pytest.mark.asyncio
async def test_active_old_callback_retires_before_candidate_becomes_live(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _ManualRail("old-manual")
    runtime = _ReloadRuntime([old])
    runtime.wait_for_callback = old
    adapter = _new_adapter(runtime, permission_rail=old, auto_enabled=False)
    candidates: list[_PermissionRail] = []

    callback_task = asyncio.create_task(old.hold_callback())
    await old.callback_started.wait()
    reload_task = asyncio.create_task(
        _reload(
            monkeypatch,
            adapter,
            _config(enabled=True, mode="auto"),
            candidates=candidates,
        )
    )
    await runtime.unregister_entered.wait()

    assert old in runtime.live_callbacks
    assert candidates[0] not in runtime.live_callbacks
    old.callback_release.set()
    await callback_task
    await reload_task

    assert runtime.live_callbacks == [candidates[0]]


@pytest.mark.asyncio
async def test_locked_deep_agent_projection_keeps_only_registered_candidate() -> None:
    manual = build_permission_rail({"permissions": {"enabled": True, "mode": "manual"}})
    auto = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
    )
    assert isinstance(manual, PermissionInterruptRail)
    assert isinstance(auto, AutoPermissionInterruptRail)

    agent = DeepAgent(AgentCard(name="auto-permission-reload-projection"))
    permission_types = (PermissionInterruptRail, AutoPermissionInterruptRail)
    try:
        agent.configure(DeepAgentConfig(rails=[manual], auto_create_workspace=False))
        await agent.ensure_initialized()
        callback_event = agent._react_agent.agent_callback_manager._get_agent_event(
            AgentCallbackEvent.BEFORE_TOOL_CALL
        )
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 1
        await agent.register_rail(auto)
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 2

        agent.configure(
            DeepAgentConfig(
                rails=[],
                permissions=None,
                auto_create_workspace=False,
            )
        )
        assert agent.is_registered_rail(manual) is True
        assert agent.is_registered_rail(auto) is True
        assert agent.find_pending_rails_by_type(PermissionInterruptRail) == []
        assert agent.find_pending_rails_by_type(AutoPermissionInterruptRail) == []

        await agent.unregister_rail(manual)
        await agent.ensure_initialized()

        assert agent.find_rails_by_type(permission_types) == [auto]
        assert agent.is_registered_rail(auto) is True
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 1
    finally:
        await agent._agent_callback_manager.clear()
        if agent._react_agent is not None:
            await agent._react_agent.agent_callback_manager.clear()


def test_auto_builder_injects_exact_persist_and_reload_notifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    persist = MagicMock(return_value=True)
    notify = MagicMock()
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.rails.permissions.permissions_persist."
        "persist_exact_permission_allow_rule",
        persist,
    )

    manual = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "manual"}},
        permissions_changed_notifier=notify,
    )
    auto = build_permission_rail(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
        permissions_changed_notifier=notify,
    )

    assert isinstance(manual, PermissionInterruptRail)
    assert manual._exact_persist_callback is None
    assert isinstance(auto, AutoPermissionInterruptRail)
    exact_callback = auto.base_rail._exact_persist_callback
    assert callable(exact_callback)
    assert exact_callback("bash", {"command": "git status"}, ()) is True
    persist.assert_called_once_with("bash", {"command": "git status"}, ())
    notify.assert_called_once_with()



def test_deep_cold_auto_permission_rail_receives_current_dependencies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    adapter = JiuWenSwarmDeepAdapter()
    adapter.mark_as_session_scoped("session-a")
    adapter._sys_operation = MagicMock(name="sys_operation")
    adapter._permission_workspace_root = tmp_path / "project"
    adapter._platform_trusted_root = tmp_path / "agent-workspace"
    captured: list[dict[str, object]] = []

    def _build_permission_rail(*_args: object, **kwargs: object) -> object:
        captured.append(dict(kwargs))
        rail = object.__new__(AutoPermissionInterruptRail)
        rail.sys_operation = adapter._sys_operation
        return rail

    monkeypatch.setattr(interface_deep, "build_permission_rail", _build_permission_rail)
    monkeypatch.setattr(
        interface_deep, "_build_context_processor_rail", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        interface_deep,
        "load_hooks_config",
        lambda _config: SimpleNamespace(events=()),
    )
    for method_name in (
        "_build_runtime_prompt_rail",
        "_build_response_prompt_rail",
        "_build_multimodal_image_rail",
        "_build_task_planning_rail",
        "_build_security_rail",
        "_build_heartbeat_rail",
        "_build_circuit_breaker_rail",
        "_build_avatar_rail",
        "_build_skill_rail",
        "_build_skill_retrieval_prompt_rail",
        "_build_symphony_orchestration_rail",
        "_build_structured_ask_user_rail",
    ):
        monkeypatch.setattr(adapter, method_name, lambda **_kwargs: None)
    monkeypatch.setattr(adapter, "_filesystem_rail_enabled_for_profile", lambda: False)
    monkeypatch.setattr(adapter, "_skill_include_tools_for_profile", lambda: ())

    adapter._build_agent_rails(
        {},
        {"permissions": {"enabled": True, "mode": "auto"}},
        mode="agent.fast",
        composition_scope="single_agent",
    )

    assert len(captured) == 1
    assert captured[0]["enable_auto_permission"] is True
    assert captured[0]["trusted_search_urls"] is adapter._trusted_search_urls
    assert captured[0]["workspace_root"] == adapter._permission_workspace_root
    assert captured[0]["platform_trusted_root"] == adapter._platform_trusted_root


def test_reload_candidate_reuses_cold_workspace_authority(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    runtime = _ReloadRuntime()
    adapter = _new_adapter(runtime, permission_rail=None, auto_enabled=False)
    adapter._permission_workspace_root = tmp_path / "project"
    adapter._platform_trusted_root = tmp_path / "agent-workspace"
    captured: list[dict[str, object]] = []

    def build_candidate(*_args: object, **kwargs: object) -> _AutoRail:
        captured.append(dict(kwargs))
        return _AutoRail("candidate")

    monkeypatch.setattr(interface_deep, "build_permission_rail", build_candidate)
    monkeypatch.setattr(
        adapter,
        "_permission_rail_types",
        lambda: (_ManualRail, _AutoRail),
    )
    monkeypatch.setattr(
        adapter,
        "_reload_browser_runtime_security_profile",
        lambda: _ENFORCED_BROWSER_PROFILE,
    )

    adapter._prepare_permission_rail_candidate(
        {"permissions": {"enabled": True, "mode": "auto"}},
        enable_auto_permission=True,
        existing_rails=[],
    )

    assert captured[0]["workspace_root"] == adapter._permission_workspace_root
    assert captured[0]["platform_trusted_root"] == adapter._platform_trusted_root


def test_facade_propagates_permission_notifier_during_lazy_adapter_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter import interface

    adapter = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(interface, "resolve_sdk_choice", lambda: "deep")
    monkeypatch.setattr(interface, "create_adapter", lambda *_args, **_kwargs: adapter)

    facade = interface.JiuWenSwarm()
    facade.set_permissions_changed_notifier(notify)

    assert facade._ensure_adapter() is adapter
    adapter.set_permissions_changed_notifier.assert_called_once_with(notify)
