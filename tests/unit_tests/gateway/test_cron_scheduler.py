"""Unit tests for CronSchedulerService: store file deletion and event validation bugs."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from jiuwenswarm.gateway.cron.models import CronJob, CronRunState
from jiuwenswarm.gateway.cron.scheduler import CronSchedulerService, _Event
from jiuwenswarm.gateway.cron.store import CronJobStore


# ── Helpers ──────────────────────────────────────────────────────────────────

class _TestableScheduler(CronSchedulerService):
    """Subclass that exposes protected members as public methods.

    G.CLS.11 forbids accessing protected members from outside the class
    hierarchy. By subclassing, we can access them legitimately and then
    expose thin public wrappers for test assertions — no source changes needed.
    """

    async def check_store_changed(self):
        # Delegate to protected method from within the subclass.
        return await self._check_store_changed()

    async def handle_event(self, ev):
        return await self._handle_event(ev)

    @property
    def jobs(self):
        return self._jobs

    @property
    def last_store_mtime(self):
        return self._last_store_mtime

    @property
    def runs(self):
        return self._runs

    @property
    def run_tasks(self):
        """Expose _run_tasks for test assertions (G.CLS.11: access via subclass property)."""
        return self._run_tasks

    def schedule_event(self, at_dt, kind, job_id, run_id):
        """Expose _schedule_event for test use (G.CLS.11: access via subclass wrapper)."""
        return self._schedule_event(at_dt, kind, job_id, run_id)

    @property
    def events(self):
        """Expose _events for test assertions (G.CLS.11: access via subclass property)."""
        return self._events


def _make_job(job_id="job-1", name="test", **overrides):
    """Build a CronJob with sensible defaults for testing."""
    defaults = {
        "id": job_id,
        "name": name,
        "enabled": True,
        "expired": False,
        "cron_expr": "0 0 9 * * ? *",
        "timezone": "Asia/Shanghai",
        "wake_offset_seconds": 300,
        "description": "reminder",
        "targets": "tui",
        "created_at": time.time(),
        "updated_at": time.time(),
    }
    defaults.update(overrides)
    return CronJob(**defaults)


class FakeAgentClient:
    """Stub AgentServerClient that never calls a real agent."""

    async def send_request(self, *a, **kw):
        return {"content": {"output": "done", "result_type": "answer"}}


class FakeMessageHandler:
    """Stub MessageHandler that records published messages."""

    def __init__(self):
        self.published = []

    async def publish_robot_messages(self, msg):
        self.published.append(msg)


async def _create_one_job(store, name="job", targets="tui"):
    """Convenience: create a single cron job via the store."""
    return await store.create_job(
        name=name,
        cron_expr="0 0 9 * * ? *",
        timezone="Asia/Shanghai",
        description="reminder",
        targets=targets,
    )


def _make_scheduler(store, handler=None):
    """Build a _TestableScheduler with fake deps for testing."""
    return _TestableScheduler(
        store=store,
        agent_client=FakeAgentClient(),
        message_handler=handler or FakeMessageHandler(),
    )


# ── _check_store_changed ─────────────────────────────────────────────────────


class TestCheckStoreChanged:
    """_check_store_changed detects file deletion, modification, recreation."""

    @pytest.mark.asyncio
    async def test_file_deleted_triggers_reload(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        await _create_one_job(store)
        assert store_file.exists()

        svc = _make_scheduler(store)
        await svc.reload()
        assert svc.last_store_mtime != 0.0
        assert len(svc.jobs) == 1

        # Delete file -> mtime becomes 0.0
        store_file.unlink()
        assert not store_file.exists()

        changed = await svc.check_store_changed()
        assert changed is True
        assert len(svc.jobs) == 0

    @pytest.mark.asyncio
    async def test_file_modified_triggers_reload(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        await _create_one_job(store, name="job-1")

        svc = _make_scheduler(store)
        await svc.reload()

        # Modify file externally via second store
        store2 = CronJobStore(path=store_file)
        await _create_one_job(store2, name="job-2", targets="web")

        changed = await svc.check_store_changed()
        assert changed is True
        assert len(svc.jobs) == 2

    @pytest.mark.asyncio
    async def test_file_recreated_triggers_reload(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        await _create_one_job(store)

        svc = _make_scheduler(store)
        await svc.reload()

        # Delete -> triggers first reload -> mtime becomes 0.0
        store_file.unlink()
        changed1 = await svc.check_store_changed()
        assert changed1 is True
        assert len(svc.jobs) == 0

        # Recreate with a new job
        store3 = CronJobStore(path=store_file)
        await _create_one_job(store3, name="new-job", targets="web")

        changed2 = await svc.check_store_changed()
        assert changed2 is True
        assert len(svc.jobs) == 1
        assert "new-job" in [j.name for j in svc.jobs.values()]

    @pytest.mark.asyncio
    async def test_no_change_does_not_reload(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        await _create_one_job(store)

        svc = _make_scheduler(store)
        await svc.reload()

        changed = await svc.check_store_changed()
        assert changed is False
        assert len(svc.jobs) == 1

    @pytest.mark.asyncio
    async def test_never_had_file_does_not_reload(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        # File never created
        store = CronJobStore(path=store_file)

        svc = _make_scheduler(store)
        await svc.reload()
        assert svc.last_store_mtime == 0.0

        changed = await svc.check_store_changed()
        assert changed is False


# ── _handle_event ────────────────────────────────────────────────────────────


class TestHandleEventStoreValidation:
    """_handle_event skips wake/push when job absent from store."""

    @pytest.mark.asyncio
    async def test_wake_skipped_when_job_absent_from_store(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()
        assert len(svc.jobs) == 1

        # Delete file -> store.get_job returns None
        store_file.unlink()

        ev = _Event(at_ts=time.time(), seq=1, kind="wake", job_id=job.id, run_id=f"{job.id}:1234")
        await svc.handle_event(ev)

        # Reload clears memory; wake not executed; no messages published
        assert len(svc.jobs) == 0
        assert len(handler.published) == 0

    @pytest.mark.asyncio
    async def test_push_skipped_when_job_absent_from_store(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        store_file.unlink()

        ev = _Event(at_ts=time.time(), seq=1, kind="push", job_id=job.id, run_id=f"{job.id}:1234")
        await svc.handle_event(ev)

        assert len(svc.jobs) == 0
        assert len(handler.published) == 0

    @pytest.mark.asyncio
    async def test_push_update_skipped_when_job_absent_from_store(self, tmp_path):
        # When cron_jobs.json is deleted, push_update should also be skipped.
        # Continuing to push results for a job that no longer exists in the store
        # creates "ghost tasks" — the user sees /cron showing no tasks but
        # messages are still being pushed, and there's no job_id to delete them.
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        # Simulate a completed run with a result to deliver
        run_id = f"{job.id}:1234"
        svc.runs[run_id] = CronRunState(
            run_id=run_id,
            job_id=job.id,
            wake_at_iso="2026-06-09T08:55:00+08:00",
            push_at_iso="2026-06-09T09:00:00+08:00",
            job_name=job.name,
            targets=job.targets,
            session_id=None,
            chat_type=None,
            timezone=job.timezone,
            result_text="result: 9am now",
        )

        # Delete file — job gone from store
        store_file.unlink()

        ev = _Event(at_ts=time.time(), seq=1, kind="push_update", job_id=job.id, run_id=run_id)
        await svc.handle_event(ev)

        # push_update should be skipped: no ghost pushes for absent jobs
        assert len(handler.published) == 0

    @pytest.mark.asyncio
    async def test_push_update_delivered_when_job_present_in_store(self, tmp_path):
        # push_update should proceed normally when the job still exists in the store.
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        # Simulate a completed run with a result to deliver
        run_id = f"{job.id}:1234"
        svc.runs[run_id] = CronRunState(
            run_id=run_id,
            job_id=job.id,
            wake_at_iso="2026-06-09T08:55:00+08:00",
            push_at_iso="2026-06-09T09:00:00+08:00",
            job_name=job.name,
            targets=job.targets,
            session_id=None,
            chat_type=None,
            timezone=job.timezone,
            result_text="result: 9am now",
        )

        # Store file still exists
        ev = _Event(at_ts=time.time(), seq=1, kind="push_update", job_id=job.id, run_id=run_id)
        await svc.handle_event(ev)

        # push_update delivered successfully
        assert len(handler.published) == 1

    @pytest.mark.asyncio
    async def test_wake_executes_normally_when_job_present(self, tmp_path):
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        svc = _make_scheduler(store)
        await svc.reload()

        wake_called = False

        async def _mock_on_wake(self, j, r):
            nonlocal wake_called
            wake_called = True

        # patch.object targets the original class method name
        with patch.object(CronSchedulerService, "_on_wake", _mock_on_wake):
            ev = _Event(at_ts=time.time(), seq=1, kind="wake", job_id=job.id, run_id=f"{job.id}:1234")
            await svc.handle_event(ev)

        assert wake_called is True
        assert len(svc.jobs) == 1


# ── Reload ghost task cleanup ─────────────────────────────────────────────────


class TestReloadGhostTaskCleanup:
    """reload() cancels running tasks and clears state for jobs no longer in the store."""

    @pytest.mark.asyncio
    async def test_reload_cancels_ghost_run_tasks_when_store_deleted(self, tmp_path):
        """Reload cancels in-flight tasks for absent jobs and clears _runs state."""
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        # Simulate a running task: create state + an asyncio Task that blocks
        run_id = f"{job.id}:1234"
        svc.runs[run_id] = CronRunState(
            run_id=run_id,
            job_id=job.id,
            wake_at_iso="2026-06-09T08:55:00+08:00",
            push_at_iso="2026-06-09T09:00:00+08:00",
            job_name=job.name,
            targets=job.targets,
            session_id=None,
            chat_type=None,
            timezone=job.timezone,
            status="running",
        )

        # Create a long-running asyncio Task (simulating agent execution)
        block_event = asyncio.Event()

        async def _long_running():
            await block_event.wait()
        task = asyncio.create_task(_long_running(), name=f"cron-run-{job.id}")
        svc.run_tasks[run_id] = task

        # Verify preconditions: run state and task exist
        assert run_id in svc.runs
        assert run_id in svc.run_tasks
        assert not task.done()

        # Delete the store file — job no longer exists persistently
        store_file.unlink()

        # Trigger reload (which _check_store_changed would do)
        await svc.reload()

        # Ghost task should be cancelled and removed from run_tasks
        assert run_id not in svc.run_tasks
        # task.cancel() is a request — the task needs a yield point to process it.
        # Give the event loop a turn to propagate the cancellation.
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
        # Ghost run state should be removed from _runs
        assert run_id not in svc.runs
        # No jobs in memory
        assert len(svc.jobs) == 0
        # No messages published for the ghost task
        assert len(handler.published) == 0

        # Cleanup
        block_event.set()

    @pytest.mark.asyncio
    async def test_reload_preserves_running_tasks_for_existing_jobs(self, tmp_path):
        """In-flight tasks for jobs still in store should NOT be cancelled on reload."""
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        # Simulate a running task
        run_id = f"{job.id}:1234"
        svc.runs[run_id] = CronRunState(
            run_id=run_id,
            job_id=job.id,
            wake_at_iso="2026-06-09T08:55:00+08:00",
            push_at_iso="2026-06-09T09:00:00+08:00",
            job_name=job.name,
            targets=job.targets,
            session_id=None,
            chat_type=None,
            timezone=job.timezone,
            status="running",
            result_text="task finished",
        )

        # Store file still exists — job is still in the store
        await svc.reload()

        # Running task for existing job should be preserved
        assert run_id in svc.runs
        assert len(svc.jobs) == 1

    @pytest.mark.asyncio
    async def test_reload_cleans_push_update_events_for_ghost_jobs(self, tmp_path):
        """Push_update events for absent jobs should be removed during reload."""
        store_file = tmp_path / "cron_jobs.json"
        store = CronJobStore(path=store_file)
        job = await _create_one_job(store)

        handler = FakeMessageHandler()
        svc = _make_scheduler(store, handler)
        await svc.reload()

        # Schedule a push_update event
        run_id = f"{job.id}:1234"
        from datetime import datetime
        from zoneinfo import ZoneInfo
        svc.schedule_event(
            datetime.now(tz=ZoneInfo("Asia/Shanghai")),
            "push_update", job.id, run_id,
        )

        # Verify push_update event exists
        push_update_events = [
            ev for _, _, ev in svc.events if ev.kind == "push_update"
        ]
        assert len(push_update_events) == 1

        # Delete the store file — job gone from store
        store_file.unlink()

        # Reload should remove push_update events for the ghost job
        await svc.reload()

        push_update_events_after = [
            ev for _, _, ev in svc.events if ev.kind == "push_update"
        ]
        assert len(push_update_events_after) == 0