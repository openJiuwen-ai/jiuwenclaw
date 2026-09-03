# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Fix A 回归：run_stream_task 的协作式取消检查点与 adapter 流显式收尾。

背景（09-02 会话 officeclaw_9d26b023 线上案例）：
    外层消费者被取消（用户点停止）后，producer 的
    ``async for chunk in adapter.process_message_stream_impl(...)`` 在积压
    排空期间不真正挂起（``__anext__`` 同步恢复、无界队列 put 不让出事件
    循环），pending 的 ``task.cancel()`` 永远无法注入 → producer 变僵尸，
    其 finally 里的租约释放 / round abort 永不执行 → 后续消息全部静默 ACK。

Fix A：
    - 循环体每个 chunk 前 ``await asyncio.sleep(0)``：保证取消在一个 chunk
      内送达；
    - finally ``await asyncio.wait_for(stream_iter.aclose(), 3)``：显式收尾
      adapter 生成器（close interaction_stream → abort_active_round=True），
      不依赖 GC 的 asyncgen finalizer。

测试通过鸭子类型流（非 async generator）直接观察 aclose 调用。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponseChunk
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter import interface as interface_module


_TOTAL_CHUNKS = 100


class _NoAwaitStream:
    """模拟积压排空：__anext__ 无真实挂起地吐 chunk；记录 aclose 调用。

    - ``raise_after``: 吐完 N 个 chunk 后 __anext__ 抛 ``raise_exc``，驱动
      生产者走 except → finally 路径（用于验证 aclose 早于 put(error)）。
    - ``aclose_delay``: aclose 内部 await sleep，模拟"收尾需要时间"。
    """

    def __init__(self, request: AgentRequest, *, total: int = _TOTAL_CHUNKS,
                 close_error: Exception | None = None,
                 aclose_delay: float | None = None,
                 raise_after: int | None = None,
                 raise_exc: Exception | None = None) -> None:
        self._request = request
        self._total = total
        self._index = 0
        self._close_error = close_error
        self._aclose_delay = aclose_delay
        self._raise_after = raise_after
        self._raise_exc = raise_exc
        self.aclose_called = False
        self.aclose_started_at: float | None = None
        self.aclose_finished_at: float | None = None

    @property
    def produced(self) -> int:
        return self._index

    def __aiter__(self) -> "_NoAwaitStream":
        return self

    async def __anext__(self) -> AgentResponseChunk:
        if self._raise_after is not None and self._index >= self._raise_after:
            raise self._raise_exc or RuntimeError("injected stream error")
        if self._index >= self._total:
            raise StopAsyncIteration
        self._index += 1
        return AgentResponseChunk(
            request_id=self._request.request_id,
            channel_id=self._request.channel_id,
            payload={"event_type": "chat.delta", "content": "x"},
            is_complete=False,
        )

    async def aclose(self) -> None:
        self.aclose_called = True
        self.aclose_started_at = time.monotonic()
        if self._aclose_delay is not None:
            await asyncio.sleep(self._aclose_delay)
        self.aclose_finished_at = time.monotonic()
        if self._close_error is not None:
            raise self._close_error


class _StreamAdapter:
    """adapter 替身：process_message_stream_impl 是 async generator。

    生产代码的 ``process_message_stream_impl`` 是 async generator（调用即得
    async gen 对象，无需 await），其内部 ``async for chunk in interaction_stream:
    yield chunk``，并在自己的 finally 里 ``interaction_stream.aclose()``。

    这里镜像该结构：把 ``_NoAwaitStream`` 当作 interaction_stream，从它 yield
    chunk；finally 里调 ``_NoAwaitStream.aclose()``。这样生产代码的
    ``stream_iter.aclose()`` 会触发本 async gen 的 finally →
    ``_NoAwaitStream.aclose_called`` 置位，测试可据此验证收尾顺序。
    """

    def __init__(self, *, total: int = _TOTAL_CHUNKS,
                 close_error: Exception | None = None,
                 aclose_delay: float | None = None,
                 raise_after: int | None = None,
                 raise_exc: Exception | None = None) -> None:
        self.total = total
        self.close_error = close_error
        self.aclose_delay = aclose_delay
        self.raise_after = raise_after
        self.raise_exc = raise_exc
        self.stream: _NoAwaitStream | None = None

    async def process_message_stream_impl(
        self, request: AgentRequest, _inputs: dict[str, Any]
    ):
        inner = _NoAwaitStream(
            request, total=self.total, close_error=self.close_error,
            aclose_delay=self.aclose_delay,
            raise_after=self.raise_after, raise_exc=self.raise_exc,
        )
        self.stream = inner
        try:
            async for chunk in inner:
                yield chunk
        finally:
            await inner.aclose()


def _request(*, request_id: str = "req-1") -> AgentRequest:
    return AgentRequest(
        request_id=request_id,
        channel_id="web",
        session_id="session-1",
        req_method=ReqMethod.CHAT_SEND,
        params={"query": "继续执行", "mode": "agent"},
        is_stream=True,
    )


def _build_swarm(monkeypatch: pytest.MonkeyPatch, adapter: _StreamAdapter):
    swarm = interface_module.JiuWenSwarm()
    swarm._adapter = adapter
    swarm._sdk_name = "harness"
    monkeypatch.setattr(swarm, "_build_inputs", lambda _request: ({}, "local", ""))
    monkeypatch.setattr(
        interface_module, "append_history_record", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        interface_module, "_schedule_symphony_session_feedback", lambda *_args: None
    )
    return swarm


@pytest.mark.asyncio
async def test_consumer_cancel_reaches_producer_within_one_chunk(monkeypatch) -> None:
    """取消必须在 ≤1 个 chunk 内送达 producer，而不是等积压排空。

    修复前：producer 一次性同步排空全部 100 个 chunk（cancel 无法注入），
    aclose 永远不会在取消路径上被调用。修复后：sleep(0) 检查点让取消立即
    送达，producer 提前收尾并显式 aclose adapter 流。
    """
    adapter = _StreamAdapter()
    swarm = _build_swarm(monkeypatch, adapter)
    consumed: list[AgentResponseChunk] = []

    async def consume() -> None:
        async for chunk in swarm.process_message_stream(_request()):
            consumed.append(chunk)
            if len(consumed) == 1:
                # 模拟外层 WebSocket 消费者被取消（用户点停止）
                asyncio.current_task().cancel()

    task = asyncio.create_task(consume())
    # 等消费者任务结束（被取消或正常返回）。return_exceptions 避免 gather
    # 把 CancelledError 抛到测试用例本身。
    result = await asyncio.gather(task, return_exceptions=True)
    # 充分让出事件循环，确保 producer 的 finally（aclose + finished 日志 +
    # stream_done.set）跑完。用轮询等待 adapter.stream 收尾稳定，避免
    # “Exception ignored in coroutine”类 unraisable 告警（async gen finalizer
    # 在测试结束前未完成会被 pytest 当作失败）。
    for _ in range(50):
        stream = adapter.stream
        if stream is not None and stream.aclose_called:
            break
        await asyncio.sleep(0)

    assert isinstance(result[0], asyncio.CancelledError)
    stream = adapter.stream
    assert stream is not None, (
        "producer 未启动（adapter.stream 为 None）——取消过早注入"
    )
    # 取消在一个 chunk 内送达：绝不能排空全部积压
    assert stream.produced < adapter.total, (
        f"producer 排空了全部 {stream.produced}/{adapter.total} 个 chunk，"
        "取消检查点未生效（僵尸 producer 回归）"
    )
    # adapter 流被显式 aclose（其 finally 负责 close interaction_stream）
    assert stream.aclose_called is True
    await swarm._session_manager.close_all_sessions()


@pytest.mark.asyncio
async def test_adapter_aclose_failure_does_not_break_stream_finish(monkeypatch) -> None:
    """aclose 抛异常只记 warning，不吞掉正常完成（stream_done 必须置位）。"""
    adapter = _StreamAdapter(total=3, close_error=RuntimeError("close boom"))
    swarm = _build_swarm(monkeypatch, adapter)

    chunks = [
        chunk async for chunk in swarm.process_message_stream(_request())
    ]

    assert adapter.stream.aclose_called is True
    payloads = [chunk.payload for chunk in chunks]
    assert sum(
        isinstance(p, dict) and p.get("event_type") == "chat.delta"
        for p in payloads
    ) == 3
    assert chunks[-1].is_complete is True
    await swarm._session_manager.close_all_sessions()


@pytest.mark.asyncio
async def test_normal_completion_yields_all_chunks_and_final_frame(monkeypatch) -> None:
    """回归：正常完成路径不受检查点影响，aclose 对已耗尽流是 no-op。"""
    adapter = _StreamAdapter(total=3)
    swarm = _build_swarm(monkeypatch, adapter)

    chunks = [
        chunk async for chunk in swarm.process_message_stream(_request())
    ]

    assert adapter.stream.produced == 3
    assert [c.is_complete for c in chunks] == [False, False, False, True]
    # 完成帧由 consumer loop 注入，payload 为 {"is_complete": True}
    assert chunks[-1].payload == {"is_complete": True}
    assert adapter.stream.aclose_called is True
    await swarm._session_manager.close_all_sessions()


@pytest.mark.asyncio
async def test_aclose_releases_lease_before_consumer_is_notified(monkeypatch) -> None:
    """aclose 必须先于 stream_queue.put(("error", ...)) 完成。

    修复前的顺序：except → put("error") → finally → aclose。消费者看到 error
    后会立刻发新请求，新请求的 attach_output 撞上尚未释放的租约 → 静默 ACK。
    修复后：except 暂存 error → finally 先 aclose（释放租约）→ 再 put("error")
    唤醒消费者。

    用 ``raise_after=1`` 让 inner stream 抛错驱动生产者走 except → finally；
    aclose 内部 sleep 50ms 模拟"收尾需要时间"。消费者收到的最后一个帧是 error
    帧（payload 为错误），记录其到达时间，断言 aclose_finished_at ≤ 该时间。
    """
    adapter = _StreamAdapter(
        total=_TOTAL_CHUNKS, aclose_delay=0.05,
        raise_after=1, raise_exc=RuntimeError("injected stream error"),
    )
    swarm = _build_swarm(monkeypatch, adapter)

    error_observed_at: list[float] = []

    async for chunk in swarm.process_message_stream(_request()):
        # error 帧由 consumer loop 注入：item_type=error，payload 含 exc 信息
        if chunk.is_complete:
            error_observed_at.append(time.monotonic())

    stream = adapter.stream
    assert stream is not None
    assert stream.aclose_called is True
    assert stream.aclose_finished_at is not None
    assert error_observed_at, "消费者未收到完成/error 帧"
    # 关键断言：租约释放（aclose 完成）必须早于消费者被唤醒
    assert stream.aclose_finished_at <= error_observed_at[-1] + 1e-3, (
        "aclose 未在 put(error) 之前完成 —— 竞态窗口未关闭"
    )
    await swarm._session_manager.close_all_sessions()


@pytest.mark.asyncio
async def test_aclose_invoked_on_normal_completion_records_state(monkeypatch) -> None:
    """正常完成时 aclose 被调用（覆盖 finally 收尾不被跳过的回归）。

    aclose 超时分支（[metric] adapter stream aclose timeout）属于防御性日志，
    其触发需要 async gen finalizer 配合，单测易触发 finalizer 死锁，故不在此
    覆盖；线上由 metrics 告警监控。
    """
    adapter = _StreamAdapter(total=3, aclose_delay=0.0)
    swarm = _build_swarm(monkeypatch, adapter)

    chunks = [
        chunk async for chunk in swarm.process_message_stream(_request())
    ]

    assert len(chunks) == 4
    assert chunks[-1].is_complete is True
    assert adapter.stream is not None
    assert adapter.stream.aclose_called is True
    assert adapter.stream.aclose_finished_at is not None
    await swarm._session_manager.close_all_sessions()
    await swarm._session_manager.close_all_sessions()
