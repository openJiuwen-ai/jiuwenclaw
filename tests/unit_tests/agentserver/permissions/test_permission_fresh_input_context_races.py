from __future__ import annotations

import ast
import asyncio
import inspect
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue import (
    RootPermissionQueueError,
)
from jiuwenswarm.server.runtime.agent_manager import AgentManager


class _ObservedLock:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self.attempts = 0
        self.second_attempt = asyncio.Event()

    async def acquire(self) -> None:
        self.attempts += 1
        if self.attempts >= 2:
            self.second_attempt.set()
        await self._lock.acquire()

    def release(self) -> None:
        self._lock.release()

    def locked(self) -> bool:
        return self._lock.locked()

    async def __aenter__(self):
        self.attempts += 1
        if self.attempts >= 2:
            self.second_attempt.set()
        await self._lock.acquire()
        return self

    async def __aexit__(self, _exc_type, _exc, _traceback) -> None:
        self._lock.release()


def _host() -> tuple[
    AgentManager, JiuWenSwarmDeepAdapter, JiuWenSwarmDeepAdapter
]:
    manager = AgentManager()
    root = JiuWenSwarmDeepAdapter()
    child = JiuWenSwarmDeepAdapter()
    root._session_adapters["session-1"] = child
    root._session_adapter_versions["session-1"] = 0
    root.set_permissions_external_input_context_builder(
        manager.build_permissions_external_input_context
    )
    return manager, root, child


def _external_request(request_id: str = "external-1") -> AgentRequest:
    return AgentRequest(
        request_id=request_id,
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "start", "mode": "agent"},
    )


def _permission_resume_request(channel_id: str) -> AgentRequest:
    return AgentRequest(
        request_id=f"resume-{channel_id}",
        channel_id=channel_id,
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "query": "resume",
            "mode": "agent",
            "source": "permission_interrupt",
            "request_id": "permission-card",
            "answers": [{"approved": True}],
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", ["web", "tui", "cli"])
async def test_permission_resume_reuses_exact_session_adapter(
    channel_id: str,
) -> None:
    root = JiuWenSwarmDeepAdapter()
    child = JiuWenSwarmDeepAdapter()
    root._session_adapters["session-1"] = child

    selected = await root._get_session_adapter_for_request(
        _permission_resume_request(channel_id),
        reserve_activity=False,
    )

    assert selected is child
    assert root._session_adapters == {"session-1": child}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", ["web", "tui", "cli"])
async def test_permission_resume_without_original_adapter_fails_closed(
    channel_id: str,
) -> None:
    root = JiuWenSwarmDeepAdapter()

    with pytest.raises(
        RootPermissionQueueError,
        match="permission_resume_owner_missing",
    ):
        await root._get_session_adapter_for_request(
            _permission_resume_request(channel_id),
            reserve_activity=False,
        )

    assert root._session_adapters == {}


@pytest.mark.asyncio
async def test_three_reloads_install_only_latest_config() -> None:
    manager, root, child = _host()
    snapshots = [{"permissions": {"marker": marker}} for marker in ("B", "C", "D")]
    installed = []

    async def publish_reload(*_args, **_kwargs) -> None:
        root._mark_session_adapters_stale_for_reload(snapshots.pop(0), None)

    async def install_config(config, _env, **_kwargs) -> None:
        installed.append(config["permissions"]["marker"])
        child._config_base_cache = config

    manager.reload_agents_config = publish_reload
    child._config_base_cache = {"permissions": {"marker": "A"}}
    child._has_permission_config_delta = lambda _config: True
    child._should_defer_permission_reload = lambda *_args, **_kwargs: False
    child.reload_agent_config = install_config

    tails = [manager.schedule_permissions_reload() for _ in range(3)]
    await tails[-1]
    selected = await root._get_session_adapter_for_request(
        _external_request("external-D"),
        reserve_activity=True,
    )

    assert installed == ["D"]
    assert root._session_adapter_versions["session-1"] == 3
    assert selected is child
    child._unregister_session_agent_task("session-1")


@pytest.mark.asyncio
async def test_targeted_permission_reload_advances_one_global_lazy_version() -> None:
    root = JiuWenSwarmDeepAdapter()
    root._session_adapters = {
        "session-a": JiuWenSwarmDeepAdapter(),
        "session-b": JiuWenSwarmDeepAdapter(),
    }
    targeted = AsyncMock()
    root._reload_target_session_adapter = targeted
    latest = {"permissions": {"enabled": True, "mode": "auto"}}

    await root._fan_out_reload_to_session_adapters(
        latest,
        None,
        "session-a",
        permission_delta=True,
    )

    targeted.assert_not_awaited()
    assert root._session_adapter_config_version == 1
    assert root._pending_session_reload_config_base == latest
    assert root._session_adapter_versions == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure", [RuntimeError("update failed"), asyncio.CancelledError()]
)
async def test_permission_reload_failure_evicts_exact_child_before_cleanup(
    failure: BaseException,
) -> None:
    root = JiuWenSwarmDeepAdapter()
    child = JiuWenSwarmDeepAdapter()
    child.mark_as_session_scoped("session-1")
    root._session_adapters["session-1"] = child
    root._session_adapter_versions["session-1"] = 0
    root._session_adapter_config_version = 1
    root._pending_session_reload_config_base = {
        "permissions": {"enabled": True, "mode": "auto"}
    }
    child._has_permission_config_delta = lambda _config: True
    child._should_defer_permission_reload = lambda *_args, **_kwargs: False
    observed = []

    async def fail_reload(*_args, **_kwargs) -> None:
        raise failure

    async def cleanup() -> None:
        observed.append(root._session_adapters.get("session-1"))

    child.reload_agent_config = fail_reload
    child.cleanup = cleanup
    lock = root._session_adapter_locks.setdefault("session-1", asyncio.Lock())

    async with lock:
        with pytest.raises(type(failure)):
            await root._reload_session_adapter_if_stale(
                "session-1",
                child,
                host_external_input=True,
            )
        assert root._session_adapter_locks["session-1"] is lock

    assert "session-1" not in root._session_adapters
    assert observed == [None]


@pytest.mark.asyncio
async def test_nonfresh_lookup_never_installs_pending_permission_config() -> None:
    root = JiuWenSwarmDeepAdapter()
    child = JiuWenSwarmDeepAdapter()
    root._session_adapter_config_version = 1
    root._pending_session_reload_config_base = {
        "permissions": {"enabled": True, "mode": "auto"}
    }
    child._has_permission_config_delta = lambda _config: True
    child.reload_agent_config = AsyncMock()

    await root._reload_session_adapter_if_stale("session-1", child)

    child.reload_agent_config.assert_not_awaited()
    assert root._session_adapter_versions.get("session-1", 0) == 0


@pytest.mark.asyncio
async def test_busy_child_keeps_complete_installed_epoch_and_latest_pending() -> None:
    manager, root, child = _host()
    root._session_adapter_config_version = 1
    root._pending_session_reload_config_base = {
        "permissions": {"enabled": True, "mode": "auto"}
    }
    child._has_permission_config_delta = lambda _config: True
    child.reload_agent_config = AsyncMock()
    child._register_session_agent_task("session-1")

    selected = await root._get_session_adapter_for_request(
        _external_request("follow-up"),
        reserve_activity=True,
    )

    assert selected is child
    child.reload_agent_config.assert_not_awaited()
    assert root._session_adapter_versions["session-1"] == 0
    assert root._pending_session_reload_config_base is not None
    assert child.is_session_active("session-1")
    child._unregister_session_agent_task("session-1")
    assert not child.is_session_active("session-1")


@pytest.mark.parametrize(
    ("params", "metadata", "channel_id", "method", "expected"),
    [
        ({"query": "new"}, {}, "web", ReqMethod.CHAT_SEND, True),
        (
            {"query": "follow", "input_mode": "follow_up"},
            {},
            "web",
            ReqMethod.CHAT_SEND,
            True,
        ),
        (
            {"query": "steer", "input_mode": "steer"},
            {},
            "web",
            ReqMethod.CHAT_SEND,
            True,
        ),
        # Preserve develop Permission Engine timing: TUI control text is still
        # an external CHAT_SEND update opportunity.  The safe boundary protects
        # live Permission callbacks/transactions, not a Goal-wide policy epoch.
        ({"query": "/goal resume"}, {}, "tui", ReqMethod.CHAT_SEND, True),
        (
            {
                "query": "answer",
                "source": "permission_interrupt",
                "request_id": "approval-1",
                "answers": [{"approved": True}],
            },
            {},
            "web",
            ReqMethod.CHAT_SEND,
            False,
        ),
        (
            {
                "query": "answer",
                "source": "ask_user_interrupt",
                "request_id": "ask-1",
                "answers": [{"answer": "yes"}],
            },
            {},
            "web",
            ReqMethod.CHAT_SEND,
            False,
        ),
        (
            {
                "query": "approve",
                "source": "confirm_interrupt",
                "request_id": "plan-1",
                "approved": True,
            },
            {},
            "web",
            ReqMethod.CHAT_SEND,
            False,
        ),
        ({"query": "retry"}, {"skip_a2ui": True}, "web", ReqMethod.CHAT_SEND, False),
        ({"query": "tick"}, {}, "cron", ReqMethod.CHAT_SEND, False),
        ({"query": "tick"}, {}, "heartbeat", ReqMethod.CHAT_SEND, False),
        (
            {"query": "internal", "source": "internal_dispatch"},
            {},
            "web",
            ReqMethod.CHAT_SEND,
            False,
        ),
        ({"query": "status"}, {}, "web", ReqMethod.COMMAND_STATUS, False),
        ({"query": "resume"}, {}, "web", ReqMethod.COMMAND_GOAL, False),
    ],
)
def test_host_permission_update_input_contract(
    params,
    metadata,
    channel_id,
    method,
    expected,
) -> None:
    request = AgentRequest(
        request_id="request-1",
        channel_id=channel_id,
        session_id="session-1",
        req_method=method,
        params=params,
        metadata=metadata,
    )

    assert JiuWenSwarmDeepAdapter._is_host_permission_update_input(request) is expected


@pytest.mark.asyncio
async def test_reload_registered_during_cutover_waits_until_enqueue() -> None:
    manager, root, child = _host()
    manager._reload_lock = _ObservedLock()
    install_started = asyncio.Event()
    install_release = asyncio.Event()
    tail_acquired = asyncio.Event()

    async def install_config(*_args, **_kwargs) -> None:
        install_started.set()
        await install_release.wait()

    child._has_permission_config_delta = lambda _config: True
    child._should_defer_permission_reload = lambda *_args, **_kwargs: False
    child.reload_agent_config = install_config
    root._mark_session_adapters_stale_for_reload({"permissions": {"marker": "B"}}, None)
    select = asyncio.create_task(
        root._get_session_adapter_for_request(
            _external_request("external-B"),
            reserve_activity=True,
        )
    )
    await install_started.wait()

    async def later_reload(*_args, **_kwargs) -> None:
        async with manager._reload_lock:
            assert install_release.is_set()
            tail_acquired.set()

    manager.reload_agents_config = later_reload
    tail = manager.schedule_permissions_reload()
    await manager._reload_lock.second_attempt.wait()
    assert tail_acquired.is_set() is False
    assert not child.is_session_active("session-1")

    install_release.set()
    await select
    await tail
    assert tail_acquired.is_set()
    child._unregister_session_agent_task("session-1")


@pytest.mark.asyncio
async def test_partial_reload_failure_enqueues_nothing() -> None:
    manager, root, child = _host()
    installs = 0

    async def partial_failure(*_args, **_kwargs) -> None:
        root._mark_session_adapters_stale_for_reload(
            {"permissions": {"marker": "partial"}}, None
        )
        raise RuntimeError("reload failed after partial publication")

    async def install_config(*_args, **_kwargs) -> None:
        nonlocal installs
        installs += 1

    manager.reload_agents_config = partial_failure
    child.reload_agent_config = install_config
    manager.schedule_permissions_reload()

    with pytest.raises(RuntimeError, match="partial publication"):
        await root._get_session_adapter_for_request(
            _external_request("external-partial"),
            reserve_activity=True,
        )

    assert root._pending_session_reload_config_base is not None
    assert installs == 0
    assert child._active_session_ids.get("session-1", 0) == 0


@pytest.mark.asyncio
async def test_cancelled_session_lock_wait_releases_and_retries() -> None:
    manager, root, child = _host()
    session_lock = _ObservedLock()
    await session_lock.acquire()
    root._session_adapter_locks["session-1"] = session_lock

    select = asyncio.create_task(
        root._get_session_adapter_for_request(
            _external_request("cancelled"),
            reserve_activity=True,
        )
    )
    await session_lock.second_attempt.wait()
    select.cancel()
    with pytest.raises(asyncio.CancelledError):
        await select

    assert manager._reload_lock.locked() is False
    assert child._active_session_ids.get("session-1", 0) == 0
    session_lock.release()

    selected = await root._get_session_adapter_for_request(
        _external_request("retry"),
        reserve_activity=True,
    )
    assert selected is child
    child._unregister_session_agent_task("session-1")


@pytest.mark.asyncio
async def test_publication_error_preserves_error_and_releases_locks() -> None:
    manager, root, child = _host()
    expected = RuntimeError("publication failed")

    async def fail_reload(*_args, **_kwargs) -> None:
        raise expected

    root._reload_session_adapter_if_stale = fail_reload
    with pytest.raises(RuntimeError) as raised:
        await root._get_session_adapter_for_request(
            _external_request("broken"),
            reserve_activity=True,
        )

    assert raised.value is expected
    assert manager._reload_lock.locked() is False
    assert root._session_adapter_locks["session-1"].locked() is False
    assert child._active_session_ids.get("session-1", 0) == 0


def test_reload_and_external_install_call_graph_does_not_reenter_interaction() -> None:
    tree = ast.parse(inspect.getsource(JiuWenSwarmDeepAdapter))
    class_node = next(node for node in tree.body if isinstance(node, ast.ClassDef))
    methods = {
        node.name: node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    pending = [
        "reload_agent_config",
        "_reload_session_adapter_if_stale",
    ]
    visited = set()
    forbidden_interaction = {
        "send_input",
        "attach_output",
        "detach_output",
        "start",
        "stop",
    }
    forbidden_goal = {
        "get",
        "set",
        "pause",
        "resume",
        "clear",
        "begin_attempt",
        "accumulate_usage",
        "apply_assessment",
    }
    goal_manager_accessors = {"goal_manager", "_get_goal_manager"}
    hits = []

    def attribute_names(node) -> set[str]:
        if node is None:
            return set()
        return {part.attr for part in ast.walk(node) if isinstance(part, ast.Attribute)}

    while pending:
        name = pending.pop()
        if name in visited or name not in methods:
            continue
        visited.add(name)
        goal_aliases = {
            target.id
            for assignment in ast.walk(methods[name])
            if isinstance(assignment, (ast.Assign, ast.AnnAssign))
            for target in (
                assignment.targets
                if isinstance(assignment, ast.Assign)
                else [assignment.target]
            )
            if isinstance(target, ast.Name)
            and goal_manager_accessors & attribute_names(assignment.value)
        }
        for node in ast.walk(methods[name]):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            receiver = node.func.value
            receiver_attrs = attribute_names(receiver)
            receiver_is_goal = (
                bool(goal_manager_accessors & receiver_attrs)
                or isinstance(receiver, ast.Name)
                and receiver.id in goal_aliases
            )
            if node.func.attr in forbidden_interaction or (
                receiver_is_goal and node.func.attr in forbidden_goal
            ):
                hits.append((name, node.func.attr, node.lineno))
            if (
                isinstance(node.func.value, ast.Name)
                and node.func.value.id == "self"
                and node.func.attr in methods
            ):
                pending.append(node.func.attr)

    assert hits == []
