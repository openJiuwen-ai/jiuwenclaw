# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for bounded stream buffering."""

from __future__ import annotations

import asyncio

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module


@pytest.mark.asyncio
async def test_process_message_stream_uses_bounded_handoff_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_queue = asyncio.Queue
    created_queues: list[asyncio.Queue] = []

    def queue_factory(*args, **kwargs):
        queue = real_queue(*args, **kwargs)
        created_queues.append(queue)
        return queue

    class FakeAdapter:
        @staticmethod
        async def process_message_stream_impl(*_args, **_kwargs):
            yield AgentResponseChunk(
                request_id="req-bounded-stream",
                channel_id="tui",
                payload={"event_type": "chat.final", "content": "done"},
                is_complete=False,
            )

    monkeypatch.setattr(interface_module.asyncio, "Queue", queue_factory)
    monkeypatch.setattr(
        interface_module.JiuWenSwarm,
        "_ensure_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh", "memory": {"mode": "disabled"}},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "_schedule_symphony_session_feedback", lambda *_args: None)

    swarm = interface_module.JiuWenSwarm()
    request = AgentRequest(
        request_id="req-bounded-stream",
        channel_id="tui",
        session_id="sess-bounded-stream",
        params={"query": "hello", "mode": "agent"},
        is_stream=True,
    )

    chunks = [chunk async for chunk in swarm.process_message_stream(request)]

    assert chunks[-1].is_complete is True
    assert created_queues
    assert created_queues[0].maxsize == swarm.STREAM_QUEUE_MAXSIZE
    assert created_queues[0].maxsize > 0


@pytest.mark.asyncio
async def test_empty_final_keeps_accumulated_delta_for_post_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalized_inputs: list[str] = []

    class FakeAdapter:
        @staticmethod
        async def process_message_stream_impl(*_args, **_kwargs):
            yield AgentResponseChunk(
                request_id="req-empty-final",
                channel_id="tui",
                payload={"event_type": "chat.delta", "content": "complete answer"},
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id="req-empty-final",
                channel_id="tui",
                payload={"event_type": "chat.final", "content": ""},
                is_complete=False,
            )

    async def fake_finalize(content: str, **_kwargs) -> str:
        finalized_inputs.append(content)
        return content

    monkeypatch.setattr(
        interface_module.JiuWenSwarm,
        "_ensure_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh", "memory": {"mode": "disabled"}},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "finalize_assistant_response_if_a2ui", fake_finalize)
    monkeypatch.setattr(interface_module, "_schedule_symphony_session_feedback", lambda *_args: None)

    swarm = interface_module.JiuWenSwarm()
    request = AgentRequest(
        request_id="req-empty-final",
        channel_id="tui",
        session_id="sess-empty-final",
        params={"query": "hello", "mode": "agent"},
        is_stream=True,
    )

    chunks = [chunk async for chunk in swarm.process_message_stream(request)]

    assert chunks[-1].is_complete is True
    assert finalized_inputs == ["complete answer"]


@pytest.mark.asyncio
async def test_later_empty_final_keeps_last_nonempty_final_for_post_processing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finalized_inputs: list[str] = []

    class FakeAdapter:
        @staticmethod
        async def process_message_stream_impl(*_args, **_kwargs):
            yield AgentResponseChunk(
                request_id="req-repeated-final",
                channel_id="tui",
                payload={"event_type": "chat.delta", "content": "draft answer"},
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id="req-repeated-final",
                channel_id="tui",
                payload={"event_type": "chat.final", "content": "authoritative answer"},
                is_complete=False,
            )
            yield AgentResponseChunk(
                request_id="req-repeated-final",
                channel_id="tui",
                payload={"event_type": "chat.final", "content": ""},
                is_complete=False,
            )

    async def fake_finalize(content: str, **_kwargs) -> str:
        finalized_inputs.append(content)
        return content

    monkeypatch.setattr(
        interface_module.JiuWenSwarm,
        "_ensure_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh", "memory": {"mode": "disabled"}},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)
    monkeypatch.setattr(interface_module, "finalize_assistant_response_if_a2ui", fake_finalize)
    monkeypatch.setattr(interface_module, "_schedule_symphony_session_feedback", lambda *_args: None)

    swarm = interface_module.JiuWenSwarm()
    request = AgentRequest(
        request_id="req-repeated-final",
        channel_id="tui",
        session_id="sess-repeated-final",
        params={"query": "hello", "mode": "agent"},
        is_stream=True,
    )

    chunks = [chunk async for chunk in swarm.process_message_stream(request)]

    assert chunks[-1].is_complete is True
    assert finalized_inputs == ["authoritative answer"]


@pytest.mark.asyncio
async def test_closing_consumer_cancels_producer_blocked_on_full_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_finished = asyncio.Event()

    class FakeAdapter:
        @staticmethod
        async def process_message_stream_impl(*_args, **_kwargs):
            seq = 0
            try:
                while True:
                    yield AgentResponseChunk(
                        request_id="req-disconnect",
                        channel_id="tui",
                        payload={"event_type": "chat.delta", "content": str(seq)},
                        is_complete=False,
                    )
                    seq += 1
            finally:
                producer_finished.set()

    monkeypatch.setattr(
        interface_module.JiuWenSwarm,
        "_ensure_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh", "memory": {"mode": "disabled"}},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)

    swarm = interface_module.JiuWenSwarm()
    swarm.STREAM_QUEUE_MAXSIZE = 1
    request = AgentRequest(
        request_id="req-disconnect",
        channel_id="tui",
        session_id="sess-disconnect",
        params={"query": "hello", "mode": "agent"},
        is_stream=True,
    )
    stream = swarm.process_message_stream(request)

    first = await anext(stream)
    assert first.payload["event_type"] == "chat.delta"
    await asyncio.wait_for(stream.aclose(), timeout=1.0)

    assert producer_finished.is_set()


@pytest.mark.asyncio
async def test_producer_close_cancellation_does_not_leave_consumer_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloseCancellingStream:
        def __init__(self) -> None:
            self._yielded = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._yielded:
                raise StopAsyncIteration
            self._yielded = True
            return AgentResponseChunk(
                request_id="req-close-cancel",
                channel_id="tui",
                payload={"event_type": "chat.delta", "content": "partial"},
                is_complete=False,
            )

        async def aclose(self) -> None:
            raise asyncio.CancelledError

    class FakeAdapter:
        @staticmethod
        def process_message_stream_impl(*_args, **_kwargs):
            return CloseCancellingStream()

    monkeypatch.setattr(
        interface_module.JiuWenSwarm,
        "_ensure_adapter",
        lambda *_args, **_kwargs: FakeAdapter(),
    )
    monkeypatch.setattr(
        interface_module,
        "get_config",
        lambda: {"preferred_language": "zh", "memory": {"mode": "disabled"}},
    )
    monkeypatch.setattr(interface_module, "get_memory_mode", lambda _config: "disabled")
    monkeypatch.setattr(interface_module, "append_history_record", lambda **_kwargs: None)

    swarm = interface_module.JiuWenSwarm()
    request = AgentRequest(
        request_id="req-close-cancel",
        channel_id="tui",
        session_id="sess-close-cancel",
        params={"query": "hello", "mode": "agent"},
        is_stream=True,
    )

    async def consume() -> None:
        async for _ in swarm.process_message_stream(request):
            pass

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(consume(), timeout=1.0)
