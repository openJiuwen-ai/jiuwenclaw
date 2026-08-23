from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.multi_session_toolkits import (
    MultiSessionToolkit,
    SessionTask,
    Status,
)
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer
from jiuwenswarm.server.runtime.proactive_adapter import (
    ProactiveTriggerRequest,
    trigger_main_agent,
)


class _CachedAgent:
    def __init__(self) -> None:
        self.executions = 0
        self.executed = asyncio.Event()

    async def process_message_stream(self, _request):
        self.executions += 1
        self.executed.set()
        if False:
            yield None

    def get_instance(self):
        return self

    async def ensure_instance(self):
        return self


class _ReadinessManager:
    def __init__(self, agent: _CachedAgent) -> None:
        self.agent = agent
        self.release = asyncio.Event()
        self.waiting = asyncio.Event()
        self.failure: Exception | None = None
        self.lookups = 0

    async def wait_for_permissions_ready(self) -> None:
        self.waiting.set()
        await self.release.wait()
        if self.failure is not None:
            raise self.failure

    def get_agent_nowait(self, _channel_id: str):
        self.lookups += 1
        return self.agent


class _Server:
    def __init__(self, manager: _ReadinessManager) -> None:
        self.manager = manager
        self.pushes: list[dict] = []

    def get_agent_manager(self) -> _ReadinessManager:
        return self.manager

    def get_agent(self):
        return self.manager.agent

    async def send_push(self, message: dict) -> None:
        self.pushes.append(message)


def _proactive_request() -> ProactiveTriggerRequest:
    return ProactiveTriggerRequest(
        session_id="session-1",
        channel_id="web",
        query="continue",
        decision=SimpleNamespace(type="reminder", target="task"),
    )


def _multi_session_toolkit() -> MultiSessionToolkit:
    toolkit = MultiSessionToolkit(
        session_id="parent-session",
        channel_id="web",
        request_id="request-1",
        sub_agent_config=SimpleNamespace(),
    )
    toolkit.sessions.append(
        SessionTask(
            session_id="child-session",
            description="finished task",
            status=Status.COMPLETED,
            result="done",
        )
    )
    return toolkit


@pytest.mark.asyncio
async def test_proactive_cached_agent_waits_for_permission_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _CachedAgent()
    manager = _ReadinessManager(agent)
    server = _Server(manager)
    monkeypatch.setattr(AgentWebSocketServer, "resolve_adapter", lambda _agent: None)

    trigger = asyncio.create_task(trigger_main_agent(server, _proactive_request()))
    await manager.waiting.wait()

    assert manager.lookups == 0
    assert agent.executions == 0
    manager.release.set()

    assert await trigger is True
    await agent.executed.wait()
    assert manager.lookups == 1
    assert agent.executions == 1


@pytest.mark.asyncio
async def test_proactive_cached_agent_failure_blocks_then_recovery_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _CachedAgent()
    manager = _ReadinessManager(agent)
    server = _Server(manager)
    monkeypatch.setattr(AgentWebSocketServer, "resolve_adapter", lambda _agent: None)
    manager.failure = RuntimeError("permission reload failed")
    manager.release.set()

    with pytest.raises(RuntimeError, match="permission reload failed"):
        await trigger_main_agent(server, _proactive_request())
    assert manager.lookups == 0
    assert agent.executions == 0

    manager.failure = None
    assert await trigger_main_agent(server, _proactive_request()) is True
    await agent.executed.wait()
    assert manager.lookups == 1
    assert agent.executions == 1


@pytest.mark.asyncio
async def test_multi_session_cached_agent_waits_for_permission_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _CachedAgent()
    manager = _ReadinessManager(agent)
    server = _Server(manager)
    toolkit = _multi_session_toolkit()
    monkeypatch.setattr(AgentWebSocketServer, "get_instance", lambda: server)

    async def run_agent_streaming(_agent, *, inputs):
        _ = inputs
        agent.executions += 1
        yield SimpleNamespace(type="answer", payload={"output": "summary"})

    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(Runner, "run_agent_streaming", run_agent_streaming)
    notify = asyncio.create_task(
        toolkit.notify("child-session", Status.COMPLETED, result="done")
    )
    await manager.waiting.wait()

    assert manager.lookups == 0
    assert agent.executions == 0
    manager.release.set()

    await notify
    assert manager.lookups == 1
    assert agent.executions == 1


@pytest.mark.asyncio
async def test_multi_session_cached_agent_failure_blocks_then_recovery_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _CachedAgent()
    manager = _ReadinessManager(agent)
    server = _Server(manager)
    toolkit = _multi_session_toolkit()
    monkeypatch.setattr(AgentWebSocketServer, "get_instance", lambda: server)

    async def run_agent_streaming(_agent, *, inputs):
        _ = inputs
        agent.executions += 1
        yield SimpleNamespace(type="answer", payload={"output": "summary"})

    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(Runner, "run_agent_streaming", run_agent_streaming)
    manager.failure = RuntimeError("permission reload failed")
    manager.release.set()

    with pytest.raises(RuntimeError, match="permission reload failed"):
        await toolkit.notify("child-session", Status.COMPLETED, result="done")
    assert manager.lookups == 0
    assert agent.executions == 0

    manager.failure = None
    await toolkit.notify("child-session", Status.COMPLETED, result="done")
    assert manager.lookups == 1
    assert agent.executions == 1
