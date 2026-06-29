# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def _make_adapter(**state: object) -> JiuWenSwarmDeepAdapter:
    """Create a bare adapter with internal state set via setattr."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    for name, value in state.items():
        setattr(adapter, name, value)
    return adapter


def test_other_active_sessions_treats_subagent_as_related() -> None:
    adapter = _make_adapter(
        _active_session_ids={
            "tui_main": 1,
            "tui_main_sub_explore": 1,
        },
    )

    assert getattr(adapter, "_other_active_sessions")("tui_main") == 0
    assert getattr(adapter, "_other_active_sessions")("tui_main_sub_explore") == 0


def test_other_active_sessions_counts_unrelated_sessions() -> None:
    adapter = _make_adapter(
        _active_session_ids={
            "tui_a": 1,
            "tui_b": 1,
        },
    )

    assert getattr(adapter, "_other_active_sessions")("tui_a") == 1


@pytest.mark.asyncio
async def test_cancel_session_agent_tasks_cancels_registered_task() -> None:
    adapter = _make_adapter(_session_agent_tasks={})
    cancelled = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(worker())
    getattr(adapter, "_session_agent_tasks")["sess_x"] = {task}
    await asyncio.sleep(0)

    cancelled_count = await getattr(adapter, "_cancel_session_agent_tasks")("sess_x")
    assert cancelled_count == 1
    await asyncio.wait_for(cancelled.wait(), timeout=2)


def test_is_session_live_when_deep_agent_stream_task_running() -> None:
    from unittest.mock import MagicMock

    instance = MagicMock()
    setattr(instance, "_invoke_active", True)
    stream_task = MagicMock()
    stream_task.done.return_value = False
    setattr(instance, "_stream_process_task", stream_task)
    loop_session = MagicMock()
    loop_session.get_session_id.return_value = "tui_main"
    setattr(instance, "_loop_session", loop_session)
    adapter = _make_adapter(
        _active_session_ids={},
        _session_agent_tasks={},
        _instance=instance,
    )

    assert getattr(adapter, "_is_session_live")("tui_main") is True
    assert getattr(adapter, "_other_active_sessions")("tui_other") == 1