"""Stage-3 coverage for the outbound Dispatcher and Agent toolkit Rail."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from types import SimpleNamespace

import pytest
from a2a.types import (
    Artifact,
    Message,
    Part,
    Role,
    StreamResponse,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)

from jiuwenswarm.agents.harness.common.rails.a2a_outbound_toolkit_rail import (
    A2AOutboundToolkitRail,
)
from jiuwenswarm.agents.harness.common.tools.a2a_outbound_tools import (
    A2AOutboundToolkit,
)
from jiuwenswarm.agents.harness.common.tools.acp_output_tools import (
    get_acp_output_manager,
)
from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
)
from jiuwenswarm.common.e2a.gateway_normalize import message_to_e2a
from jiuwenswarm.common.e2a.models import E2AResponse
from jiuwenswarm.common.e2a.wire_codec import parse_agent_server_wire_chunk
from jiuwenswarm.gateway.a2a_manager.outbound import (
    A2ACompatibleInterface,
    A2AOutboundAgent,
    A2AOutboundAvailability,
    A2AOutboundDiscoveryService,
    A2AOutboundDispatchStatus,
    A2AOutboundDispatcher,
    A2AOutboundError,
    A2AOutboundErrorCode,
    A2AOutboundRepository,
)
from jiuwenswarm.gateway.a2a_manager.tool_rpc import (
    A2A_TOOL_CANCEL_CALL,
    A2A_TOOL_DISPATCH_TASK,
    A2A_TOOL_FIND_AGENTS,
    A2A_TOOL_GET_DISPATCH,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
from jiuwenswarm.gateway.storage.backends.memory_persistent import (
    InMemoryPersistentBackend,
)
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire
from jiuwenswarm.server.wire_parse import parse_inbound


def _agent(agent_id: str = "agent-1") -> A2AOutboundAgent:
    stamp = "2026-08-26T00:00:00Z"
    return A2AOutboundAgent(
        agent_id=agent_id,
        display_name="Research Agent",
        source_url="https://agent.example.com",
        card_path="/.well-known/agent-card.json",
        card_fingerprint="sha256:card",
        card_revision=1,
        agent_card={
            "name": "Research Agent",
            "description": "Researches and summarizes",
            "skills": [
                {
                    "id": "research",
                    "name": "Research",
                    "description": "Find evidence",
                    "tags": ["summarization"],
                }
            ],
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
        },
        selected_interface=A2ACompatibleInterface(
            "JSONRPC", "1.0.0", "https://agent.example.com/a2a"
        ),
        enabled=True,
        availability=A2AOutboundAvailability.AVAILABLE,
        credential_ref=None,
        connect_timeout_seconds=1,
        sync_wait_seconds=0.05,
        created_at=stamp,
        updated_at=stamp,
    )


class _FakeClient:
    def __init__(self, events=(), *, tasks=(), block_after_events: bool = False):
        self.events = list(events)
        self.tasks = list(tasks)
        self.block_after_events = block_after_events
        self.closed = 0
        self.sent_requests = []
        self.get_requests = []

    async def send_message(self, request, *, context=None):
        self.sent_requests.append((request, context))
        for event in self.events:
            yield event
        if self.block_after_events:
            await asyncio.Event().wait()

    async def get_task(self, request, *, context=None):
        self.get_requests.append((request, context))
        return self.tasks.pop(0)

    async def cancel_task(self, request, *, context=None):
        return self.tasks.pop(0)

    async def close(self) -> None:
        self.closed += 1


def _message_event(text: str = "done") -> StreamResponse:
    return StreamResponse(
        message=Message(
            message_id="remote-message",
            context_id="ctx-1",
            role=Role.ROLE_AGENT,
            parts=[Part(text=text)],
        )
    )


def _task(
    state: int,
    *,
    task_id: str = "remote-task",
    text: str | None = None,
) -> Task:
    status = TaskStatus(state=state)
    if text is not None:
        status.message.CopyFrom(
            Message(
                message_id="remote-message",
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            )
        )
    return Task(id=task_id, context_id="ctx-1", status=status)


def _task_event(state: int) -> StreamResponse:
    return StreamResponse(task=_task(state))


def _artifact_event(text: str) -> StreamResponse:
    return StreamResponse(
        artifact_update=TaskArtifactUpdateEvent(
            task_id="remote-task",
            context_id="ctx-1",
            artifact=Artifact(
                artifact_id="answer",
                name="response",
                parts=[Part(text=text)],
            ),
            last_chunk=True,
        )
    )


def _status_event(state: int, *, text: str | None = None) -> StreamResponse:
    status = TaskStatus(state=state)
    if text is not None:
        status.message.CopyFrom(
            Message(
                message_id="status-message",
                role=Role.ROLE_AGENT,
                parts=[Part(text=text)],
            )
        )
    return StreamResponse(
        status_update=TaskStatusUpdateEvent(
            task_id="remote-task",
            context_id="ctx-1",
            status=status,
        )
    )


async def _dispatcher(client: _FakeClient):
    repository = A2AOutboundRepository(InMemoryPersistentBackend())
    await repository.create_agent(_agent())

    async def builder(_agent, _credential):
        return client

    return (
        A2AOutboundDispatcher(
            repository,
            client_builder=builder,
            query_interval_seconds=0,
        ),
        repository,
    )


@pytest.mark.asyncio
async def test_find_agents_returns_only_callable_minimal_catalog() -> None:
    client = _FakeClient()
    dispatcher, repository = await _dispatcher(client)
    await repository.create_agent(replace(_agent("disabled"), enabled=False))

    result = await dispatcher.find_agents(
        query="research summary", required_skills=["research"], limit=5
    )

    assert [item["agent_id"] for item in result["items"]] == ["agent-1"]
    assert "url" not in result["items"][0]
    assert "credential_ref" not in result["items"][0]


@pytest.mark.asyncio
async def test_sync_dispatch_returns_normalized_final_message() -> None:
    client = _FakeClient([_message_event("final answer")])
    dispatcher, repository = await _dispatcher(client)

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="do research", mode="sync", source_session_id="s1"
    )

    assert result["ok"] is True
    assert result["status"] == "completed"
    assert result["result"]["text"] == "final answer"
    persisted = await repository.get_dispatch(result["dispatch_id"])
    assert (
        persisted is not None
        and persisted.status is A2AOutboundDispatchStatus.COMPLETED
    )
    assert persisted.input_digest.startswith("sha256:")
    assert client.sent_requests[0][0].message.parts[0].text == "do research"
    assert client.sent_requests[0][1] is None
    assert client.closed == 1


@pytest.mark.asyncio
async def test_real_http_stream_can_outlive_connect_timeout_within_sync_budget() -> None:
    served = asyncio.Event()
    received_requests = []

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            header_bytes = await reader.readuntil(b"\r\n\r\n")
            header_text = header_bytes.decode("iso-8859-1")
            content_length = 0
            for line in header_text.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
                    break
            request = json.loads((await reader.readexactly(content_length)).decode())
            received_requests.append(request)
            response = {
                "jsonrpc": "2.0",
                "id": request["id"],
                "result": {
                    "message": {
                        "messageId": "remote-message",
                        "role": "ROLE_AGENT",
                        "parts": [{"text": "slow final"}],
                    }
                },
            }
            body = f"data: {json.dumps(response)}\n\n".encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                + f"Content-Length: {len(body)}\r\n".encode()
                + b"Connection: close\r\n\r\n"
            )
            await writer.drain()
            await asyncio.sleep(0.12)
            writer.write(body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            served.set()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    endpoint = f"http://127.0.0.1:{port}/a2a"
    agent = replace(
        _agent(),
        source_url=f"http://127.0.0.1:{port}",
        selected_interface=A2ACompatibleInterface("JSONRPC", "1.0.0", endpoint),
        connect_timeout_seconds=0.05,
        sync_wait_seconds=0.4,
        agent_card={
            "name": "Slow Agent",
            "description": "Responds after the connect timeout",
            "version": "1.0.0",
            "supportedInterfaces": [
                {
                    "url": endpoint,
                    "protocolBinding": "JSONRPC",
                    "protocolVersion": "1.0.0",
                }
            ],
            "capabilities": {"streaming": True},
            "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"],
            "skills": [],
        },
    )
    repository = A2AOutboundRepository(InMemoryPersistentBackend())
    await repository.create_agent(agent)
    dispatcher = A2AOutboundDispatcher(
        repository,
        discovery_service=A2AOutboundDiscoveryService(allow_loopback_http=True),
    )
    try:
        result = await dispatcher.dispatch(
            agent_id=agent.agent_id,
            task="slow request",
            mode="sync",
            source_session_id="s1",
        )
        await asyncio.wait_for(served.wait(), timeout=1)
    finally:
        server.close()
        await server.wait_closed()

    assert result["status"] == "completed"
    assert result["result"]["text"] == "slow final"
    assert received_requests[0]["params"]["configuration"]["returnImmediately"] is True


@pytest.mark.asyncio
async def test_completed_stream_promotes_final_artifact_over_progress_text() -> None:
    client = _FakeClient(
        [
            _status_event(TaskState.TASK_STATE_WORKING, text="正在查询天气演示数据……"),
            _artifact_event("北京当前晴，25°C。"),
            _status_event(TaskState.TASK_STATE_COMPLETED),
        ]
    )
    dispatcher, _ = await _dispatcher(client)

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="查询北京天气", mode="sync", source_session_id="s1"
    )

    assert result["status"] == "completed"
    assert result["result"]["text"] == "北京当前晴，25°C。"
    assert result["result"]["artifacts"][0]["text"] == "北京当前晴，25°C。"


@pytest.mark.asyncio
async def test_sync_dispatch_polls_an_accepted_task_to_terminal() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)],
        tasks=[_task(TaskState.TASK_STATE_COMPLETED, text="polled answer")],
    )
    dispatcher, _ = await _dispatcher(client)

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="do research", mode="sync", source_session_id="s1"
    )

    assert result["status"] == "completed"
    assert result["result"]["text"] == "polled answer"
    assert client.sent_requests[0][0].configuration.return_immediately is True
    assert len(client.get_requests) == 1


@pytest.mark.asyncio
async def test_sync_dispatch_gets_prompt_rejection_before_terminal_wait() -> None:
    class _UnavailableExecutorClient(_FakeClient):
        async def send_message(self, request, *, context=None):
            self.sent_requests.append((request, context))
            if request.configuration.return_immediately:
                raise RuntimeError("executor unavailable")
            await asyncio.Event().wait()
            yield  # pragma: no cover - keeps this an async generator

    client = _UnavailableExecutorClient()
    dispatcher, repository = await _dispatcher(client)
    await repository.update_agent(
        "agent-1",
        lambda current: replace(current, sync_wait_seconds=1),
    )

    result = await asyncio.wait_for(
        dispatcher.dispatch(
            agent_id="agent-1",
            task="do research",
            mode="sync",
            source_session_id="s1",
        ),
        timeout=0.2,
    )

    assert result["status"] == "dispatch_failed"
    assert result["error_code"] == A2AOutboundErrorCode.DISPATCH_REJECTED.value
    assert client.sent_requests[0][0].configuration.return_immediately is True


@pytest.mark.asyncio
async def test_async_dispatch_requires_remote_task_id_then_query_converges() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)],
        tasks=[_task(TaskState.TASK_STATE_COMPLETED, text="async answer")],
    )
    dispatcher, _ = await _dispatcher(client)

    accepted = await dispatcher.dispatch(
        agent_id="agent-1", task="long work", mode="async", source_session_id="s1"
    )
    completed = await dispatcher.query_dispatch(
        accepted["dispatch_id"], source_session_id="s1"
    )

    assert accepted["status"] == "accepted"
    assert accepted["remote_task_id"] == "remote-task"
    assert "a2a_get_dispatch" in accepted["next_action"]
    assert completed["status"] == "completed"
    assert completed["result"]["text"] == "async answer"


@pytest.mark.asyncio
async def test_async_without_remote_id_is_dispatch_failed() -> None:
    client = _FakeClient([])
    dispatcher, _ = await _dispatcher(client)

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="long work", mode="async", source_session_id="s1"
    )

    assert result["ok"] is False
    assert result["status"] == "dispatch_failed"


@pytest.mark.asyncio
async def test_sync_timeout_keeps_known_ids_queryable() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)], block_after_events=True
    )
    dispatcher, repository = await _dispatcher(client)

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="slow", mode="sync", source_session_id="s1"
    )

    assert result["status"] == "timed_out"
    assert result["remote_task_id"] == "remote-task"
    assert "a2a_get_dispatch" in result["next_action"]
    persisted = await repository.get_dispatch(result["dispatch_id"])
    assert persisted is not None and not persisted.is_terminal


@pytest.mark.asyncio
async def test_query_completion_clears_prior_timeout_error() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)], block_after_events=True
    )
    dispatcher, _ = await _dispatcher(client)
    timed_out = await dispatcher.dispatch(
        agent_id="agent-1", task="slow", mode="sync", source_session_id="s1"
    )
    client.block_after_events = False
    client.tasks.append(_task(TaskState.TASK_STATE_COMPLETED, text="late answer"))

    completed = await dispatcher.query_dispatch(
        timed_out["dispatch_id"], source_session_id="s1"
    )

    assert completed["status"] == "completed"
    assert completed["error_code"] is None
    assert completed["error_summary"] is None


@pytest.mark.asyncio
async def test_required_remote_auth_without_credential_is_not_left_submitting() -> None:
    client = _FakeClient()
    dispatcher, repository = await _dispatcher(client)
    await repository.update_agent(
        "agent-1",
        lambda current: replace(
            current,
            agent_card={**current.agent_card, "securityRequirements": [{"bearer": []}]},
        ),
    )

    result = await dispatcher.dispatch(
        agent_id="agent-1", task="work", mode="sync", source_session_id="s1"
    )

    assert result["status"] == "auth_required"
    assert result["error_code"] == A2AOutboundErrorCode.AUTH_REQUIRED.value
    assert client.sent_requests == []


@pytest.mark.asyncio
async def test_cancellation_moves_record_out_of_processing_and_reraises() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)], block_after_events=True
    )
    dispatcher, repository = await _dispatcher(client)
    started = asyncio.create_task(
        dispatcher.dispatch(
            agent_id="agent-1", task="slow", mode="sync", source_session_id="s1"
        )
    )
    while not client.sent_requests:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    started.cancel()

    with pytest.raises(asyncio.CancelledError):
        await started
    records = await repository.list_dispatches()
    assert records[0].status is A2AOutboundDispatchStatus.UNKNOWN
    assert records[0].remote_task_id == "remote-task"


@pytest.mark.asyncio
async def test_cancellation_uses_remote_cancel_result_when_available() -> None:
    client = _FakeClient(
        [_task_event(TaskState.TASK_STATE_SUBMITTED)],
        tasks=[_task(TaskState.TASK_STATE_CANCELED)],
        block_after_events=True,
    )
    dispatcher, repository = await _dispatcher(client)
    started = asyncio.create_task(
        dispatcher.dispatch(
            agent_id="agent-1", task="slow", mode="sync", source_session_id="s1"
        )
    )
    while not client.sent_requests:
        await asyncio.sleep(0)
    await asyncio.sleep(0)
    started.cancel()

    with pytest.raises(asyncio.CancelledError):
        await started
    records = await repository.list_dispatches()
    assert records[0].status is A2AOutboundDispatchStatus.CANCELED


@pytest.mark.asyncio
async def test_dispatch_query_is_scoped_to_originating_session() -> None:
    client = _FakeClient([_task_event(TaskState.TASK_STATE_SUBMITTED)])
    dispatcher, _ = await _dispatcher(client)
    accepted = await dispatcher.dispatch(
        agent_id="agent-1", task="work", mode="async", source_session_id="s1"
    )

    with pytest.raises(A2AOutboundError) as error:
        await dispatcher.query_dispatch(
            accepted["dispatch_id"], source_session_id="other"
        )
    assert error.value.code is A2AOutboundErrorCode.DISPATCH_NOT_FOUND


class _Backend:
    ready = True

    async def call(self, method, params, *, session_id, channel_id):
        return {"method": method, "session_id": session_id, "channel_id": channel_id}


class _AbilityManager:
    def __init__(self):
        self.tools = {}

    def add_ability(self, card, tool):
        self.tools[card.name] = tool

    def remove_ability(self, name):
        self.tools.pop(name, None)


class _PromptBuilder:
    language = "cn"

    def __init__(self):
        self.sections = {}

    def add_section(self, section):
        self.sections[section.name] = section

    def remove_section(self, name):
        self.sections.pop(name, None)


@dataclass
class _Agent:
    ability_manager: _AbilityManager
    system_prompt_builder: _PromptBuilder


def test_rail_registers_three_tools_and_cleans_up() -> None:
    agent = _Agent(_AbilityManager(), _PromptBuilder())
    rail = A2AOutboundToolkitRail(backend_provider=lambda: _Backend())

    rail.init(agent)
    assert set(agent.ability_manager.tools) == {
        "a2a_find_agents",
        "a2a_dispatch_task",
        "a2a_get_dispatch",
    }
    assert "a2a_outbound_usage" in agent.system_prompt_builder.sections

    rail.uninit(agent)
    assert agent.ability_manager.tools == {}
    assert agent.system_prompt_builder.sections == {}


def test_rail_keeps_tools_visible_when_gateway_proxy_is_not_ready() -> None:
    backend = _Backend()
    backend.ready = False
    agent = _Agent(_AbilityManager(), _PromptBuilder())
    rail = A2AOutboundToolkitRail(backend_provider=lambda: backend)

    rail.init(agent)

    assert set(agent.ability_manager.tools) == {
        "a2a_find_agents",
        "a2a_dispatch_task",
        "a2a_get_dispatch",
    }


@pytest.mark.asyncio
async def test_rail_registers_on_first_model_call_after_gateway_becomes_ready() -> None:
    backend = _Backend()
    current_backend = None
    agent = _Agent(_AbilityManager(), _PromptBuilder())
    rail = A2AOutboundToolkitRail(backend_provider=lambda: current_backend)

    rail.init(agent)
    current_backend = backend
    await rail.before_model_call(SimpleNamespace(agent=agent))

    assert set(agent.ability_manager.tools) == {
        "a2a_find_agents",
        "a2a_dispatch_task",
        "a2a_get_dispatch",
    }
    prompt = agent.system_prompt_builder.sections["a2a_outbound_usage"].content["cn"]
    assert '"\n' not in prompt
    assert "a2a_find_agents" in prompt


@pytest.mark.asyncio
async def test_toolkit_binds_session_and_exposes_no_url_or_credentials() -> None:
    toolkit = A2AOutboundToolkit(_Backend(), runtime_route=lambda: ("session-1", "web"))
    result = await toolkit.dispatch_task("agent-1", "task", "sync")
    dispatch_tool = {tool.card.name: tool for tool in toolkit.get_tools()}[
        "a2a_dispatch_task"
    ]

    assert result["session_id"] == "session-1"
    properties = dispatch_tool.card.input_params["properties"]
    assert not ({"url", "headers", "credential", "timeout"} & set(properties))


@pytest.mark.asyncio
async def test_rail_uses_adapter_owned_route_provider() -> None:
    backend = _Backend()
    agent = _Agent(_AbilityManager(), _PromptBuilder())
    rail = A2AOutboundToolkitRail(
        backend_provider=lambda: backend,
        runtime_route=lambda: ("persistent-session", "web"),
    )
    rail.init(agent)

    result = await agent.ability_manager.tools["a2a_find_agents"].invoke(
        {"query": "weather"}
    )

    assert result["session_id"] == "persistent-session"
    assert result["channel_id"] == "web"


@pytest.mark.asyncio
async def test_toolkit_missing_route_is_not_reported_as_remote_rejection() -> None:
    toolkit = A2AOutboundToolkit(_Backend(), runtime_route=lambda: ("", "web"))

    result = await toolkit.find_agents(query="weather")

    assert result["ok"] is False
    assert result["error_code"] == A2AOutboundErrorCode.MANAGER_UNAVAILABLE.value


@pytest.mark.asyncio
async def test_gateway_bridge_binds_wire_session_instead_of_caller_params() -> None:
    calls = []

    class _Manager:
        async def outbound_dispatch_task(self, **kwargs):
            calls.append(kwargs)
            return {"ok": True, "dispatch_id": "disp-1"}

    handler = object.__new__(MessageHandler)
    handler._a2a_outbound_tool_manager = _Manager()
    replies = []

    async def publish(message):
        replies.append(message)

    handler.publish_user_messages = publish
    chunk = SimpleNamespace(
        payload={
            "id": "rpc-1",
            "method": A2A_TOOL_DISPATCH_TASK,
            "params": {
                "agent_id": "agent-1",
                "task": "secret task body",
                "mode": "sync",
                "sessionId": "spoofed",
            },
        },
        channel_id="web",
    )

    handled = await handler._handle_a2a_outbound_tool_push(
        chunk=chunk, session_id="trusted-session"
    )

    assert handled is True
    assert calls[0]["source_session_id"] == "trusted-session"
    assert replies[0].params["response"]["result"]["dispatch_id"] == "disp-1"


@pytest.mark.asyncio
async def test_gateway_bridge_handles_canonical_acp_output_request_wire() -> None:
    calls = []

    class _Manager:
        async def outbound_find_agents(self, **kwargs):
            calls.append(kwargs)
            return {"items": [{"agent_id": "agent-1"}], "total": 1}

    handler = object.__new__(MessageHandler)
    handler._a2a_outbound_tool_manager = _Manager()
    replies = []

    async def publish(message):
        replies.append(message)

    handler.publish_user_messages = publish
    response = E2AResponse(
        request_id="acp-out-1",
        response_id="acp-response-1",
        response_kind=E2A_RESPONSE_KIND_ACP_OUTPUT_REQUEST,
        status=E2A_RESPONSE_STATUS_IN_PROGRESS,
        is_final=False,
        session_id="trusted-session",
        channel="web",
        body={
            "jsonrpc": "2.0",
            "id": "rpc-1",
            "method": "a2a.outbound.tool.find_agents",
            "params": {"query": "weather", "limit": 5},
        },
    )
    push = response.to_dict()
    push["channel_id"] = "web"
    push["payload"] = dict(response.body)
    wire = build_server_push_wire(push)
    chunk = parse_agent_server_wire_chunk(wire)

    handled = await handler._handle_a2a_outbound_tool_push(
        chunk=chunk,
        session_id=wire["session_id"],
    )

    assert handled is True
    assert calls == [{"query": "weather", "required_skills": None, "limit": 5}]
    assert replies[0].params["response"]["result"]["total"] == 1


@pytest.mark.asyncio
async def test_all_a2a_tools_reverse_rpc_round_trip_complete_original_futures(
    monkeypatch,
) -> None:
    output_manager = get_acp_output_manager()
    output_manager.reset_state()

    class _Manager:
        dispatch_modes = []

        async def outbound_find_agents(self, **kwargs):
            assert kwargs == {
                "query": "weather",
                "required_skills": ["weather"],
                "limit": 3,
            }
            return {"items": [{"agent_id": "agent-1"}], "total": 1}

        async def outbound_dispatch_task(self, **kwargs):
            assert kwargs["agent_id"] == "agent-1"
            assert kwargs["source_session_id"] == "trusted-session"
            self.dispatch_modes.append(kwargs["mode"])
            if kwargs["mode"] == "async":
                return {"ok": True, "dispatch_id": "disp-2", "status": "accepted"}
            assert kwargs["task"] == "query weather"
            assert kwargs["reason"] == "diagnostic"
            return {"ok": True, "dispatch_id": "disp-1", "status": "completed"}

        async def outbound_get_dispatch(self, **kwargs):
            assert kwargs == {
                "dispatch_id": "disp-1",
                "source_session_id": "trusted-session",
            }
            return {"ok": True, "dispatch_id": "disp-1", "status": "completed"}

    handler = object.__new__(MessageHandler)
    handler._a2a_outbound_tool_manager = _Manager()

    async def publish(reply):
        envelope = message_to_e2a(reply)
        parsed = parse_inbound(json.dumps(envelope.to_dict(), ensure_ascii=False))
        assert parsed.ok is True
        request = parsed.request
        assert request is not None
        assert request.req_method.value == "acp.tool_response"
        assert output_manager.complete_jsonrpc_response(
            request.params["jsonrpc_id"], request.params["response"]
        )

    handler.publish_user_messages = publish

    async def send_push(push):
        wire = build_server_push_wire(push)
        chunk = parse_agent_server_wire_chunk(wire)
        handled = await handler._handle_a2a_outbound_tool_push(
            chunk=chunk,
            session_id=wire.get("session_id"),
        )
        assert handled is True
        return True

    monkeypatch.setattr(output_manager, "_send_push_callback", send_push)
    try:
        found = await output_manager.send_jsonrpc_request(
            A2A_TOOL_FIND_AGENTS,
            {"query": "weather", "required_skills": ["weather"], "limit": 3},
            session_id="trusted-session",
            channel_id="web",
            timeout=0.5,
            log_params=False,
        )
        dispatched = await output_manager.send_jsonrpc_request(
            A2A_TOOL_DISPATCH_TASK,
            {
                "agent_id": "agent-1",
                "task": "query weather",
                "mode": "sync",
                "reason": "diagnostic",
            },
            session_id="trusted-session",
            channel_id="web",
            timeout=0.5,
            log_params=False,
        )
        dispatched_async = await output_manager.send_jsonrpc_request(
            A2A_TOOL_DISPATCH_TASK,
            {
                "agent_id": "agent-1",
                "task": "query weather later",
                "mode": "async",
            },
            session_id="trusted-session",
            channel_id="web",
            timeout=0.5,
            log_params=False,
        )
        queried = await output_manager.send_jsonrpc_request(
            A2A_TOOL_GET_DISPATCH,
            {"dispatch_id": "disp-1"},
            session_id="trusted-session",
            channel_id="web",
            timeout=0.5,
            log_params=False,
        )
    finally:
        output_manager.reset_state()

    assert found["result"]["total"] == 1
    assert dispatched["result"]["dispatch_id"] == "disp-1"
    assert dispatched_async["result"]["status"] == "accepted"
    assert handler._a2a_outbound_tool_manager.dispatch_modes == ["sync", "async"]
    assert queried["result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_canonical_reverse_rpc_cancel_reaches_and_stops_gateway_call(
    monkeypatch,
) -> None:
    output_manager = get_acp_output_manager()
    output_manager.reset_state()
    started = asyncio.Event()
    stopped = asyncio.Event()

    class _Manager:
        async def outbound_dispatch_task(self, **_kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

    handler = object.__new__(MessageHandler)
    handler._a2a_outbound_tool_manager = _Manager()
    handler._active_a2a_outbound_tool_tasks = {}
    gateway_tasks: list[asyncio.Task] = []
    responses = []

    async def publish(reply):
        responses.append(reply.params["response"])
        envelope = message_to_e2a(reply)
        parsed = parse_inbound(json.dumps(envelope.to_dict(), ensure_ascii=False))
        assert parsed.ok is True and parsed.request is not None
        assert output_manager.complete_jsonrpc_response(
            parsed.request.params["jsonrpc_id"],
            parsed.request.params["response"],
        )

    handler.publish_user_messages = publish

    async def send_push(push):
        wire = build_server_push_wire(push)
        chunk = parse_agent_server_wire_chunk(wire)
        task = asyncio.create_task(
            handler._handle_a2a_outbound_tool_push(
                chunk=chunk,
                session_id=wire.get("session_id"),
            )
        )
        gateway_tasks.append(task)
        return True

    monkeypatch.setattr(output_manager, "_send_push_callback", send_push)
    request = asyncio.create_task(
        output_manager.send_jsonrpc_request(
            A2A_TOOL_DISPATCH_TASK,
            {
                "agent_id": "agent-1",
                "task": "slow task",
                "mode": "sync",
            },
            session_id="trusted-session",
            channel_id="web",
            timeout=30,
            log_params=False,
            cancel_method=A2A_TOOL_CANCEL_CALL,
        )
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=0.5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        await asyncio.wait_for(stopped.wait(), timeout=0.5)
        await asyncio.gather(*gateway_tasks, return_exceptions=True)
    finally:
        output_manager.reset_state()

    assert any(item.get("result") == {"canceled": True} for item in responses)
    assert handler._active_a2a_outbound_tool_tasks == {}


@pytest.mark.asyncio
async def test_reverse_rpc_emits_cancel_notification_when_tool_is_canceled(
    monkeypatch,
) -> None:
    manager = get_acp_output_manager()
    manager.reset_state()
    pushes = []

    async def send_push(wire):
        pushes.append(wire)
        if wire["body"]["method"] == A2A_TOOL_CANCEL_CALL:
            rpc_id = wire["body"]["id"]
            manager.complete_jsonrpc_response(
                rpc_id, {"jsonrpc": "2.0", "id": rpc_id, "result": {"canceled": True}}
            )

    monkeypatch.setattr(manager, "_send_push_callback", send_push)
    pending = asyncio.create_task(
        manager.send_jsonrpc_request(
            A2A_TOOL_DISPATCH_TASK,
            {"task": "secret"},
            session_id="s1",
            log_params=False,
            cancel_method=A2A_TOOL_CANCEL_CALL,
        )
    )
    while not pushes:
        await asyncio.sleep(0)
    pending.cancel()

    with pytest.raises(asyncio.CancelledError):
        await pending
    assert [item["body"]["method"] for item in pushes] == [
        A2A_TOOL_DISPATCH_TASK,
        A2A_TOOL_CANCEL_CALL,
    ]
    assert pushes[1]["body"]["params"]["jsonrpc_id"] == pushes[0]["body"]["id"]
    manager.reset_state()


@pytest.mark.asyncio
async def test_reverse_rpc_delivery_failure_fails_immediately(monkeypatch) -> None:
    manager = get_acp_output_manager()
    manager.reset_state()

    async def reject_delivery(_wire):
        return False

    monkeypatch.setattr(manager, "_send_push_callback", reject_delivery)
    try:
        with pytest.raises(RuntimeError, match="not delivered"):
            await manager.send_jsonrpc_request(
                A2A_TOOL_FIND_AGENTS,
                {"query": "weather"},
                session_id="s1",
                timeout=30,
                log_params=False,
            )
    finally:
        manager.reset_state()


@pytest.mark.asyncio
async def test_reverse_rpc_ids_remain_unique_across_manager_state_reset(
    monkeypatch,
) -> None:
    manager = get_acp_output_manager()
    manager.reset_state()
    ids = []

    async def complete_delivery(wire):
        rpc_id = wire["body"]["id"]
        ids.append(rpc_id)
        manager.complete_jsonrpc_response(
            rpc_id,
            {"jsonrpc": "2.0", "id": rpc_id, "result": {"items": []}},
        )
        return True

    monkeypatch.setattr(manager, "_send_push_callback", complete_delivery)
    try:
        await manager.send_jsonrpc_request(
            A2A_TOOL_FIND_AGENTS, {}, session_id="s1", timeout=0.5
        )
        manager.reset_state()
        await manager.send_jsonrpc_request(
            A2A_TOOL_FIND_AGENTS, {}, session_id="s1", timeout=0.5
        )
    finally:
        manager.reset_state()

    assert len(set(ids)) == 2
    assert all(rpc_id.startswith("acp-") for rpc_id in ids)


@pytest.mark.asyncio
async def test_reverse_rpc_cancel_during_delivery_clears_pending_and_notifies(
    monkeypatch,
) -> None:
    manager = get_acp_output_manager()
    manager.reset_state()
    delivery_started = asyncio.Event()
    methods = []

    async def send_push(wire):
        method = wire["body"]["method"]
        methods.append(method)
        if method == A2A_TOOL_DISPATCH_TASK:
            delivery_started.set()
            await asyncio.Event().wait()
        else:
            rpc_id = wire["body"]["id"]
            manager.complete_jsonrpc_response(
                rpc_id,
                {"jsonrpc": "2.0", "id": rpc_id, "result": {"canceled": True}},
            )
        return True

    monkeypatch.setattr(manager, "_send_push_callback", send_push)
    request = asyncio.create_task(
        manager.send_jsonrpc_request(
            A2A_TOOL_DISPATCH_TASK,
            {"agent_id": "agent-1", "task": "slow", "mode": "sync"},
            session_id="s1",
            timeout=30,
            log_params=False,
            cancel_method=A2A_TOOL_CANCEL_CALL,
        )
    )
    try:
        await asyncio.wait_for(delivery_started.wait(), timeout=0.5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert manager.pending_count == 0
    finally:
        manager.reset_state()

    assert methods == [A2A_TOOL_DISPATCH_TASK, A2A_TOOL_CANCEL_CALL]


@pytest.mark.asyncio
async def test_reverse_rpc_timeout_covers_delivery_stage(monkeypatch) -> None:
    manager = get_acp_output_manager()
    manager.reset_state()

    async def blocked_delivery(_wire):
        await asyncio.Event().wait()

    monkeypatch.setattr(manager, "_send_push_callback", blocked_delivery)
    try:
        with pytest.raises(RuntimeError, match="Failed to send ACP output request"):
            await manager.send_jsonrpc_request(
                A2A_TOOL_FIND_AGENTS,
                {"query": "weather"},
                session_id="s1",
                timeout=0.01,
                log_params=False,
            )
        assert manager.pending_count == 0
    finally:
        manager.reset_state()


@pytest.mark.asyncio
async def test_gateway_cancel_call_cancels_only_same_session_active_rpc() -> None:
    started = asyncio.Event()

    class _Manager:
        async def outbound_dispatch_task(self, **kwargs):
            started.set()
            await asyncio.Event().wait()

    handler = object.__new__(MessageHandler)
    handler._a2a_outbound_tool_manager = _Manager()
    handler._active_a2a_outbound_tool_tasks = {}
    replies = []

    async def publish(message):
        replies.append(message)

    handler.publish_user_messages = publish
    dispatch_chunk = SimpleNamespace(
        payload={
            "id": "rpc-dispatch",
            "method": A2A_TOOL_DISPATCH_TASK,
            "params": {"agent_id": "agent-1", "task": "work", "mode": "sync"},
        },
        channel_id="web",
    )
    dispatch_call = asyncio.create_task(
        handler._handle_a2a_outbound_tool_push(chunk=dispatch_chunk, session_id="s1")
    )
    await started.wait()

    wrong_session = SimpleNamespace(
        payload={
            "id": "rpc-cancel-other",
            "method": A2A_TOOL_CANCEL_CALL,
            "params": {"jsonrpc_id": "rpc-dispatch"},
        },
        channel_id="web",
    )
    await handler._handle_a2a_outbound_tool_push(
        chunk=wrong_session, session_id="other"
    )
    assert dispatch_call.done() is False

    same_session = SimpleNamespace(
        payload={
            "id": "rpc-cancel-own",
            "method": A2A_TOOL_CANCEL_CALL,
            "params": {"jsonrpc_id": "rpc-dispatch"},
        },
        channel_id="web",
    )
    await handler._handle_a2a_outbound_tool_push(chunk=same_session, session_id="s1")
    with pytest.raises(asyncio.CancelledError):
        await dispatch_call
    assert replies[-1].params["response"]["result"]["canceled"] is True
