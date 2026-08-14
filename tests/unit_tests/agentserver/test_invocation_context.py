from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from openjiuwen.core.session.agent import create_agent_session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.deep_agent import DeepAgent
from openjiuwen.harness.schema.config import DeepAgentConfig
from openjiuwen.harness.schema.interaction import SendInputRequest

from jiuwenswarm.agents.harness.common.rails.invocation_context_rail import (
    InvocationContextRail,
    _extract_invocation_context,
)
from jiuwenswarm.common.invocation_context import (
    INVOCATION_CONTEXT_EXTRA_KEY,
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    XiaoyiInvocationContext,
    attach_invocation_context,
    get_current_invocation_context,
    invocation_context_from_dict,
    invocation_context_to_dict,
)
from jiuwenswarm.common.invocation_context.adapters import (
    build_device_command_context_from_invocation,
)
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.server.invocation_context_builder import build_invocation_context
from jiuwenswarm.server.request_context import build_device_context_from_request
from jiuwenswarm.server.gui_rpc.client import build_gui_rpc_request
from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools import utils as device_utils
from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools import (
    xiaoyi_gui_tool as gui_tool,
)


def _context(*, xiaoyi: XiaoyiInvocationContext | None = None) -> InvocationContext:
    return InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id="invocation-1",
        request_id="request-1",
        session_id="session-1",
        channel_id="xiaoyi" if xiaoyi is not None else "web",
        chat_id="chat-1",
        xiaoyi=xiaoyi,
        metadata={"scheduled_device": {"required_intents": ["CreateNote"]}}
        if xiaoyi is not None
        else {},
    )


def test_invocation_context_round_trip_without_xiaoyi() -> None:
    context = _context()
    assert invocation_context_from_dict(invocation_context_to_dict(context)) == context


def test_invocation_context_round_trip_with_xiaoyi_and_unknown_optional_field() -> None:
    context = _context(
        xiaoyi=XiaoyiInvocationContext(
            root_session_id="root",
            params_session_id="params",
            task_id="task",
            message_id="message",
            device_id="device",
            scheduled_device={"required_intents": ["CreateNote"]},
            cron={"job_id": "job", "run_id": "run"},
        )
    )
    payload = invocation_context_to_dict(context)
    payload["unknown_optional"] = {"ignored": True}
    assert invocation_context_from_dict(payload) == context


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"invocation_id": "i"}, "version"),
        ({"version": 2, "invocation_id": "i", "request_id": "r", "channel_id": "c"}, "unsupported"),
        ({"version": 1, "request_id": "r", "channel_id": "c"}, "invocation_id"),
        ({"version": 1, "invocation_id": "i", "channel_id": "c"}, "request_id"),
    ],
)
def test_invocation_context_codec_rejects_invalid_identity(payload, message) -> None:
    with pytest.raises(ValueError, match=message):
        invocation_context_from_dict(payload)


def test_attach_invocation_context_merges_existing_run_context() -> None:
    context = _context()
    inputs = {
        "query": "hello",
        "run": {
            "kind": "cron",
            "context": {"extra": {"raw_query": "x", "foo": "bar"}},
        },
    }
    attached = attach_invocation_context(inputs, context)
    assert attached["run"]["kind"] == "cron"
    assert attached["run"]["context"]["extra"]["raw_query"] == "x"
    payload = attached["run"]["context"]["extra"][INVOCATION_CONTEXT_EXTRA_KEY]
    assert invocation_context_from_dict(payload) == context
    assert INVOCATION_CONTEXT_EXTRA_KEY not in inputs["run"]["context"]["extra"]


def test_builder_and_device_adapter_preserve_routing_fields() -> None:
    request = AgentRequest(
        request_id="request-1",
        channel_id="xiaoyi",
        session_id="jiuwen-1",
        chat_id="root-1",
        params={"session_id": "params-1", "task_id": "task-1"},
        metadata={
            "xiaoyi_root_session_id": "root-override",
            "xiaoyi_params_session_id": "params-override",
            "xiaoyi_task_id": "task-override",
            "xiaoyi_rpc_id": "message-1",
            "xiaoyi_device_id": "device-1",
            "scheduled_device": {"required_intents": ["CreateNote"]},
            "cron": {"job_id": "job-1"},
            "app_id": "app-1",
            "binding_id": "binding-1",
            "ignored": "not copied",
        },
    )
    invocation = build_invocation_context(request)
    legacy = build_device_context_from_request(request)
    device = build_device_command_context_from_invocation(invocation)
    assert device.source_request_id == legacy.source_request_id
    assert device.channel_id == legacy.channel_id
    assert device.jiuwen_session_id == legacy.jiuwen_session_id
    assert device.xiaoyi_root_session_id == legacy.xiaoyi_root_session_id
    assert device.xiaoyi_params_session_id == legacy.xiaoyi_params_session_id
    assert device.xiaoyi_task_id == legacy.xiaoyi_task_id
    assert device.xiaoyi_rpc_id == legacy.xiaoyi_rpc_id
    assert device.metadata == {
        "invocation_id": invocation.invocation_id,
        "app_id": "app-1",
        "binding_id": "binding-1",
        "scheduled_device": legacy.metadata["scheduled_device"],
        "cron": legacy.metadata["cron"],
    }


def test_gui_builder_uses_explicit_invocation() -> None:
    context = _context(
        xiaoyi=XiaoyiInvocationContext(
            root_session_id="root",
            params_session_id="params",
            task_id="task",
            message_id="message",
            device_id="device",
        )
    )
    request = build_gui_rpc_request(query="open settings", invocation=context, timeout=30)
    assert request.source_request_id == "request-1"
    assert request.jiuwen_session_id == "session-1"
    assert request.xiaoyi_session_id == "root"
    assert request.xiaoyi_task_id == "task"
    assert request.xiaoyi_message_id == "message"
    assert request.device_id == "device"


@pytest.mark.asyncio
async def test_invocation_context_rail_nested_task_reset() -> None:
    outer = _context()
    inner = _context(
        xiaoyi=XiaoyiInvocationContext(
            root_session_id="root",
            task_id="task",
            message_id="message",
        )
    )
    rail = InvocationContextRail()
    invoke_ctx = SimpleNamespace(
        inputs=SimpleNamespace(
            run_context=SimpleNamespace(
                extra={INVOCATION_CONTEXT_EXTRA_KEY: invocation_context_to_dict(outer)}
            )
        ),
        extra={},
    )
    task_ctx = SimpleNamespace(
        inputs={
            "run_context": {
                "extra": {INVOCATION_CONTEXT_EXTRA_KEY: invocation_context_to_dict(inner)}
            }
        },
        extra={},
    )
    await rail.before_invoke(invoke_ctx)
    assert get_current_invocation_context() == outer
    await rail.before_task_iteration(task_ctx)
    assert get_current_invocation_context() == inner
    await rail.after_task_iteration(task_ctx)
    assert get_current_invocation_context() == outer
    await rail.after_invoke(invoke_ctx)
    assert get_current_invocation_context() is None


@pytest.mark.asyncio
async def test_invocation_context_rail_isolated_for_concurrent_sessions() -> None:
    """ContextVar bindings must not bleed between concurrent persistent tasks."""

    async def _turn(request_id: str, task_id: str) -> tuple[str, str | None]:
        context = InvocationContext(
            version=INVOCATION_CONTEXT_VERSION,
            invocation_id=f"inv-{request_id}",
            request_id=request_id,
            session_id=f"session-{request_id}",
            channel_id="xiaoyi",
            chat_id=f"chat-{request_id}",
            xiaoyi=XiaoyiInvocationContext(
                root_session_id=f"root-{request_id}",
                params_session_id=f"params-{request_id}",
                task_id=task_id,
                message_id=f"message-{request_id}",
                device_id=f"device-{request_id}",
            ),
        )
        rail = InvocationContextRail()
        callback = SimpleNamespace(
            inputs={
                "run_context": {
                    "extra": {
                        INVOCATION_CONTEXT_EXTRA_KEY: invocation_context_to_dict(context)
                    }
                }
            },
            extra={},
        )
        await rail.before_invoke(callback)
        # Yield while the sibling session binds its own context.  ContextVar
        # state remains local to each asyncio task.
        await asyncio.sleep(0)
        seen = get_current_invocation_context()
        await rail.after_invoke(callback)
        return request_id, seen.request_id if seen is not None else None

    results = await asyncio.gather(_turn("request-a", "task-a"), _turn("request-b", "task-b"))
    assert sorted(results) == [("request-a", "request-a"), ("request-b", "request-b")]
    assert get_current_invocation_context() is None


@pytest.mark.asyncio
async def test_invocation_context_rail_sequential_turns_leave_no_session_residue() -> None:
    rail = InvocationContextRail()

    async def _turn(request_id: str) -> None:
        context = InvocationContext(
            version=INVOCATION_CONTEXT_VERSION,
            invocation_id=f"inv-{request_id}",
            request_id=request_id,
            # Deliberately reuse one persistent session while changing the
            # invocation/request identity on each turn.
            session_id="session-shared",
            channel_id="web",
            chat_id=f"chat-{request_id}",
        )
        callback = SimpleNamespace(
            inputs=SimpleNamespace(
                run_context=SimpleNamespace(
                    extra={INVOCATION_CONTEXT_EXTRA_KEY: invocation_context_to_dict(context)}
                )
            ),
            extra={},
        )
        await rail.before_invoke(callback)
        assert get_current_invocation_context() == context
        await rail.after_invoke(callback)
        assert get_current_invocation_context() is None

    await _turn("request-a")
    await _turn("request-b")


@pytest.mark.asyncio
async def test_persistent_lifecycle_probe_rail_and_tool_binding(monkeypatch) -> None:
    """Probe rail/tool binding across a persistent-style attach/send sequence.

    This is intentionally a lightweight lifecycle probe; constructing a real
    openjiuwen ``start_interaction`` runner requires model/provider fixtures
    and is left to the integration suite.
    """

    context = _context(
        xiaoyi=XiaoyiInvocationContext(
            root_session_id="root",
            params_session_id="params",
            task_id="task",
            message_id="message",
            device_id="device",
        )
    )
    attached = attach_invocation_context({"query": "device"}, context)
    run_context = attached["run"]["context"]
    rail = InvocationContextRail()
    invoke_callback = SimpleNamespace(
        inputs=SimpleNamespace(run_context=SimpleNamespace(extra=run_context["extra"])),
        extra={},
    )
    task_callback = SimpleNamespace(
        inputs={"run_context": {"extra": run_context["extra"]}},
        extra={},
    )
    calls: list[dict] = []

    class _Manager:
        async def call(self, *, intent_name, command, context, timeout):
            calls.append({
                "intent_name": intent_name,
                "command": command,
                "context": context,
                "timeout": timeout,
            })
            return SimpleNamespace(ok=True, result={"ok": True})

    monkeypatch.setattr(
        device_utils,
        "get_xiaoyi_device_reverse_rpc_client",
        lambda: _Manager(),
    )

    # Exercise the same hook order used by a persistent runner: the outer
    # invocation is bound once, attach_output starts a task iteration, and
    # send_input executes the Device Tool while that task-local binding is
    # active.
    await rail.before_invoke(invoke_callback)
    assert get_current_invocation_context() == context
    await rail.before_task_iteration(task_callback)
    assert get_current_invocation_context() == context
    result = await device_utils.execute_device_command("CreateNote", {"title": "hello"})
    assert result == {"ok": True}
    assert calls and calls[0]["context"].source_request_id == context.request_id
    assert calls[0]["context"].xiaoyi_task_id == "task"
    await rail.after_task_iteration(task_callback)
    assert get_current_invocation_context() == context
    await rail.after_invoke(invoke_callback)
    assert get_current_invocation_context() is None


@pytest.mark.asyncio
async def test_real_deep_agent_persistent_lifecycle_binds_tool_task_context() -> None:
    """Carry invocation data through the real persistent supervisor/task loop."""

    context = _context(
        xiaoyi=XiaoyiInvocationContext(
            root_session_id="root",
            params_session_id="params",
            task_id="task",
            message_id="message",
            device_id="device",
        )
    )
    inputs = attach_invocation_context({"query": "probe context"}, context)
    card = AgentCard(id="invocation-context-probe", name="invocation-context-probe")
    rail = InvocationContextRail()
    observed: asyncio.Future[tuple[InvocationContext | None, int | None]] = (
        asyncio.get_running_loop().create_future()
    )

    class _ProbeReactAgent:
        async def register_callback(self, *args, **kwargs) -> None:
            return None

        async def invoke(self, effective, session, _streaming=False):
            if not observed.done():
                task = asyncio.current_task()
                observed.set_result(
                    (get_current_invocation_context(), id(task) if task else None)
                )
            return {"output": "context observed", "result_type": "answer"}

        async def write_invoke_result_to_stream(self, result, session) -> None:
            return None

    agent = DeepAgent(card).configure(
        DeepAgentConfig(
            card=card,
            enable_task_loop=True,
            completion_timeout=5.0,
            auto_create_workspace=False,
            rails=[rail],
        )
    )
    agent.set_react_agent(_ProbeReactAgent())
    await agent.ensure_initialized()
    session = create_agent_session(session_id="persistent-session", card=card)
    await session.pre_run(inputs={})

    request_task = asyncio.current_task()
    try:
        await agent.start(session=session)
        stream = await agent.attach_output()
        assert stream is not None
        await agent.send_input(
            SendInputRequest(request_id=context.request_id, inputs=inputs)
        )
        seen, tool_task_id = await asyncio.wait_for(observed, timeout=5.0)
        assert seen == context
        assert request_task is not None
        assert tool_task_id != id(request_task)
        await asyncio.wait_for(
            _drain_interaction_output(stream),
            timeout=5.0,
        )
    finally:
        await agent.stop()
        completion_rail = agent.find_rail_by_name("TaskCompletionRail")
        if completion_rail is not None:
            completion_rail.uninit(agent)
        await session.post_run()

    assert get_current_invocation_context() is None


async def _drain_interaction_output(stream) -> list[object]:
    return [item async for item in stream]


def test_extract_invocation_context_accepts_run_context_mapping() -> None:
    context = _context()
    extracted = _extract_invocation_context(
        {"extra": {INVOCATION_CONTEXT_EXTRA_KEY: invocation_context_to_dict(context)}}
    )
    assert extracted == context


@pytest.mark.asyncio
async def test_device_tool_fails_closed_without_invocation(monkeypatch) -> None:
    monkeypatch.setattr(device_utils, "get_current_invocation_context", lambda: None)

    with pytest.raises(RuntimeError, match="No active Jiuwen invocation context"):
        await device_utils.execute_device_command("CreateNote", {})


@pytest.mark.asyncio
async def test_gui_tool_fails_closed_without_invocation(monkeypatch) -> None:
    monkeypatch.setattr(gui_tool, "get_current_invocation_context", lambda: None)

    with pytest.raises(RuntimeError, match="INVALID_CONTEXT"):
        await gui_tool.xiaoyi_gui_agent.invoke({"query": "open settings"})
