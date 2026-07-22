import asyncio
import json
import logging
import os
import re
import time
import textwrap
from pathlib import Path

import pytest
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.single_agent import AgentCard
from websockets.exceptions import ConnectionClosedError

from jiuwenswarm.common.e2a.gateway_normalize import (
    build_fallback_e2a,
    e2a_from_agent_fields,
)
from jiuwenswarm.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenswarm.common.schema.agent import AgentResponse
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer
from jiuwenswarm.integrations.ai4research_subscription.codex_process import (
    CodexProcessRunner,
)
from jiuwenswarm.integrations.ai4research_subscription.constants import (
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.model_client import (
    CodexSubscriptionModelClient,
)
from jiuwenswarm.integrations.ai4research_subscription.locking import (
    acquire_profile_lock,
    release_profile_lock,
)
from jiuwenswarm.integrations.ai4research_subscription.profiles import (
    ensure_codex_profile,
)
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm


class FakeWebSocket:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


class ClosedFakeWebSocket:
    remote_address = ("127.0.0.1", 1)

    async def send(self, payload: str) -> None:
        raise ConnectionClosedError(None, None)


class _AgentWsTestHarness(AgentWebSocketServer):
    async def handle_message_for_test(self, ws, raw: str, send_lock: asyncio.Lock) -> None:
        await self._handle_message(ws, raw, send_lock)


class ClosedDuringUnaryServer(_AgentWsTestHarness):
    async def _handle_unary(self, ws, request, send_lock) -> None:
        raise ConnectionClosedError(None, None)


class _FakeInterruptAgent:
    async def process_message(self, request):
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"event_type": "chat.interrupt_result", "success": True},
        )


class _CleanupRecordingAgentManager:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str]] = []
        self.agent = _FakeInterruptAgent()

    def get_agent_nowait(self, *_args, **_kwargs):
        return self.agent

    async def get_agent(self, **_kwargs):
        return self.agent

    async def cleanup_session_runtime(self, *, channel_id: str, session_id: str) -> bool:
        self.cleaned.append((channel_id, session_id))
        return True


class _NoCreateCleanupAgentManager:
    def __init__(self) -> None:
        self.cleaned: list[tuple[str, str]] = []

    def get_agent_nowait(self, *_args, **_kwargs):
        return None

    async def get_agent(self, **_kwargs):
        raise AssertionError("client disconnect cancel must not create an agent")

    async def cleanup_session_runtime(self, *, channel_id: str, session_id: str) -> bool:
        self.cleaned.append((channel_id, session_id))
        return False


@pytest.mark.asyncio
async def test_cancel_terminal_waits_for_exact_outer_stream_cleanup() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    server._agent_manager = _CleanupRecordingAgentManager()
    ws = FakeWebSocket()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def stream_owner() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await allow_cleanup.wait()
            raise

    stream_task = asyncio.create_task(stream_owner())
    await asyncio.sleep(0)
    stream_task.cancel()
    await cleanup_started.wait()
    request = e2a_from_agent_fields(
        request_id="cancel-after-cleanup",
        channel_id="tui",
        session_id="sess",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.fast"},
        is_stream=False,
        timestamp=0.0,
    )
    handle = asyncio.create_task(
        server._handle_cancel(
            ws,
            e2a_to_agent_request(request),
            asyncio.Lock(),
            stream_tasks=[stream_task],
            deadline=time.monotonic() + 2,
        )
    )
    await asyncio.sleep(0)
    assert ws.sent == []
    assert not handle.done()

    allow_cleanup.set()
    await handle
    assert len(ws.sent) == 1


@pytest.mark.asyncio
async def test_cancel_cleanup_deadline_emits_typed_false_terminal() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    server._agent_manager = _CleanupRecordingAgentManager()
    ws = FakeWebSocket()
    release = asyncio.Event()

    async def stream_owner() -> None:
        await release.wait()

    stream_task = asyncio.create_task(stream_owner())
    request = e2a_from_agent_fields(
        request_id="cancel-timeout",
        channel_id="tui",
        session_id="sess",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.fast"},
        is_stream=False,
        timestamp=0.0,
    )
    await server._handle_cancel(
        ws,
        e2a_to_agent_request(request),
        asyncio.Lock(),
        stream_tasks=[stream_task],
        deadline=time.monotonic() - 1,
    )
    assert len(ws.sent) == 1
    encoded = json.dumps(ws.sent[0])
    assert "cancel_cleanup_timeout" in encoded
    assert '"success": false' in encoded

    release.set()
    await stream_task


@pytest.mark.asyncio
async def test_cancel_deadline_does_not_cancel_stalled_adapter_cleanup() -> None:
    cleanup_started = asyncio.Event()
    cleanup_release = asyncio.Event()
    cleanup_finished = asyncio.Event()
    cancellation_count = 0

    class StalledCleanupAgent:
        async def process_message(self, request):
            nonlocal cancellation_count
            cleanup_started.set()
            try:
                await cleanup_release.wait()
            except asyncio.CancelledError:
                cancellation_count += 1
                raise
            finally:
                cleanup_finished.set()
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"event_type": "chat.interrupt_result", "success": True},
            )

    class Manager:
        def get_agent_nowait(self, *_args, **_kwargs):
            return StalledCleanupAgent()

    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    server._agent_manager = Manager()
    server._session_stream_tasks = {}
    server._cancel_cleanup_tasks = set()
    ws = FakeWebSocket()
    request = e2a_from_agent_fields(
        request_id="cancel-stalled-adapter",
        channel_id="tui",
        session_id="sess",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.fast"},
        is_stream=False,
        timestamp=0.0,
    )

    await server._handle_cancel(
        ws,
        e2a_to_agent_request(request),
        asyncio.Lock(),
        deadline=time.monotonic() + 0.02,
    )

    assert cleanup_started.is_set()
    assert len(server._cancel_cleanup_tasks) == 1
    assert cancellation_count == 0
    assert len(ws.sent) == 1
    encoded = json.dumps(ws.sent[0])
    assert "cancel_cleanup_timeout" in encoded
    assert '"success": false' in encoded

    cleanup_release.set()
    await asyncio.wait_for(cleanup_finished.wait(), timeout=1)
    await asyncio.sleep(0)
    assert cancellation_count == 0
    assert server._cancel_cleanup_tasks == set()


@pytest.mark.asyncio
@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group test")
async def test_cancel_barrier_crosses_real_production_stream_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cancel one real provider group through every production stream owner.

    The synchronized snapshot proves that the active model-call task, the
    JiuWenSwarm facade queue producer, and the AgentServer stream/send owner are
    three distinct pending tasks.  The interrupt is sent through AgentServer,
    not directly to the owner.  Its wire terminal is accepted only after the
    real leader and child are gone and DeepAdapter has released its turn owner.
    """
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.profiles.get_user_workspace_dir",
        lambda: workspace,
    )
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    auth_path.write_text("not-a-real-token", encoding="utf-8")
    auth_path.chmod(0o600)
    binary = tmp_path / "codex"
    binary.write_text(
        textwrap.dedent(
            r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, time
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
pathlib.Path(__file__).with_name("barrier-pids.json").write_text(
 json.dumps({"pids":[os.getpid(), child.pid], "pgids":[os.getpgid(0), os.getpgid(child.pid)]})
)
time.sleep(60)
'''
        ),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    runner = CodexProcessRunner(binary_path=binary)
    model = Model(
        model_client_config=ModelClientConfig(
            client_id="production-chain-cancel",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=30,
            max_retries=0,
        ),
        model_config=ModelRequestConfig(
            model_name=CODEX_MODEL_ALIAS,
            temperature=0,
        ),
    )
    assert isinstance(model._client, CodexSubscriptionModelClient)
    model._client._runner = runner
    deep_agent = interface_deep_module.create_deep_agent(
        model=model,
        card=AgentCard(id="production-chain-cancel", name="production-chain-cancel"),
        tools=[],
        rails=[],
        max_iterations=3,
        parallel_tool_calls=False,
        enable_llm_retry_rail=False,
        enable_read_image_multimodal=False,
        enable_task_loop=False,
        add_general_purpose_agent=False,
        auto_create_workspace=False,
    )
    await deep_agent.ensure_initialized()

    adapter = interface_deep_module.JiuWenSwarmDeepAdapter()
    adapter._instance = deep_agent
    adapter._is_session_scoped_adapter = True
    adapter._model = model
    adapter._model_request_config = model.model_config
    adapter._model_client_config = model.model_client_config
    adapter._model_cache = {CODEX_MODEL_ALIAS: model}
    adapter._model_canonical_key_by_object_id = {id(model): CODEX_MODEL_ALIAS}
    adapter._config_cache = {}
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda _name: True)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: model)

    async def no_slash(*_args, **_kwargs):
        return None

    async def no_async_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(adapter, "_handle_slash_command", no_slash)
    monkeypatch.setattr(adapter, "_update_runtime_config", no_async_side_effect)
    monkeypatch.setattr(adapter, "_sync_prompt_attachments_for_request", no_async_side_effect)
    await adapter.start_interaction("sess")

    facade = JiuWenSwarm()
    facade._adapter = adapter
    facade._sdk_name = "deep"
    monkeypatch.setattr(
        facade,
        "_build_inputs",
        lambda _request: (
            {"query": "cancel", "conversation_id": "sess"},
            "local",
            "cancel",
        ),
    )
    monkeypatch.setattr(facade, "_cancel_team_work_for_session", no_async_side_effect)

    class Manager:
        def get_agent_nowait(self, *_args, **_kwargs):
            return facade

        async def get_agent(self, **_kwargs):
            return facade

    class ProductionChainServer(_AgentWsTestHarness):
        async def _trigger_before_chat_request_hook(self, _request):
            return None

        async def _prepare_code_mode_chat_turn(self, _request, _channel_id):
            return "agent", "fast", facade

        async def _ensure_code_mode_state(self, *_args, **_kwargs):
            return False

        async def _check_post_process_plan_exit(self, *_args, **_kwargs):
            return None

    pid_path = tmp_path / "barrier-pids.json"
    terminal_snapshots: list[dict[str, object]] = []

    class CleanupAwareWebSocket(FakeWebSocket):
        async def send(self, payload):
            decoded = json.loads(payload)
            encoded = json.dumps(decoded)
            if "chat.interrupt_result" in encoded:
                evidence = json.loads(pid_path.read_text(encoding="utf-8"))
                terminal_snapshots.append(
                    {
                        "pids_absent": all(
                            not Path(f"/proc/{pid}").exists()
                            for pid in evidence["pids"]
                        ),
                        "owners_empty": adapter._codex_turn_owners == {},
                        "turns_empty": list(profile.turns_dir.iterdir()) == [],
                    }
                )
            self.sent.append(decoded)

    server = ProductionChainServer.__new__(ProductionChainServer)
    server._agent_manager = Manager()
    server._session_stream_tasks = {}
    server._cancel_cleanup_tasks = set()
    ws = CleanupAwareWebSocket()
    send_lock = asyncio.Lock()
    chat = e2a_from_agent_fields(
        request_id="original-request",
        channel_id="tui",
        session_id="sess",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "query": "cancel",
            "mode": "agent.fast",
            "model_name": CODEX_MODEL_ALIAS,
        },
        is_stream=True,
        timestamp=0.0,
    )
    server_stream_task = asyncio.create_task(
        server.handle_message_for_test(
            ws,
            json.dumps(chat.to_dict(), ensure_ascii=False),
            send_lock,
        )
    )

    pid_path = tmp_path / "barrier-pids.json"
    for _ in range(200):
        if pid_path.exists():
            break
        await asyncio.sleep(0.01)
    assert pid_path.exists()
    owner = adapter._codex_turn_owners["sess"]
    model_call_task = owner._model_call_task
    facade_tasks = [
        task
        for task in asyncio.all_tasks()
        if not task.done()
        and "JiuWenSwarm.process_message_stream.<locals>.run_stream_task"
        in getattr(task.get_coro(), "__qualname__", "")
    ]
    assert len(facade_tasks) == 1
    facade_queue_task = facade_tasks[0]
    assert model_call_task is not None and not model_call_task.done()
    assert not facade_queue_task.done()
    assert server_stream_task in server._session_stream_tasks["sess"]
    assert len({model_call_task, facade_queue_task, server_stream_task}) == 3

    cancel = e2a_from_agent_fields(
        request_id="cancel-request",
        channel_id="tui",
        session_id="sess",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.fast"},
        is_stream=False,
        timestamp=0.0,
    )
    await server.handle_message_for_test(
        ws,
        json.dumps(cancel.to_dict(), ensure_ascii=False),
        send_lock,
    )
    await asyncio.wait_for(server_stream_task, timeout=5)

    evidence = json.loads(pid_path.read_text(encoding="utf-8"))
    assert len(set(evidence["pgids"])) == 1
    for _ in range(100):
        if all(not Path(f"/proc/{pid}").exists() for pid in evidence["pids"]):
            break
        await asyncio.sleep(0.01)
    assert all(not Path(f"/proc/{pid}").exists() for pid in evidence["pids"])
    assert list(profile.turns_dir.iterdir()) == []
    lock_handle = acquire_profile_lock(profile)
    release_profile_lock(lock_handle)
    assert adapter._codex_turn_owners == {}
    assert terminal_snapshots == [
        {"pids_absent": True, "owners_empty": True, "turns_empty": True}
    ]
    interrupt_frames = [
        frame for frame in ws.sent if "chat.interrupt_result" in json.dumps(frame)
    ]
    assert len(interrupt_frames) == 1
    encoded = json.dumps(interrupt_frames[0])
    assert '"success": true' in encoded
    assert any(
        item.get("event") == "cleanup_finished" and item.get("cleanup_complete")
        for item in runner.lifecycle_evidence
    )

    processors = list(facade._session_manager._session_processors.values())
    for processor in processors:
        processor.cancel()
    await asyncio.gather(*processors, return_exceptions=True)
    await adapter.stop_interaction()
    await deep_agent.react_agent.clear_session("sess")


@pytest.mark.asyncio
async def test_same_session_history_reaches_codex_prompt_through_real_ws_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Two completed chat turns in one session must serialize turn-1 facts
    into turn-2's provider prompt through the real AgentServer stream chain.

    This is the live-path continuity seam the direct-Runner tests cannot see:
    AgentWebSocketServer -> facade/session queue -> DeepAdapter -> real
    DeepAgent/Runner -> call-bound model -> CodexSubscriptionModelClient ->
    real CodexProcessRunner child, capturing the exact stdin prompt.
    """
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.profiles.get_user_workspace_dir",
        lambda: workspace,
    )
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    auth_path.write_text("not-a-real-token", encoding="utf-8")
    auth_path.chmod(0o600)
    binary = tmp_path / "codex"
    binary.write_text(
        textwrap.dedent(
            r'''#!/usr/bin/env python3
import json, os, pathlib, sys, time
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
prompt = sys.stdin.read()
pathlib.Path(__file__).with_name(f"prompt-capture-{time.time_ns()}.txt").write_text(
 prompt, encoding="utf-8")
final = {"content": "Context stored.", "reasoning_content": "",
 "tool_calls": [], "finish_reason": "stop"}
for event in (
 {"type": "thread.started", "thread_id": "diag"},
 {"type": "turn.started"},
 {"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(final)}},
 {"type": "turn.completed", "usage": {"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}},
):
 print(json.dumps(event))
'''
        ),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    runner = CodexProcessRunner(binary_path=binary)
    model = Model(
        model_client_config=ModelClientConfig(
            client_id="history-continuity-probe",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=30,
            max_retries=0,
        ),
        model_config=ModelRequestConfig(
            model_name=CODEX_MODEL_ALIAS,
            temperature=0,
        ),
    )
    assert isinstance(model._client, CodexSubscriptionModelClient)
    model._client._runner = runner
    deep_agent = interface_deep_module.create_deep_agent(
        model=model,
        card=AgentCard(id="history-continuity-probe", name="history-continuity-probe"),
        tools=[],
        rails=[],
        max_iterations=3,
        parallel_tool_calls=False,
        enable_llm_retry_rail=False,
        enable_read_image_multimodal=False,
        enable_task_loop=False,
        add_general_purpose_agent=False,
        auto_create_workspace=False,
    )
    await deep_agent.ensure_initialized()

    adapter = interface_deep_module.JiuWenSwarmDeepAdapter()
    adapter._instance = deep_agent
    adapter._is_session_scoped_adapter = True
    adapter._model = model
    adapter._model_request_config = model.model_config
    adapter._model_client_config = model.model_client_config
    adapter._model_cache = {CODEX_MODEL_ALIAS: model}
    adapter._model_canonical_key_by_object_id = {id(model): CODEX_MODEL_ALIAS}
    adapter._config_cache = {}
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda _name: True)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: model)

    async def no_slash(*_args, **_kwargs):
        return None

    async def no_async_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(adapter, "_handle_slash_command", no_slash)
    monkeypatch.setattr(adapter, "_update_runtime_config", no_async_side_effect)
    monkeypatch.setattr(
        adapter, "_sync_prompt_attachments_for_request", no_async_side_effect
    )
    await adapter.start_interaction("history-sess")

    facade = JiuWenSwarm()
    facade._adapter = adapter
    facade._sdk_name = "deep"

    def build_inputs(request):
        query = request.params.get("query")
        return ({"query": query, "conversation_id": "history-sess"}, "local", query)

    monkeypatch.setattr(facade, "_build_inputs", build_inputs)
    monkeypatch.setattr(facade, "_cancel_team_work_for_session", no_async_side_effect)

    class Manager:
        def get_agent_nowait(self, *_args, **_kwargs):
            return facade

        async def get_agent(self, **_kwargs):
            return facade

    class ProductionChainServer(_AgentWsTestHarness):
        async def _trigger_before_chat_request_hook(self, _request):
            return None

        async def _prepare_code_mode_chat_turn(self, _request, _channel_id):
            return "agent", "fast", facade

        async def _ensure_code_mode_state(self, *_args, **_kwargs):
            return False

        async def _check_post_process_plan_exit(self, *_args, **_kwargs):
            return None

    server = ProductionChainServer.__new__(ProductionChainServer)
    server._agent_manager = Manager()
    server._session_stream_tasks = {}
    server._cancel_cleanup_tasks = set()
    ws = FakeWebSocket()
    send_lock = asyncio.Lock()

    fact_query = (
        "For this conversation, the fictional project is Atlas, its launch day is "
        "Tuesday, and I prefer concise bullet points. Confirm with ATLAS_CONTEXT_STORED."
    )
    recall_query = (
        "Without asking me to repeat the setup, state the launch day I gave you and "
        "end with RECALL_CHECK_OK."
    )
    try:
        for index, query in enumerate((fact_query, recall_query), start=1):
            chat = e2a_from_agent_fields(
                request_id=f"history-turn-{index}",
                channel_id="tui",
                session_id="history-sess",
                req_method=ReqMethod.CHAT_SEND,
                params={
                    "query": query,
                    "mode": "agent.fast",
                    "model_name": CODEX_MODEL_ALIAS,
                },
                is_stream=True,
                timestamp=0.0,
            )
            await asyncio.wait_for(
                server.handle_message_for_test(
                    ws,
                    json.dumps(chat.to_dict(), ensure_ascii=False),
                    send_lock,
                ),
                timeout=30,
            )
    finally:
        processors = list(facade._session_manager._session_processors.values())
        for processor in processors:
            processor.cancel()
        await asyncio.gather(*processors, return_exceptions=True)
        await adapter.stop_interaction()
        await deep_agent.react_agent.clear_session("history-sess")

    captures = sorted(tmp_path.glob("prompt-capture-*.txt"))
    assert len(captures) == 2, f"expected 2 provider prompts, saw {len(captures)}"
    header_pattern = re.compile(
        r"<<<JIUWEN_MSG (\d+)/(\d+) role=(system|developer|user|assistant|tool)>>>"
    )

    def parse(prompt: str) -> list[dict]:
        lines = prompt.split("\n")
        parsed = []
        for position, line in enumerate(lines):
            header = header_pattern.fullmatch(line)
            if header is not None:
                parsed.append(
                    {
                        "role": header.group(3),
                        "content": json.loads(lines[position + 1]),
                    }
                )
        return parsed

    first_turn = parse(captures[0].read_text(encoding="utf-8"))
    second_turn = parse(captures[1].read_text(encoding="utf-8"))
    first_roles = [message["role"] for message in first_turn]
    second_roles = [message["role"] for message in second_turn]

    assert any(
        fact_query in message["content"]
        for message in first_turn
        if message["role"] == "user"
    ), f"turn-1 prompt lost the user fact; roles={first_roles}"
    second_contents = " || ".join(
        f"{message['role']}:{message['content'][:120]}" for message in second_turn
    )
    assert any(
        fact_query in message["content"]
        for message in second_turn
        if message["role"] == "user"
    ), (
        "turn-2 prompt lost the turn-1 user fact; "
        f"roles={second_roles}; messages={second_contents}"
    )
    assert any(
        "Context stored." in message["content"]
        for message in second_turn
        if message["role"] == "assistant"
    ), (
        "turn-2 prompt lost the turn-1 assistant reply; "
        f"roles={second_roles}; messages={second_contents}"
    )
    assert recall_query in second_turn[-1]["content"], (
        "turn-2 prompt does not end with the current recall query; "
        f"roles={second_roles}; messages={second_contents}"
    )
    assert second_roles.count("user") >= 2, (
        f"turn-2 prompt did not accumulate history; roles={second_roles}; "
        f"messages={second_contents}"
    )


@pytest.mark.asyncio
async def test_codex_timeout_error_reaches_wire_with_typed_route_receipts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The r12b live failure: OpenJiuwen converts the model-call timeout into a
    stream error chunk, so the wire chat.error lost its typed code and
    provider/consumer route receipts. The turn owner must restore them."""
    workspace = tmp_path / "instance"
    workspace.mkdir(mode=0o700)
    monkeypatch.setattr(
        "jiuwenswarm.integrations.ai4research_subscription.profiles.get_user_workspace_dir",
        lambda: workspace,
    )
    profile = ensure_codex_profile()
    auth_path = profile.root / "auth.json"
    auth_path.write_text("not-a-real-token", encoding="utf-8")
    auth_path.chmod(0o600)
    binary = tmp_path / "codex"
    binary.write_text(
        textwrap.dedent(
            r'''#!/usr/bin/env python3
import sys, time
if "--version" in sys.argv:
 print("codex-cli 0.144.5"); raise SystemExit(0)
time.sleep(60)
'''
        ),
        encoding="utf-8",
    )
    binary.chmod(0o700)
    runner = CodexProcessRunner(binary_path=binary)
    model = Model(
        model_client_config=ModelClientConfig(
            client_id="timeout-receipt-probe",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=1,
            max_retries=0,
        ),
        model_config=ModelRequestConfig(
            model_name=CODEX_MODEL_ALIAS,
            temperature=0,
        ),
    )
    assert isinstance(model._client, CodexSubscriptionModelClient)
    model._client._runner = runner
    deep_agent = interface_deep_module.create_deep_agent(
        model=model,
        card=AgentCard(id="timeout-receipt-probe", name="timeout-receipt-probe"),
        tools=[],
        rails=[],
        max_iterations=3,
        parallel_tool_calls=False,
        enable_llm_retry_rail=False,
        enable_read_image_multimodal=False,
        enable_task_loop=False,
        add_general_purpose_agent=False,
        auto_create_workspace=False,
    )
    await deep_agent.ensure_initialized()

    adapter = interface_deep_module.JiuWenSwarmDeepAdapter()
    adapter._instance = deep_agent
    adapter._is_session_scoped_adapter = True
    adapter._model = model
    adapter._model_request_config = model.model_config
    adapter._model_client_config = model.model_client_config
    adapter._model_cache = {CODEX_MODEL_ALIAS: model}
    adapter._model_canonical_key_by_object_id = {id(model): CODEX_MODEL_ALIAS}
    adapter._config_cache = {}
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda _name: True)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: model)

    async def no_slash(*_args, **_kwargs):
        return None

    async def no_async_side_effect(*_args, **_kwargs):
        return None

    monkeypatch.setattr(adapter, "_handle_slash_command", no_slash)
    monkeypatch.setattr(adapter, "_update_runtime_config", no_async_side_effect)
    monkeypatch.setattr(
        adapter, "_sync_prompt_attachments_for_request", no_async_side_effect
    )
    await adapter.start_interaction("timeout-sess")

    facade = JiuWenSwarm()
    facade._adapter = adapter
    facade._sdk_name = "deep"

    def build_inputs(request):
        query = request.params.get("query")
        return ({"query": query, "conversation_id": "timeout-sess"}, "local", query)

    monkeypatch.setattr(facade, "_build_inputs", build_inputs)
    monkeypatch.setattr(facade, "_cancel_team_work_for_session", no_async_side_effect)

    class Manager:
        def get_agent_nowait(self, *_args, **_kwargs):
            return facade

        async def get_agent(self, **_kwargs):
            return facade

    class ProductionChainServer(_AgentWsTestHarness):
        async def _trigger_before_chat_request_hook(self, _request):
            return None

        async def _prepare_code_mode_chat_turn(self, _request, _channel_id):
            return "agent", "fast", facade

        async def _ensure_code_mode_state(self, *_args, **_kwargs):
            return False

        async def _check_post_process_plan_exit(self, *_args, **_kwargs):
            return None

    server = ProductionChainServer.__new__(ProductionChainServer)
    server._agent_manager = Manager()
    server._session_stream_tasks = {}
    server._cancel_cleanup_tasks = set()
    ws = FakeWebSocket()
    chat = e2a_from_agent_fields(
        request_id="timeout-turn-1",
        channel_id="tui",
        session_id="timeout-sess",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "query": "Produce a long answer.",
            "mode": "agent.fast",
            "model_name": CODEX_MODEL_ALIAS,
        },
        is_stream=True,
        timestamp=0.0,
    )
    try:
        await asyncio.wait_for(
            server.handle_message_for_test(
                ws,
                json.dumps(chat.to_dict(), ensure_ascii=False),
                send_lock=asyncio.Lock(),
            ),
            timeout=30,
        )
    finally:
        processors = list(facade._session_manager._session_processors.values())
        for processor in processors:
            processor.cancel()
        await asyncio.gather(*processors, return_exceptions=True)
        await adapter.stop_interaction()
        await deep_agent.react_agent.clear_session("timeout-sess")

    encoded_frames = [json.dumps(frame) for frame in ws.sent]
    error_frames = [
        frame
        for frame in ws.sent
        if json.dumps(frame).find('"chat.error"') != -1
    ]
    assert error_frames, f"no chat.error frame on the wire: {encoded_frames}"
    def _frame_payloads(frame: dict) -> list[dict]:
        found = []
        for candidate in (
            frame.get("payload"),
            (frame.get("body") or {}).get("delta")
            if isinstance(frame.get("body"), dict)
            else None,
        ):
            if isinstance(candidate, dict):
                found.append(candidate)
        return found

    error_payloads = [
        payload for frame in error_frames for payload in _frame_payloads(frame)
    ]
    typed = [
        payload
        for payload in error_payloads
        if isinstance(payload, dict) and payload.get("code") == "timeout"
    ]
    assert typed, f"chat.error lacked typed timeout code: {error_payloads}"
    assert typed[0].get("provider") == CODEX_PROVIDER_NAME
    assert typed[0].get("consumer") == "direct_agent_fast"
    assert not any('"chat.final"' in frame for frame in encoded_frames)
    assert adapter._codex_turn_owners == {}


async def _handle_cancel_cleanup_case(env) -> list[tuple[str, str]]:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}

    await server.handle_message_for_test(
        FakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )
    return manager.cleaned


@pytest.mark.asyncio
async def test_handle_message_treats_no_close_frame_as_disconnect(caplog) -> None:
    target_logger = logging.getLogger("jiuwenswarm.server.agent_ws_server")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)
    env = e2a_from_agent_fields(
        request_id="req-closed",
        channel_id="tui",
        session_id="session-1",
        req_method=ReqMethod.CONFIG_GET,
        params={},
        is_stream=False,
        timestamp=0.0,
    )
    try:
        await ClosedDuringUnaryServer().handle_message_for_test(
            FakeWebSocket(),
            json.dumps(env.to_dict(), ensure_ascii=False),
            asyncio.Lock(),
        )
    finally:
        target_logger.removeHandler(caplog.handler)

    assert "no close frame received or sent" in caplog.text
    assert "request_id=req-closed" in caplog.text


@pytest.mark.asyncio
async def test_handle_message_ignores_json_error_when_peer_is_closed(caplog) -> None:
    target_logger = logging.getLogger("jiuwenswarm.server.agent_ws_server")
    target_logger.addHandler(caplog.handler)
    caplog.set_level(logging.INFO, logger=target_logger.name)
    try:
        await _AgentWsTestHarness.__new__(_AgentWsTestHarness).handle_message_for_test(
            ClosedFakeWebSocket(),
            "not-json",
            asyncio.Lock(),
        )
    finally:
        target_logger.removeHandler(caplog.handler)

    assert "JSON" in caplog.text


@pytest.mark.asyncio
async def test_handle_message_reports_json_error_when_peer_is_open() -> None:
    ws = FakeWebSocket()
    await _AgentWsTestHarness.__new__(_AgentWsTestHarness).handle_message_for_test(
        ws,
        "not-json",
        asyncio.Lock(),
    )

    assert ws.sent[0]["status"] == "failed"


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_after_cancel() -> None:
    env = e2a_from_agent_fields(
        request_id="req-disconnect-cancel",
        channel_id="tui",
        session_id="sess-exit",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-exit",
        },
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    assert await _handle_cancel_cleanup_case(env) == [("tui", "sess-exit")]


@pytest.mark.asyncio
async def test_disconnect_cancel_does_not_create_agent_or_send_terminal_when_runtime_missing() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _NoCreateCleanupAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}
    ws = FakeWebSocket()
    env = e2a_from_agent_fields(
        request_id="req-disconnect-no-agent",
        channel_id="tui",
        session_id="sess-no-agent",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-no-agent"},
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        ws,
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-no-agent")]
    assert ws.sent == []


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_when_cancel_reply_send_fails() -> None:
    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    server._session_stream_tasks = {}
    env = e2a_from_agent_fields(
        request_id="req-disconnect-cancel-send-fails",
        channel_id="tui",
        session_id="sess-send-fails",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-send-fails"},
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        ClosedFakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-send-fails")]


@pytest.mark.asyncio
async def test_disconnect_cancel_cleans_session_runtime_when_stream_task_cleanup_fails() -> None:
    async def failing_stream_task() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            raise RuntimeError("stream cleanup failed")

    server = _AgentWsTestHarness.__new__(_AgentWsTestHarness)
    manager = _CleanupRecordingAgentManager()
    server._agent_manager = manager
    stream_task = asyncio.create_task(failing_stream_task())
    server._session_stream_tasks = {"sess-stream-cleanup-fails": {stream_task: asyncio.Event()}}
    env = e2a_from_agent_fields(
        request_id="req-disconnect-stream-cleanup-fails",
        channel_id="tui",
        session_id="sess-stream-cleanup-fails",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-stream-cleanup-fails",
        },
        is_stream=False,
        timestamp=0.0,
    )
    env.channel_context["_jiuwenswarm_cancel_source"] = "client_disconnect"

    await server.handle_message_for_test(
        FakeWebSocket(),
        json.dumps(env.to_dict(), ensure_ascii=False),
        asyncio.Lock(),
    )

    assert manager.cleaned == [("tui", "sess-stream-cleanup-fails")]
    assert stream_task.done() is True


@pytest.mark.asyncio
async def test_cancel_source_param_does_not_trigger_session_runtime_cleanup() -> None:
    env = e2a_from_agent_fields(
        request_id="req-param-source",
        channel_id="tui",
        session_id="sess-param",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "cancel",
            "session_id": "sess-param",
            "cancel_source": "client_disconnect",
        },
        is_stream=False,
        timestamp=0.0,
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_cancel_source_metadata_does_not_trigger_supplement_runtime_cleanup() -> None:
    env = e2a_from_agent_fields(
        request_id="req-metadata-source",
        channel_id="tui",
        session_id="sess-metadata",
        req_method=ReqMethod.CHAT_CANCEL,
        params={
            "intent": "supplement",
            "session_id": "sess-metadata",
        },
        is_stream=False,
        timestamp=0.0,
        metadata={"_jiuwenswarm_cancel_source": "client_disconnect"},
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_legacy_metadata_cancel_source_does_not_trigger_runtime_cleanup() -> None:
    env = build_fallback_e2a(
        {
            "request_id": "req-legacy-metadata-source",
            "channel_id": "tui",
            "session_id": "sess-legacy-metadata",
            "req_method": ReqMethod.CHAT_CANCEL.value,
            "params": {
                "intent": "cancel",
                "session_id": "sess-legacy-metadata",
            },
            "is_stream": False,
            "timestamp": 0.0,
            "metadata": {"_jiuwenswarm_cancel_source": "client_disconnect"},
        }
    )

    assert await _handle_cancel_cleanup_case(env) == []


@pytest.mark.asyncio
async def test_manual_cancel_keeps_session_runtime() -> None:
    env = e2a_from_agent_fields(
        request_id="req-manual-cancel",
        channel_id="tui",
        session_id="sess-keep",
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "session_id": "sess-keep"},
        is_stream=False,
        timestamp=0.0,
    )

    assert await _handle_cancel_cleanup_case(env) == []
