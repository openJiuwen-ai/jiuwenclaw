# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

pytest.importorskip("markdown")

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey
from jiuwenclaw.agentserver.tools.deepresearch_task_manager import (
    DeepResearchTaskManagerPool,
    DeepResearchTaskRequest,
    get_deepresearch_manager,
)


@pytest.fixture(autouse=True)
def _reset_dr_pool():
    DeepResearchTaskManagerPool.reset_for_tests()
    yield
    DeepResearchTaskManagerPool.reset_for_tests()


@pytest.mark.asyncio
async def test_deepresearch_managers_are_tenant_isolated() -> None:
    a = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc1", "aid1"))
    b = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc2", "aid2"))
    a2 = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc1", "aid1"))

    assert a is a2
    assert a is not b
    assert a.service_id == "svc1"
    assert b.agent_id == "aid2"


@pytest.mark.asyncio
async def test_get_deepresearch_manager_rejects_none_scope() -> None:
    with pytest.raises(TypeError, match="non-None scope"):
        await get_deepresearch_manager(None)


@pytest.mark.asyncio
async def test_deepresearch_pool_remove_shutdown() -> None:
    mgr = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc", "aid"))
    cancelled: list[bool] = []
    started = asyncio.Event()

    async def _runner():
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise

    handle = asyncio.create_task(_runner())
    await started.wait()
    mgr._task_handles["t1"] = handle
    mgr._tasks["t1"] = MagicMock(cancel_event=None)

    assert await DeepResearchTaskManagerPool.remove("svc", "aid") is True
    assert await DeepResearchTaskManagerPool.remove("svc", "aid") is False
    assert cancelled == [True]
    assert mgr._tasks == {}
    assert mgr._task_handles == {}

    again = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc", "aid"))
    assert again is not mgr


@pytest.mark.asyncio
async def test_get_instance_uses_explicit_default_tenant() -> None:
    from jiuwenclaw.agentserver.tools.deepresearch_task_manager import DeepResearchTaskManager

    with pytest.warns(DeprecationWarning, match="get_instance\\(\\) is deprecated"):
        legacy = await DeepResearchTaskManager.get_instance()
    explicit = await get_deepresearch_manager(
        RuntimeScopeKey.from_ids("default", "default")
    )
    assert legacy is explicit
    assert legacy.service_id == "default"
    assert legacy.agent_id == "default"


@pytest.mark.asyncio
async def test_create_task_captures_env_snapshot(monkeypatch) -> None:
    from jiuwenclaw.agentserver.tools import deepresearch_task_manager as drm

    mgr = await get_deepresearch_manager(RuntimeScopeKey.from_ids("svc", "aid"))

    monkeypatch.setattr(
        drm,
        "build_effective_env_overlay",
        lambda *a, **k: {"API_KEY": "tenant-secret", "MODEL_NAME": "m"},
    )
    monkeypatch.setattr(drm, "get_task_env_overlay", lambda: None)
    monkeypatch.setattr(drm, "get_effective_request_workspace_dir", lambda: "/tmp/ws")

    async def _noop_execute(*args, **kwargs):
        await asyncio.sleep(0)

    monkeypatch.setattr(mgr, "_execute_task", _noop_execute)

    task_id = await mgr.create_task(
        DeepResearchTaskRequest(
            query="q",
            file_name="report",
            session_id="sess-1",
            service_id="svc",
            agent_id="aid",
        )
    )
    task = mgr._tasks[task_id]
    assert task.env_snapshot["API_KEY"] == "tenant-secret"
    assert task.workspace_dir == "/tmp/ws"
    assert task.service_id == "svc"
    assert task.agent_id == "aid"
