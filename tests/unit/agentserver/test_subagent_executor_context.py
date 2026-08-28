# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for request-local subagent executor isolation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.agentserver.tools.subagent_executor.globals import (
    get_fork_agent_executor,
    init_subagent_executor,
    reset_fork_agent_executor,
    set_fork_agent_executor,
)
from jiuwenclaw.agentserver.tools.subagent_tools import spawn_subagent


class _ExecutorResult:
    def __init__(self, user_id: str) -> None:
        self._user_id = user_id

    def model_dump(self) -> dict[str, str]:
        return {"executor_user_id": self._user_id}


class _FakeExecutor:
    def __init__(self, user_id: str) -> None:
        self.user_id = user_id

    async def execute_spawn(self, task: Any, parent_session: Any) -> _ExecutorResult:
        del task, parent_session
        return _ExecutorResult(self.user_id)


class _StopAfterExecutorBinding(Exception):
    """Stop the runtime update after request-local bindings are installed."""


def test_executor_binding_can_be_restored() -> None:
    original: Any = SimpleNamespace(name="original")
    replacement: Any = SimpleNamespace(name="replacement")

    original_token = set_fork_agent_executor(original)
    try:
        replacement_token = set_fork_agent_executor(replacement)
        assert get_fork_agent_executor() is replacement

        reset_fork_agent_executor(replacement_token)
        assert get_fork_agent_executor() is original
    finally:
        reset_fork_agent_executor(original_token)


@pytest.mark.asyncio
async def test_initialized_executor_supports_normal_and_child_task_invocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the pre-fix single-adapter flow working, including child tasks."""
    executor: Any = _FakeExecutor("normal-user")
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.subagent_executor.globals.ForkAgentExecutor",
        lambda *args, **kwargs: executor,
    )

    outer_token = set_fork_agent_executor(None)
    try:
        initialized = init_subagent_executor(
            parent_agent=SimpleNamespace(name="main-agent"),
            model=SimpleNamespace(name="model"),
        )
        assert initialized is executor
        assert get_fork_agent_executor() is executor

        direct_result = await spawn_subagent.invoke(
            {
                "objective": "normal request",
                "role_id": "MainAgent",
                "prompt": "",
            }
        )
        child_result = await asyncio.create_task(
            spawn_subagent.invoke(
                {
                    "objective": "normal child task",
                    "role_id": "MainAgent",
                    "prompt": "",
                }
            )
        )

        assert direct_result == {"executor_user_id": "normal-user"}
        assert child_result == {"executor_user_id": "normal-user"}
    finally:
        reset_fork_agent_executor(outer_token)


@pytest.mark.asyncio
async def test_concurrent_requests_keep_their_own_executor() -> None:
    user_520_executor: Any = SimpleNamespace(user_id="520")
    user_236_executor: Any = SimpleNamespace(user_id="236")
    user_520_bound = asyncio.Event()
    user_236_bound = asyncio.Event()

    async def user_520_request() -> object:
        token = set_fork_agent_executor(user_520_executor)
        try:
            user_520_bound.set()
            await user_236_bound.wait()
            return get_fork_agent_executor()
        finally:
            reset_fork_agent_executor(token)

    async def user_236_cron() -> object:
        await user_520_bound.wait()
        token = set_fork_agent_executor(user_236_executor)
        try:
            user_236_bound.set()
            await asyncio.sleep(0)
            return get_fork_agent_executor()
        finally:
            reset_fork_agent_executor(token)

    request_executor, cron_executor = await asyncio.gather(
        user_520_request(),
        user_236_cron(),
    )

    assert request_executor is user_520_executor
    assert cron_executor is user_236_executor


@pytest.mark.asyncio
async def test_runtime_config_binds_the_adapter_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the request setup binds its owning adapter before tool updates."""
    executor: Any = SimpleNamespace(user_id="520")
    adapter: Any = SimpleNamespace(
        _instance=object(),
        _workspace_dir="/workspace/user-520",
        _subagent_executor=executor,
        _runtime_prompt_rail=None,
        _resolve_runtime_language=lambda: "zh",
    )

    async def stop_after_binding(_: str) -> None:
        raise _StopAfterExecutorBinding

    adapter._update_rails_for_mode = stop_after_binding
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.cwd.set_cwd",
        lambda _: None,
    )

    outer_token = set_fork_agent_executor(None)
    try:
        with pytest.raises(_StopAfterExecutorBinding):
            await JiuWenClawDeepAdapter._update_runtime_config(
                adapter,
                SimpleNamespace(
                    session_id="session-520",
                    mode="agent.plan",
                    request_id=None,
                    channel_id=None,
                    request_metadata=None,
                    request_system_prompt=None,
                ),
            )
        assert get_fork_agent_executor() is executor
    finally:
        reset_fork_agent_executor(outer_token)


@pytest.mark.asyncio
async def test_spawn_subagent_uses_the_request_executor_after_concurrent_rebind() -> None:
    """Reproduce a chat paused while another user's cron binds its executor."""
    user_520_executor: Any = _FakeExecutor("520")
    user_236_executor: Any = _FakeExecutor("236")
    user_520_bound = asyncio.Event()
    user_236_bound = asyncio.Event()

    async def user_520_request() -> dict[str, str]:
        token = set_fork_agent_executor(user_520_executor)
        try:
            user_520_bound.set()
            await user_236_bound.wait()
            return await spawn_subagent.invoke(
                {
                    "objective": "run user 520 skill",
                    "role_id": "MainAgent",
                    "prompt": "",
                }
            )
        finally:
            reset_fork_agent_executor(token)

    async def user_236_cron() -> dict[str, str]:
        await user_520_bound.wait()
        token = set_fork_agent_executor(user_236_executor)
        try:
            user_236_bound.set()
            return await spawn_subagent.invoke(
                {
                    "objective": "run user 236 cron",
                    "role_id": "MainAgent",
                    "prompt": "",
                }
            )
        finally:
            reset_fork_agent_executor(token)

    request_result, cron_result = await asyncio.gather(
        user_520_request(),
        user_236_cron(),
    )

    assert request_result == {"executor_user_id": "520"}
    assert cron_result == {"executor_user_id": "236"}


@pytest.mark.asyncio
async def test_concurrent_adapter_runtime_updates_bind_their_own_executors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the production request setup before concurrent subagent calls."""
    user_520_executor: Any = _FakeExecutor("520")
    user_236_executor: Any = _FakeExecutor("236")
    user_520_bound = asyncio.Event()
    user_236_bound = asyncio.Event()

    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.cwd.set_cwd",
        lambda _: None,
    )

    def make_adapter(executor: Any, update_rails: Any) -> Any:
        return SimpleNamespace(
            _instance=object(),
            _workspace_dir=f"/workspace/user-{executor.user_id}",
            _subagent_executor=executor,
            _runtime_prompt_rail=None,
            _resolve_runtime_language=lambda: "zh",
            _update_rails_for_mode=update_rails,
        )

    async def user_520_rails(_: str) -> None:
        user_520_bound.set()
        await user_236_bound.wait()
        raise _StopAfterExecutorBinding

    async def user_236_rails(_: str) -> None:
        await user_520_bound.wait()
        user_236_bound.set()
        raise _StopAfterExecutorBinding

    async def run_request(adapter: Any, session_id: str) -> dict[str, str]:
        request = SimpleNamespace(
            session_id=session_id,
            mode="agent.plan",
            request_id=f"request-{session_id}",
            channel_id="web",
            request_metadata=None,
            request_system_prompt=None,
        )
        with pytest.raises(_StopAfterExecutorBinding):
            await JiuWenClawDeepAdapter._update_runtime_config(adapter, request)
        return await spawn_subagent.invoke(
            {
                "objective": f"run {session_id}",
                "role_id": "MainAgent",
                "prompt": "",
            }
        )

    user_520_result, user_236_result = await asyncio.gather(
        run_request(make_adapter(user_520_executor, user_520_rails), "520"),
        run_request(make_adapter(user_236_executor, user_236_rails), "236"),
    )

    assert user_520_result == {"executor_user_id": "520"}
    assert user_236_result == {"executor_user_id": "236"}
