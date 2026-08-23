"""Atomic request admission contracts for permission config reloads."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime import agent_manager as agent_manager_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


class _ManualRail:
    pass


class _AutoRail(_ManualRail):
    pass


def _auto_config() -> dict[str, object]:
    return {"permissions": {"enabled": True, "mode": "auto"}}


def _child(session_id: str = "session-a") -> JiuWenSwarmDeepAdapter:
    child = JiuWenSwarmDeepAdapter()
    child.mark_as_session_scoped(session_id)
    child._permission_rail = _ManualRail()
    child._permission_rail_types = lambda: (_ManualRail, _AutoRail)
    return child


def _parent_with_child(
    child: JiuWenSwarmDeepAdapter,
    session_id: str = "session-a",
) -> JiuWenSwarmDeepAdapter:
    parent = JiuWenSwarmDeepAdapter()
    parent._session_adapters[session_id] = child
    parent._session_adapter_versions[session_id] = 0
    return parent


def _request() -> AgentRequest:
    return AgentRequest(
        request_id="request-a",
        channel_id="web",
        session_id="session-a",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "continue", "mode": "agent"},
    )




@pytest.mark.asyncio
async def test_nonexternal_lookup_keeps_permission_version_pending() -> None:
    child = _child()
    child._is_session_live = lambda _session_id: False
    child.reload_agent_config = AsyncMock()
    parent = _parent_with_child(child)
    parent._mark_session_adapters_stale_for_reload(_auto_config(), {})

    await parent._reload_session_adapter_if_stale("session-a", child)

    child.reload_agent_config.assert_not_awaited()
    assert parent._session_adapter_versions["session-a"] == 0
    assert parent._pending_session_reload_config_base == _auto_config()


@pytest.mark.asyncio
async def test_external_input_installs_permission_when_child_is_idle() -> None:
    child = _child()
    child._is_session_live = lambda _session_id: False
    child.reload_agent_config = AsyncMock()
    parent = _parent_with_child(child)
    parent._mark_session_adapters_stale_for_reload(_auto_config(), {})

    await parent._reload_session_adapter_if_stale(
        "session-a",
        child,
        host_external_input=True,
    )

    child.reload_agent_config.assert_awaited_once()
    assert parent._session_adapter_versions["session-a"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_state", ["live", "pending"])
async def test_external_input_keeps_complete_old_epoch_when_boundary_is_unsafe(
    blocked_state: str,
) -> None:
    child = _child()
    child._is_session_live = lambda _session_id: blocked_state == "live"
    child.reload_agent_config = AsyncMock()
    if blocked_state == "pending":
        child._root_permission_queue.has_live = lambda **_kwargs: True
    parent = _parent_with_child(child)
    parent._mark_session_adapters_stale_for_reload(_auto_config(), {})

    await parent._reload_session_adapter_if_stale(
        "session-a",
        child,
        host_external_input=True,
    )

    child.reload_agent_config.assert_not_awaited()
    assert parent._session_adapter_versions["session-a"] == 0
    assert parent._pending_session_reload_config_base == _auto_config()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_permission_publication_failure_evicts_child_without_retrying_in_place(
    failure: BaseException,
) -> None:
    child = _child()
    child._is_session_live = lambda _session_id: False
    child.reload_agent_config = AsyncMock(side_effect=failure)
    child.cleanup = AsyncMock()
    parent = _parent_with_child(child)
    parent._mark_session_adapters_stale_for_reload(_auto_config(), {})

    with pytest.raises(type(failure)):
        await parent._reload_session_adapter_if_stale(
            "session-a",
            child,
            host_external_input=True,
        )

    assert "session-a" not in parent._session_adapters
    assert "session-a" not in parent._session_adapter_versions
    child.cleanup.assert_awaited_once()


def test_live_deep_agent_stream_defers_with_zero_activity_counter() -> None:
    child = _child()
    child._instance = SimpleNamespace(
        _invoke_active=True,
        _stream_process_task=SimpleNamespace(done=lambda: False),
        _loop_session=SimpleNamespace(get_session_id=lambda: "session-a"),
    )

    assert child._active_session_ids == {}
    assert child._should_defer_permission_reload(
        _auto_config(),
        session_id="session-a",
    )


def test_base_manual_pending_defers_after_request_is_no_longer_live() -> None:
    child = _child()
    child._permission_rail = _AutoRail()
    child._enable_auto_permission = False
    child._is_session_live = lambda _session_id: False
    child._root_permission_queue.has_live = lambda **_kwargs: True

    assert child._should_defer_permission_reload(
        _auto_config(),
        session_id="session-a",
    )


def test_auto_pending_defers_same_mode_rule_change_after_request_ends() -> None:
    child = _child()
    child._config_base_cache = {
        "permissions": {
            "enabled": True,
            "mode": "auto",
            "tools": {"bash": "ask"},
        }
    }
    child._is_session_live = lambda _session_id: False
    child._root_permission_queue.has_live = lambda **_kwargs: True
    candidate = {
        "permissions": {
            "enabled": True,
            "mode": "auto",
            "tools": {"bash": "allow"},
        }
    }

    assert child._should_defer_permission_reload(
        candidate,
        session_id="session-a",
    )


def test_base_pending_defers_same_mode_rule_change_after_request_ends() -> None:
    child = _child()
    child._config_base_cache = {
        "permissions": {
            "enabled": True,
            "mode": "manual",
            "tools": {"bash": "ask"},
        }
    }
    child._is_session_live = lambda _session_id: False
    child._root_permission_queue.has_live = lambda **_kwargs: True
    candidate = {
        "permissions": {
            "enabled": True,
            "mode": "manual",
            "tools": {"bash": "deny"},
        }
    }

    assert child._should_defer_permission_reload(
        candidate,
        session_id="session-a",
    )


def test_auto_pending_defers_auto_to_manual_after_request_ends() -> None:
    child = _child()
    child._config_base_cache = {"permissions": {"enabled": True, "mode": "auto"}}
    child._is_session_live = lambda _session_id: False
    child._root_permission_queue.has_live = lambda **_kwargs: True

    assert child._should_defer_permission_reload(
        {"permissions": {"enabled": True, "mode": "manual"}},
        session_id="session-a",
    )


def test_live_request_does_not_defer_when_permissions_are_unchanged() -> None:
    child = _child()
    current = {"enabled": True, "mode": "manual", "tools": {"bash": "ask"}}
    child._config_base_cache = {"permissions": current, "language": "zh"}
    child._is_session_live = lambda _session_id: True

    assert not child._should_defer_permission_reload(
        {"permissions": current, "language": "en"},
        session_id="session-a",
    )


@pytest.mark.asyncio
async def test_next_admission_installs_latest_root_pending_config() -> None:
    child = _child()
    child._config_base_cache = {
        "permissions": {"enabled": True, "mode": "manual", "epoch": "A"}
    }
    parent = _parent_with_child(child)
    parent._evict_idle_session_adapters = AsyncMock()
    installed: list[str] = []

    async def reload(config_base, *_args, **_kwargs) -> None:
        child._config_base_cache = dict(config_base)
        installed.append(config_base["permissions"]["epoch"])

    async def process(_request: AgentRequest, _inputs: dict) -> str:
        return child._config_base_cache["permissions"]["epoch"]

    child.reload_agent_config = reload
    child.process_message_impl = process
    parent._mark_session_adapters_stale_for_reload(
        {"permissions": {"enabled": True, "mode": "manual", "epoch": "B"}},
        {},
    )
    parent._mark_session_adapters_stale_for_reload(
        {"permissions": {"enabled": True, "mode": "manual", "epoch": "C"}},
        {},
    )

    assert await parent._process_message_impl(_request(), {}) == "C"
    assert installed == ["C"]
    assert parent._session_adapter_versions["session-a"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("retained_wrapper", [False, True])
async def test_request_first_reservation_defers_targeted_cutover(
    retained_wrapper: bool,
) -> None:
    child = _child()
    if retained_wrapper:
        child._permission_rail = _AutoRail()
        child._enable_auto_permission = False
    parent = _parent_with_child(child)
    entered = asyncio.Event()
    release = asyncio.Event()
    child.reload_agent_config = AsyncMock()
    parent._evict_idle_session_adapters = AsyncMock()

    async def process(_request: AgentRequest, _inputs: dict) -> str:
        entered.set()
        await release.wait()
        return "completed"

    child.process_message_impl = process
    request_task = asyncio.create_task(parent._process_message_impl(_request(), {}))
    await entered.wait()

    with pytest.raises(
        RuntimeError,
        match="permission_reload_deferred_manual_pending",
    ):
        await parent._reload_target_session_adapter(
            _auto_config(),
            {},
            target_session_id="session-a",
        )

    child.reload_agent_config.assert_not_awaited()
    release.set()
    assert await request_task == "completed"
    assert not child.is_session_active("session-a")

    await parent._reload_target_session_adapter(
        _auto_config(),
        {},
        target_session_id="session-a",
    )
    child.reload_agent_config.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_resume_turn_stays_manual_until_next_request() -> None:
    child = _child()
    child._permission_rail = _AutoRail()
    child._enable_auto_permission = False
    parent = _parent_with_child(child)
    pending = [True]
    observed_auto_state: list[bool] = []
    parent._evict_idle_session_adapters = AsyncMock()
    child._root_permission_queue.has_live = lambda **_kwargs: pending[0]

    async def reload(*_args, **_kwargs) -> None:
        child._enable_auto_permission = True

    async def process(_request: AgentRequest, _inputs: dict) -> str:
        observed_auto_state.append(child._enable_auto_permission)
        if pending[0]:
            pending[0] = False
            observed_auto_state.append(child._enable_auto_permission)
        return "completed"

    child.reload_agent_config = reload
    child.process_message_impl = process
    parent._mark_session_adapters_stale_for_reload(_auto_config(), {})

    assert await parent._process_message_impl(_request(), {}) == "completed"
    assert parent._session_adapter_versions["session-a"] == 0
    assert observed_auto_state == [False, False]

    assert await parent._process_message_impl(_request(), {}) == "completed"
    assert parent._session_adapter_versions["session-a"] == 1
    assert observed_auto_state == [False, False, True]


@pytest.mark.asyncio
async def test_retained_wrapper_stream_request_defers_targeted_cutover() -> None:
    child = _child()
    child._permission_rail = _AutoRail()
    child._enable_auto_permission = False
    parent = _parent_with_child(child)
    entered = asyncio.Event()
    release = asyncio.Event()
    child.reload_agent_config = AsyncMock()
    parent._evict_idle_session_adapters = AsyncMock()

    async def process_stream(_request: AgentRequest, _inputs: dict):
        entered.set()
        await release.wait()
        yield "chunk"

    child.process_message_stream_impl = process_stream
    stream = parent._process_message_stream_impl(_request(), {})
    first_chunk = asyncio.create_task(anext(stream))
    await entered.wait()

    with pytest.raises(
        RuntimeError,
        match="permission_reload_deferred_manual_pending",
    ):
        await parent._reload_target_session_adapter(
            _auto_config(),
            {},
            target_session_id="session-a",
        )

    child.reload_agent_config.assert_not_awaited()
    release.set()
    assert await first_chunk == "chunk"
    await stream.aclose()
    assert not child.is_session_active("session-a")


@pytest.mark.asyncio
async def test_reload_first_cutover_completes_before_request_admission() -> None:
    child = _child()
    parent = _parent_with_child(child)
    reload_entered = asyncio.Event()
    reload_release = asyncio.Event()
    parent._evict_idle_session_adapters = AsyncMock()

    async def reload(*_args, **_kwargs) -> None:
        reload_entered.set()
        await reload_release.wait()
        child._permission_rail = _AutoRail()

    async def process(_request: AgentRequest, _inputs: dict) -> bool:
        return isinstance(child._permission_rail, _AutoRail)

    child.reload_agent_config = reload
    child.process_message_impl = process
    reload_task = asyncio.create_task(
        parent._reload_target_session_adapter(
            _auto_config(),
            {},
            target_session_id="session-a",
        )
    )
    await reload_entered.wait()
    request_task = asyncio.create_task(parent._process_message_impl(_request(), {}))
    await asyncio.sleep(0)
    assert not request_task.done()

    reload_release.set()
    await reload_task

    assert await request_task is True
    assert not child.is_session_active("session-a")


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("failed"), asyncio.CancelledError()])
async def test_outer_reservation_balances_on_delegation_failure(
    failure: BaseException,
) -> None:
    child = _child()
    parent = _parent_with_child(child)
    evict_counts: list[int] = []

    async def process(_request: AgentRequest, _inputs: dict) -> None:
        raise failure

    async def evict() -> None:
        evict_counts.append(child._active_session_ids.get("session-a", 0))

    child.process_message_impl = process
    parent._evict_idle_session_adapters = evict

    with pytest.raises(type(failure)):
        await parent._process_message_impl(_request(), {})

    assert evict_counts == [0]
    assert not child.is_session_active("session-a")


@pytest.mark.asyncio
async def test_agent_manager_retries_identical_targeted_reload_after_deferral(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DeferringAgent:
        def __init__(self) -> None:
            self.pending = True
            self.calls = 0

        async def reload_agent_config(self, **_kwargs) -> None:
            self.calls += 1
            if self.pending:
                raise RuntimeError("permission_reload_deferred_manual_pending")

    class TeamManager:
        async def update_evolution_config(self, _config) -> None:
            return None

    manager = agent_manager_module.AgentManager()
    agent = DeferringAgent()
    manager.agents = {"web": {"agent": agent}}
    monkeypatch.setattr(
        agent_manager_module,
        "get_team_manager",
        lambda _channel_id: TeamManager(),
    )
    config = _auto_config()

    with pytest.raises(
        RuntimeError,
        match="permission_reload_deferred_manual_pending",
    ):
        await manager.reload_agents_config(
            config,
            {},
            target_channel_id="web",
            target_session_id="session-a",
        )

    assert manager._last_reload_fingerprint is None
    agent.pending = False
    await manager.reload_agents_config(
        config,
        {},
        target_channel_id="web",
        target_session_id="session-a",
    )

    assert agent.calls == 2
    assert manager._last_reload_fingerprint is not None
