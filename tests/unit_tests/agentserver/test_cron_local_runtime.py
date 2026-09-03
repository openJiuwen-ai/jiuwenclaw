from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.cron_local_runtime import (
    AgentCronRegistry,
    InProcessAgentServerClient,
    NopCronMessageHandler,
    RelayCronMessageHandler,
    resolve_agent_side_cron_deps,
)
from jiuwenclaw.agentserver.tools.cron_tools import CronToolRoute, CronTools
from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.gateway.cron.models import CronRunState
from jiuwenclaw.gateway.cron.scheduler import CronSchedulerService
from jiuwenclaw.gateway.cron.store import CronJobStore
from jiuwenclaw.schema.agent import AgentResponse
from jiuwenclaw.schema.message import ReqMethod


@pytest.mark.asyncio
async def test_inprocess_client_send_request_uses_pool() -> None:
    pool = MagicMock()
    pool.process_message = AsyncMock(
        return_value=AgentResponse(
            request_id="r1",
            channel_id="__cron__",
            ok=True,
            payload={"content": {"output": "done"}},
        )
    )
    client = InProcessAgentServerClient(agent_manager=pool)
    envelope = e2a_from_agent_fields(
        request_id="cron-r1",
        channel_id="__cron__",
        session_id="cron_sess",
        req_method=ReqMethod.CHAT_SEND,
        params={"content": "hello", "service_id": "default", "agent_id": "office"},
        metadata={"service_id": "default", "agent_id": "office"},
    )
    resp = await client.send_request(envelope)
    assert resp.ok is True
    pool.process_message.assert_awaited_once()
    req = pool.process_message.await_args.args[0]
    assert req.params.get("content") == "hello"
    assert req.service_id == "default"
    assert req.agent_id == "office"


@pytest.mark.asyncio
async def test_nop_message_handler_swallows_publish() -> None:
    await NopCronMessageHandler().publish_robot_messages(MagicMock(channel_id="web"))


def test_resolve_deps_defaults_to_inprocess_and_relay_push() -> None:
    client, handler = resolve_agent_side_cron_deps()
    assert isinstance(client, InProcessAgentServerClient)
    assert isinstance(handler, RelayCronMessageHandler)


@pytest.mark.asyncio
async def test_relay_message_handler_forwards_web_cron_result() -> None:
    pushed: list[dict] = []

    class _CapturePush:
        async def send_push(self, payload: dict) -> None:
            pushed.append(payload)

    handler = RelayCronMessageHandler(gateway_push=_CapturePush())
    await handler.publish_robot_messages(
        SimpleNamespace(
            channel_id="web",
            metadata={
                "officeclaw_cron_route": {
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "agent_id": "office",
                }
            },
            payload={
                "content": "任务完成",
                "cron": {"job_id": "job-1", "run_id": "run-1", "is_placeholder": False},
            },
        )
    )

    assert pushed == [
        {
            "request_id": "cron-delivery:job-1:run-1:final",
            "channel_id": "officeclaw",
            "payload": {
                "event_type": "chat.cron_delivery",
                "content": "任务完成",
                "cron": {"job_id": "job-1", "run_id": "run-1", "is_placeholder": False},
            },
            "metadata": {
                "officeclaw_cron_route": {
                    "user_id": "user-1",
                    "thread_id": "thread-1",
                    "agent_id": "office",
                }
            },
        }
    ]


@pytest.mark.asyncio
async def test_cron_tools_persists_officeclaw_delivery_route(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)

    class _CapturePush:
        async def send_push(self, _payload: dict) -> None:
            return None

    tools = CronTools(gateway_push=_CapturePush(), service_id="default", agent_id="office")
    token = tools.push_cron_route(
        CronToolRoute(
            channel_id="officeclaw",
            relay_user_id="user-1",
            relay_thread_id="thread-1",
            relay_agent_id="office",
        )
    )
    try:
        job = await tools.create_job(
            {
                "name": "daily",
                "cron_expr": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "targets": "web",
            }
        )
    finally:
        tools.reset_cron_route(token)

    assert job["relay_delivery"] == {
        "user_id": "user-1",
        "thread_id": "thread-1",
        "agent_id": "office",
    }


@pytest.mark.asyncio
async def test_scheduler_passes_persisted_relay_route_to_agentserver_handler(tmp_path) -> None:
    store = CronJobStore(path=tmp_path / "cron_jobs.json")
    job = await store.create_job(
        job_id="job-1",
        name="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="任务完成",
        targets="web",
        relay_delivery={"user_id": "user-1", "thread_id": "thread-1", "agent_id": "office"},
    )
    messages = []

    class _CaptureHandler:
        async def publish_robot_messages(self, message) -> None:
            messages.append(message)

    scheduler = CronSchedulerService(
        store=store,
        agent_client=MagicMock(),
        message_handler=_CaptureHandler(),
    )
    await scheduler._push_to_targets(
        job,
        CronRunState(run_id="run-1", job_id="job-1", wake_at_iso="", push_at_iso=""),
        text="任务完成",
        is_placeholder=False,
    )

    assert messages[0].metadata["officeclaw_cron_route"] == {
        "user_id": "user-1",
        "thread_id": "thread-1",
        "agent_id": "office",
    }


@pytest.mark.asyncio
async def test_cron_tools_ensure_scheduler_starts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    AgentCronRegistry.reset_for_tests()

    started = AsyncMock()
    reloaded = AsyncMock()

    class _FakeScheduler:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self._running = False

        def is_running(self) -> bool:
            return self._running

        async def start(self) -> None:
            self._running = True
            await started()

        async def reload(self) -> None:
            await reloaded()

        async def stop(self) -> None:
            self._running = False

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.CronSchedulerService",
        _FakeScheduler,
    )

    tools = AgentCronRegistry.get_or_create(
        "default",
        "office",
        factory=lambda: CronTools(service_id="default", agent_id="office"),
    )
    sched = await tools.ensure_scheduler()
    assert sched is not None
    assert sched.is_running()
    started.assert_awaited_once()

    await tools._reload_scheduler()
    reloaded.assert_awaited_once()
    assert sched.kwargs["service_id"] == "default"
    assert sched.kwargs["agent_id"] == "office"

    AgentCronRegistry.reset_for_tests()


@pytest.mark.asyncio
async def test_ensure_scheduler_retries_after_start_failure(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    AgentCronRegistry.reset_for_tests()

    attempts = {"n": 0}
    stop_mock = AsyncMock()
    warn_calls: list[tuple] = []

    class _FlakyScheduler:
        def __init__(self, **_kwargs):
            self._running = False

        def is_running(self) -> bool:
            return self._running

        async def start(self) -> None:
            attempts["n"] += 1
            if attempts["n"] == 1:
                self._running = True  # simulate half-started before failure
                raise RuntimeError("boom-start")
            self._running = True

        async def stop(self) -> None:
            self._running = False
            await stop_mock()

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.CronSchedulerService",
        _FlakyScheduler,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.cron_local_runtime.resolve_agent_side_cron_deps",
        lambda **_kwargs: (MagicMock(), MagicMock()),
    )

    import jiuwenclaw.agentserver.tools.cron_tools as cron_tools_mod

    real_warning = cron_tools_mod.logger.warning

    def _capture_warning(msg, *args, **kwargs):
        warn_calls.append((msg, args, kwargs))
        return real_warning(msg, *args, **kwargs)

    monkeypatch.setattr(cron_tools_mod.logger, "warning", _capture_warning)

    tools = AgentCronRegistry.get_or_create(
        "svc-a",
        "office",
        factory=lambda: CronTools(service_id="svc-a", agent_id="office"),
    )

    assert await tools.ensure_scheduler() is None

    assert tools._scheduler is None
    assert tools._scheduler_started is False
    assert stop_mock.await_count == 1
    assert warn_calls
    msg, args, kwargs = warn_calls[0]
    rendered = msg % args if args else msg
    assert "Failed to start scheduler" in rendered
    assert "svc-a" in rendered
    assert "office" in rendered
    assert "will retry on next ensure_scheduler" in rendered
    assert kwargs.get("exc_info") is True

    sched = await tools.ensure_scheduler()
    assert sched is not None
    assert sched.is_running()
    assert attempts["n"] == 2

    AgentCronRegistry.reset_for_tests()


@pytest.mark.asyncio
async def test_cron_tools_create_job_uses_instance_tenant_not_route_default(
    tmp_path, monkeypatch
) -> None:
    """Empty CronToolRoute defaults to service_id='default'; instance tenant must win."""
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)

    pushed: list[dict] = []

    class _CapturePush:
        async def send_push(self, payload: dict) -> None:
            pushed.append(payload)

    tools = CronTools(
        gateway_push=_CapturePush(),
        service_id="svc-a",
        agent_id="office",
    )
    # No route pushed → _route() synthesizes CronToolRoute() with default/default.
    job = await tools.create_job(
        {
            "name": "t",
            "cron_expr": "0 9 * * *",
            "targets": "web",
        }
    )
    assert job["service_id"] == "svc-a"
    assert job["agent_id"] == "office"
    assert pushed
    assert pushed[0]["body"]["service_id"] == "svc-a"
    assert pushed[0]["body"]["agent_id"] == "office"


@pytest.mark.asyncio
async def test_agent_cron_registry_remove_stops_scheduler(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    AgentCronRegistry.reset_for_tests()

    stop_mock = AsyncMock()
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.CronSchedulerService.start",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.CronSchedulerService.stop",
        stop_mock,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.cron_local_runtime.resolve_agent_side_cron_deps",
        lambda **_kwargs: (MagicMock(), MagicMock()),
    )

    tools = AgentCronRegistry.get_or_create(
        "default",
        "office",
        factory=lambda: CronTools(service_id="default", agent_id="office"),
    )
    await tools.ensure_scheduler()

    assert await AgentCronRegistry.remove("default", "office") is True
    assert stop_mock.await_count == 1
    assert await AgentCronRegistry.remove("default", "office") is False
    assert tools._scheduler is None
    assert tools._retired is True
    assert await tools.ensure_scheduler() is None

    AgentCronRegistry.reset_for_tests()


@pytest.mark.asyncio
async def test_ensure_scheduler_does_not_resurrect_after_concurrent_remove(
    tmp_path, monkeypatch
) -> None:
    """Delayed ensure_scheduler must not re-register after AgentCronRegistry.remove."""
    import asyncio

    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    AgentCronRegistry.reset_for_tests()

    start_gate = asyncio.Event()
    start_entered = asyncio.Event()
    stop_mock = AsyncMock()

    class _SlowStartScheduler:
        def __init__(self, **_kwargs):
            self._running = False

        def is_running(self) -> bool:
            return self._running

        async def start(self) -> None:
            start_entered.set()
            await start_gate.wait()
            self._running = True

        async def stop(self) -> None:
            self._running = False
            await stop_mock()

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.CronSchedulerService",
        _SlowStartScheduler,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.cron_local_runtime.resolve_agent_side_cron_deps",
        lambda **_kwargs: (MagicMock(), MagicMock()),
    )

    tools = AgentCronRegistry.get_or_create(
        "default",
        "office",
        factory=lambda: CronTools(service_id="default", agent_id="office"),
    )
    ensure_task = asyncio.create_task(tools.ensure_scheduler())
    await start_entered.wait()

    assert await AgentCronRegistry.remove("default", "office") is True
    start_gate.set()
    result = await ensure_task

    assert result is None
    assert tools._retired is True
    assert tools._scheduler is None
    assert AgentCronRegistry.is_current("default", "office", tools) is False
    # Re-provision uses a new CronTools instance.
    fresh = AgentCronRegistry.get_or_create(
        "default",
        "office",
        factory=lambda: CronTools(service_id="default", agent_id="office"),
    )
    assert fresh is not tools
    assert await fresh.ensure_scheduler() is not None

    AgentCronRegistry.reset_for_tests()
