# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for request-local subagent executor isolation."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from jiuwenclaw.agentserver.tools.subagent_executor.globals import (
    get_fork_agent_executor,
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
