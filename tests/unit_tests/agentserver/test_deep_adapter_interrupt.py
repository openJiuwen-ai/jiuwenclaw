# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for JiuWenSwarmDeepAdapter interrupt when stream consumer already unwound."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenswarm.server.runtime.agent_adapter.stream_lifecycle import (
    close_owned_async_iterator,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import CodexProviderError


def _build_cancel_request(session_id: str = "tui_sess_1") -> AgentRequest:
    return AgentRequest(
        request_id="req-cancel",
        channel_id="tui",
        session_id=session_id,
        req_method=ReqMethod.CHAT_CANCEL,
        params={"intent": "cancel", "mode": "agent.plan"},
    )


def _make_adapter(**state: object) -> JiuWenSwarmDeepAdapter:
    """Create a bare adapter with internal state set via setattr."""
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._is_session_scoped_adapter = True  # pylint: disable=protected-access
    adapter._parent_session_id = None  # pylint: disable=protected-access
    for name, value in state.items():
        setattr(adapter, name, value)
    return adapter


@pytest.mark.asyncio
async def test_cancel_runs_teardown_when_session_not_in_active_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When session is not active, per-session teardown runs but global abort is skipped.

    Global abort (instance.abort) is unsafe when the session is inactive — a
    just-starting session on the same adapter could be killed as collateral.
    Per-session teardown (rail abort, shell kill) is sufficient for the target.
    """
    rail = MagicMock()
    rail.get_cancelled_tool_results.return_value = []
    instance = MagicMock()
    instance.abort = AsyncMock()
    # Force the non-interaction interrupt path under test (rail teardown /
    # skip global abort).  A bare MagicMock makes ``_interaction_started``
    # truthy and would divert into cancel_round().
    instance._interaction_started = False
    adapter = _make_adapter(
        _active_session_ids={},
        _session_agent_tasks={},
        _stream_event_rail=rail,
        _instance=instance,
    )

    kill_mock = MagicMock(return_value=2)
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.kill_shell_processes_for_session_tree",
        kill_mock,
    )
    monkeypatch.setattr(adapter, "_cancel_pending_todos", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_cancel_scheduler_running_tasks", MagicMock())

    response = await adapter.process_interrupt(_build_cancel_request())

    # Per-session teardown must still run
    rail.abort.assert_called_once_with("tui_sess_1")
    rail.collect_cancelled_tool_updates.assert_called_once_with("tui_sess_1")
    rail.reset_for_new_task.assert_called_once_with("tui_sess_1")
    kill_mock.assert_called_once_with("tui_sess_1")
    # Global abort must NOT fire — session is inactive, could kill a just-starting session
    instance.abort.assert_not_awaited()
    assert response.payload["event_type"] == "chat.interrupt_result"
    assert response.payload["intent"] == "cancel"
    assert response.payload["success"] is True


@pytest.mark.asyncio
async def test_unmark_skips_rail_cleanup_when_stream_consumer_cancelled() -> None:
    rail = MagicMock()
    adapter = _make_adapter(
        _active_session_ids={"sess_a": 1},
        _stream_event_rail=rail,
    )

    getattr(adapter, "_unmark_session_active")("sess_a", cleanup_rail=False)

    rail.cleanup_session.assert_not_called()
    assert "sess_a" not in getattr(adapter, "_active_session_ids")


@pytest.mark.asyncio
async def test_unmark_cleans_rail_on_normal_completion() -> None:
    rail = MagicMock()
    adapter = _make_adapter(
        _active_session_ids={"sess_a": 1},
        _stream_event_rail=rail,
    )

    getattr(adapter, "_unmark_session_active")("sess_a")

    rail.cleanup_session.assert_called_once_with("sess_a")
    assert "sess_a" not in getattr(adapter, "_active_session_ids")


@pytest.mark.asyncio
async def test_abort_skipped_when_other_sessions_active_even_if_target_executing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """instance.abort() is global on the shared DeepAgent — when other sessions are
    active, it must NEVER be called, even if the target session is executing.
    Per-session teardown (rail abort, task cancel, shell kill) is sufficient.
    """
    rail = MagicMock()
    rail.get_cancelled_tool_results.return_value = []
    instance = MagicMock()
    setattr(instance, "abort", AsyncMock())
    setattr(instance, "_interaction_started", False)
    setattr(instance, "_invoke_active", True)
    stream_task = MagicMock()
    stream_task.done.return_value = False
    setattr(instance, "_stream_process_task", stream_task)
    loop_session = MagicMock()
    loop_session.get_session_id.return_value = "tui_target"
    setattr(instance, "_loop_session", loop_session)
    adapter = _make_adapter(
        _active_session_ids={"tui_other": 1},
        _session_agent_tasks={},
        _stream_event_rail=rail,
        _instance=instance,
    )

    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.kill_shell_processes_for_session_tree",
        MagicMock(return_value=0),
    )
    monkeypatch.setattr(adapter, "_cancel_pending_todos", AsyncMock(return_value=[]))
    monkeypatch.setattr(adapter, "_cancel_scheduler_running_tasks", MagicMock())

    await adapter.process_interrupt(_build_cancel_request(session_id="tui_target"))

    # instance.abort must NOT be called — it would kill tui_other's work too
    instance.abort.assert_not_awaited()
    # But per-session teardown must still run
    rail.abort.assert_called_once_with("tui_target")


def test_reset_runtime_cron_context_resets_shell_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.core.sys_operation.shell_process_registry import (
        set_shell_session_id,
    )

    reset_shell_mock = MagicMock()
    monkeypatch.setattr(
        "openjiuwen.core.sys_operation.shell_process_registry.reset_shell_session_id",
        reset_shell_mock,
    )
    for var_name in (
        "_CRON_TOOL_MODE",
        "_CRON_TOOL_METADATA",
        "_CRON_TOOL_SESSION_ID",
        "_CRON_TOOL_CHANNEL_ID",
    ):
        monkeypatch.setattr(
            f"jiuwenswarm.server.runtime.agent_adapter.interface_deep.{var_name}",
            MagicMock(),
        )

    shell_token = set_shell_session_id("sess_reset")
    getattr(JiuWenSwarmDeepAdapter, "_reset_runtime_cron_context")(
        (
            MagicMock(),
            MagicMock(),
            MagicMock(),
            MagicMock(),
            shell_token,
        )
    )
    reset_shell_mock.assert_called_once_with(shell_token)


@pytest.mark.asyncio
async def test_cancel_session_gathers_existing_cancellation_without_cancelling_twice() -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await allow_cleanup.wait()
            raise

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    task.cancel()
    await cleanup_started.wait()
    assert task.cancelling() == 1
    adapter = _make_adapter(_session_agent_tasks={"sess": {task}})

    cancel_call = asyncio.create_task(adapter._cancel_session_agent_tasks("sess"))
    await asyncio.sleep(0)
    assert not cancel_call.done()
    assert task.cancelling() == 1
    allow_cleanup.set()

    assert await cancel_call == 0
    assert task.cancelled()
    assert adapter._session_agent_tasks == {}


@pytest.mark.asyncio
async def test_cancel_session_timeout_keeps_cleanup_owner_without_second_cancel() -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await allow_cleanup.wait()
            raise

    task = asyncio.create_task(worker())
    await asyncio.sleep(0)
    adapter = _make_adapter(_session_agent_tasks={"sess": {task}})

    with pytest.raises(CodexProviderError) as captured:
        await adapter._cancel_session_agent_tasks(
            "sess",
            deadline=asyncio.get_running_loop().time() + 0.01,
        )
    assert captured.value.code == "cancel_cleanup_timeout"
    assert task.cancelling() == 1
    assert adapter._session_agent_tasks["sess"] == {task}
    await cleanup_started.wait()

    allow_cleanup.set()
    await asyncio.gather(task, return_exceptions=True)
    assert await adapter._cancel_session_agent_tasks("sess") == 0
    assert adapter._session_agent_tasks == {}


@pytest.mark.asyncio
async def test_codex_turn_owner_repeated_cancel_shares_completion_until_cleanup() -> None:
    adapter = _make_adapter(
        _codex_turn_owners={},
        _codex_turn_generations={},
    )
    owner = interface_deep_module._CodexTurnOwner(
        interface_deep_module._CodexTurnIdentity(
            session_id="sess",
            request_id="original-request",
            generation=1,
        ),
        adapter._remove_codex_turn_owner,
    )
    adapter._register_codex_turn_owner(owner)
    call_started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()

    async def provider_call() -> None:
        task = owner.bind_model_call()
        call_started.set()
        error = None
        try:
            await asyncio.Event().wait()
        except BaseException as exc:
            error = exc
            cleanup_started.set()
            await allow_cleanup.wait()
            raise
        finally:
            owner.release_model_call(task, error)

    provider_task = asyncio.create_task(provider_call())
    await call_started.wait()

    first = owner.request_cancel()
    second = owner.request_cancel()
    assert first is second
    await cleanup_started.wait()
    assert not first.done()
    assert adapter._codex_turn_owners["sess"] is owner

    allow_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await provider_task
    owner.finish(asyncio.CancelledError())

    assert (await first).clean is True
    assert adapter._codex_turn_owners == {}


@pytest.mark.asyncio
async def test_codex_turn_owner_waits_for_model_release_after_outer_cleanup() -> None:
    adapter = _make_adapter(_codex_turn_owners={})
    owner = interface_deep_module._CodexTurnOwner(
        interface_deep_module._CodexTurnIdentity(
            session_id="sess",
            request_id="original-request",
            generation=1,
        ),
        adapter._remove_codex_turn_owner,
    )
    adapter._register_codex_turn_owner(owner)
    model_started = asyncio.Event()
    release_model = asyncio.Event()

    async def model_call() -> None:
        task = owner.bind_model_call()
        model_started.set()
        try:
            await release_model.wait()
        finally:
            owner.release_model_call(task, None)

    model_task = asyncio.create_task(model_call())
    await model_started.wait()
    completion = owner.request_cancel()
    owner.finish(asyncio.CancelledError())

    assert not completion.done()
    assert adapter._codex_turn_owners["sess"] is owner

    release_model.set()
    await asyncio.gather(model_task, return_exceptions=True)
    assert (await completion).clean is True
    assert adapter._codex_turn_owners == {}


@pytest.mark.asyncio
async def test_codex_turn_owner_blocks_same_session_replacement_until_completion() -> None:
    adapter = _make_adapter(_codex_turn_owners={})

    def make_owner(generation: int):
        return interface_deep_module._CodexTurnOwner(
            interface_deep_module._CodexTurnIdentity(
                session_id="sess",
                request_id=f"request-{generation}",
                generation=generation,
            ),
            adapter._remove_codex_turn_owner,
        )

    first = make_owner(1)
    second = make_owner(2)
    adapter._register_codex_turn_owner(first)

    with pytest.raises(CodexProviderError) as captured:
        adapter._register_codex_turn_owner(second)
    assert captured.value.code == "provider_busy"

    first.finish()
    adapter._register_codex_turn_owner(second)
    assert adapter._codex_turn_owners["sess"] is second
    second.finish()


@pytest.mark.asyncio
async def test_owned_iterator_close_survives_repeated_external_cancellation() -> None:
    close_started = asyncio.Event()
    allow_close = asyncio.Event()

    class Iterator:
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1
            close_started.set()
            await allow_close.wait()

    iterator = Iterator()
    close_task = asyncio.create_task(close_owned_async_iterator(iterator))
    await close_started.wait()
    close_task.cancel()
    await asyncio.sleep(0)
    assert not close_task.done()
    allow_close.set()

    with pytest.raises(asyncio.CancelledError):
        await close_task
    assert iterator.close_calls == 2


@pytest.mark.asyncio
async def test_stream_aclose_cancels_and_joins_owned_background_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class BlockingAdapter:
        async def process_message_stream_impl(self, request, inputs):
            del inputs
            try:
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"event_type": "chat.delta", "content": "first"},
                    is_complete=False,
                )
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_started.set()
                await allow_cleanup.wait()
                raise
            finally:
                cleanup_finished.set()

    swarm = JiuWenSwarm()
    adapter = BlockingAdapter()
    monkeypatch.setattr(swarm, "_ensure_adapter", lambda **_kwargs: adapter)
    monkeypatch.setattr(
        swarm,
        "_build_inputs",
        lambda _request: ({"query": "test"}, "off", "test"),
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface.append_history_record",
        lambda **_kwargs: None,
    )
    request = AgentRequest(
        request_id="req-close",
        channel_id="tui",
        session_id="sess-close",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "query": "test",
            "mode": "auto_harness",
            "activate_response": {},
        },
    )
    stream = swarm.process_message_stream(request)
    first = await anext(stream)
    assert first.payload["content"] == "first"

    close_task = asyncio.create_task(stream.aclose())
    await cleanup_started.wait()
    assert not close_task.done()
    allow_cleanup.set()
    await close_task

    assert cleanup_finished.is_set()
