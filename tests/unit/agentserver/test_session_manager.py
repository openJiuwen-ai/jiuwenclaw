# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio

import pytest

from jiuwenclaw.agentserver.session_manager import SessionManager


async def _shutdown_processors(manager: SessionManager) -> None:
    processors = list(manager._session_processors.values())
    for processor in processors:
        processor.cancel()
    if processors:
        await asyncio.gather(*processors, return_exceptions=True)


def test_submit_task_once_atomically_rejects_duplicate_key() -> None:
    async def scenario() -> None:
        manager = SessionManager()
        release = asyncio.Event()
        calls = 0

        async def task() -> None:
            nonlocal calls
            calls += 1
            await release.wait()

        accepted = await asyncio.gather(*(
            manager.submit_task_once("session-1", "permission-1", task)
            for _ in range(3)
        ))

        assert accepted.count(True) == 1
        assert accepted.count(False) == 2
        release.set()
        await asyncio.sleep(0)
        assert calls == 1
        await _shutdown_processors(manager)

    asyncio.run(scenario())


def test_submit_task_once_keeps_different_keys_independent() -> None:
    async def scenario() -> None:
        manager = SessionManager()
        calls: list[str] = []
        completed = asyncio.Event()

        async def task(value: str) -> None:
            calls.append(value)
            if len(calls) == 2:
                completed.set()

        accepted = await asyncio.gather(
            manager.submit_task_once(
                "session-1", "permission-a", lambda: task("a")
            ),
            manager.submit_task_once(
                "session-1", "permission-b", lambda: task("b")
            ),
        )

        assert accepted == [True, True]
        await asyncio.wait_for(completed.wait(), timeout=1)
        assert sorted(calls) == ["a", "b"]
        await _shutdown_processors(manager)

    asyncio.run(scenario())


def test_submit_and_wait_once_returns_duplicate_without_rerunning() -> None:
    async def scenario() -> None:
        manager = SessionManager()
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def task() -> str:
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return "done"

        first = asyncio.create_task(
            manager.submit_and_wait_once("session-1", "permission-1", task)
        )
        await started.wait()

        duplicate = await manager.submit_and_wait_once(
            "session-1", "permission-1", task
        )
        assert duplicate == (False, None)

        release.set()
        assert await first == (True, "done")
        assert calls == 1
        await _shutdown_processors(manager)

    asyncio.run(scenario())


def test_submit_task_once_releases_key_when_enqueue_fails(monkeypatch) -> None:
    async def scenario() -> None:
        manager = SessionManager()
        attempts = 0

        async def submit_task(_session_id, _task_func) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("queue unavailable")

        monkeypatch.setattr(manager, "submit_task", submit_task)

        with pytest.raises(RuntimeError, match="queue unavailable"):
            await manager.submit_task_once(
                "session-1", "permission-1", lambda: asyncio.sleep(0)
            )

        assert await manager.submit_task_once(
            "session-1", "permission-1", lambda: asyncio.sleep(0)
        )

    asyncio.run(scenario())


def test_submit_task_once_releases_queued_key_when_processor_exits() -> None:
    async def scenario() -> None:
        manager = SessionManager()
        blocker_started = asyncio.Event()
        task_completed = asyncio.Event()
        calls = 0

        async def blocker() -> None:
            blocker_started.set()
            await asyncio.Event().wait()

        async def task() -> None:
            nonlocal calls
            calls += 1
            task_completed.set()

        await manager.submit_task("session-1", blocker)
        await blocker_started.wait()
        processor = manager._session_processors["session-1"]

        assert await manager.submit_task_once(
            "session-1", "permission-1", task
        )

        await manager.cancel_session_task("session-1")
        await processor

        assert await manager.submit_task_once(
            "session-1", "permission-1", task
        )
        await asyncio.wait_for(task_completed.wait(), timeout=1)
        assert calls == 1
        await _shutdown_processors(manager)

    asyncio.run(scenario())
