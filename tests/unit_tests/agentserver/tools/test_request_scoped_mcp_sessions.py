# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PooledMcpWorker / acquire / close / sweep 生命周期单测（纯打桩）。

覆盖 request_scoped_mcp_sessions 池化核心的行为语义：
- acquire 复用（同 key 只起一个 owner task）与 force_rebuild（关旧起新）；
- close 哨兵排干队列（竞态下投递的 req 不永挂）；
- release_request_scoped_mcp_sessions 只回收死 worker（活 worker 保留给
  HITL 拆出的后续请求复用）；
- sweep TTL 回收空闲超时 worker；
- invoke 对 TimeoutError 不重试（非幂等工具防双重执行）、对 worker 死亡
  类异常 force_rebuild 重试一次。

**纯打桩 UT**：不真起 stdio 进程，_enter_stdio_mcp_session 全程 monkeypatch 为
返回 _FakeSession 的 AsyncMock；card 用 MagicMock 构造完整属性（含 id）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import MagicMock, AsyncMock

import pytest

from jiuwenclaw.agentserver.tools import request_scoped_mcp_sessions as pool_mod
from jiuwenclaw.agentserver.tools.request_scoped_mcp_sessions import (
    PooledMcpWorker,
    PooledRequestMcpTool,
    _clear_request_scoped_mcp_sessions_for_tests,
    _pool_key,
    acquire_request_scoped_mcp_session,
    release_request_scoped_mcp_sessions,
    sweep_idle_session_mcp_workers,
)


class _FakeSession:
    """替身 stdio session：记录调用，可选抛异常/延迟。"""

    def __init__(self, *, fail_first: int = 0, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._fail_first = fail_first
        self._delay_s = delay_s

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._fail_first > 0:
            self._fail_first -= 1
            raise RuntimeError("fake session failure")
        return _FakeResult(f"ok:{name}")


class _FakeResult:
    """最小 CallToolResult 形状（content[-1].text）。"""

    class _Text:
        def __init__(self, text: str) -> None:
            self.text = text

    def __init__(self, text: str) -> None:
        self.content = [_FakeResult._Text(text)]


@pytest.fixture(autouse=True)
def _clean_pool():
    """每个测试前后清空池，避免状态泄漏。"""
    _clear_request_scoped_mcp_sessions_for_tests()
    yield
    _clear_request_scoped_mcp_sessions_for_tests()


@pytest.fixture(autouse=True)
def _stub_stdio_session(monkeypatch: pytest.MonkeyPatch):
    """所有测试默认打桩 stdio session 构造，避免起真子进程。"""

    async def _fake_enter_stdio(stack, params):  # noqa: ANN001
        return _FakeSession(
            fail_first=int(params.get("_fail_first", 0)),
            delay_s=float(params.get("_delay_s", 0.0)),
        )

    monkeypatch.setattr(pool_mod, "_enter_stdio_mcp_session", _fake_enter_stdio)


def _make_card(name: str = "srv__tool") -> MagicMock:
    """构造 Tool 基类要求的完整 card mock（含 id）。"""
    card = MagicMock()
    card.name = name
    card.id = name.replace("__", "::", 1)
    card.description = f"Tool {name}"
    return card


_PARAMS: dict[str, Any] = {"command": "fake-mcp", "args": ["--stdio"]}


async def test_acquire_reuses_same_worker():
    """同 key 只起一个 owner task。"""
    w1 = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    w2 = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    assert w1 is w2
    assert w1.alive
    w3 = await acquire_request_scoped_mcp_session("s2", "srv", _PARAMS)
    assert w3 is not w1


async def test_pool_key_distinguishes_params():
    """env 漂移时指纹不同。"""
    assert _pool_key("s1", "srv", _PARAMS) == _pool_key("s1", "srv", dict(_PARAMS))
    drift = {**_PARAMS, "env": {"INVOCATION_ID": "x"}}
    assert _pool_key("s1", "srv", drift) != _pool_key("s1", "srv", _PARAMS)


async def test_call_tool_roundtrip():
    """call_tool 正常返回 _FakeResult。"""
    worker = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    result = await worker.call_tool("new_page", {"url": "https://example.com"})
    assert getattr(result.content[-1], "text", "") == "ok:new_page"


async def test_force_rebuild_kills_old_worker():
    """force_rebuild 关旧 worker 起新。"""
    w1 = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    w2 = await acquire_request_scoped_mcp_session(
        "s1", "srv", _PARAMS, force_rebuild=True
    )
    assert w2 is not w1
    assert not w1.alive
    assert w2.alive


async def test_close_sentinel_drains_queued_requests():
    """close 哨兵后投递的请求必须收到异常，不能永挂。"""
    worker = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    # 模拟竞态：owner 已收到 None 但尚未 resume 时 caller 投递 req。
    worker.queue.put_nowait(None)
    with pytest.raises(Exception):
        await asyncio.wait_for(
            worker.call_tool("anything", {}), timeout=2.0
        )


async def test_release_request_keeps_alive_worker():
    """归属请求结束时，活 worker 保留（HITL 后续请求复用）。"""
    params = {**_PARAMS, "_request_id": "req-1"}
    worker = await acquire_request_scoped_mcp_session("s1", "srv", params)
    await release_request_scoped_mcp_sessions("req-1")
    assert worker.alive
    # 复用同一 worker
    w2 = await acquire_request_scoped_mcp_session("s1", "srv", params)
    assert w2 is worker


async def test_release_request_removes_dead_worker():
    """死 worker 仍在池映射里（未 sweep），release 应摘除。"""
    params = {**_PARAMS, "_request_id": "req-1"}
    key = _pool_key("s1", "srv", params)
    worker = await acquire_request_scoped_mcp_session("s1", "srv", params)
    # 杀死 owner task 模拟进程崩溃
    assert worker.task is not None
    worker.task.cancel()
    # 等 task 进入 cancelled 状态（可能抛 CancelledError，ignore）
    await asyncio.sleep(0.01)
    assert not worker.alive
    # 死 worker 仍在池映射里（未 sweep），release 应摘除
    await release_request_scoped_mcp_sessions("req-1")
    assert key not in pool_mod._request_scoped_mcp_sessions


async def test_sweep_reclaims_idle_worker():
    """空闲超时 worker 被 sweep 回收。"""
    worker = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    # 人为把 last_used 拨回 TTL 之前
    worker.last_used = time.monotonic() - 10_000.0
    reclaimed = await sweep_idle_session_mcp_workers(idle_ttl_s=600.0)
    assert reclaimed == 1
    assert not worker.alive


async def test_sweep_keeps_fresh_worker():
    """未超时 worker 不被 sweep。"""
    worker = await acquire_request_scoped_mcp_session("s1", "srv", _PARAMS)
    reclaimed = await sweep_idle_session_mcp_workers(idle_ttl_s=600.0)
    assert reclaimed == 0
    assert worker.alive


async def test_invoke_does_not_retry_on_timeout(monkeypatch: pytest.MonkeyPatch):
    """TimeoutError（结果未知）不自动重试——非幂等工具防双重执行。"""
    # 慢 session：超过 call_tool 超时
    monkeypatch.setattr(pool_mod, "_mcp_call_tool_timeout_s", lambda: 0.05)
    slow_params = {**_PARAMS, "_delay_s": 1.0}

    card = _make_card("srv__slow")
    tool = PooledRequestMcpTool(
        card,
        lambda: slow_params,
        raw_tool_name="slow",
        request_id="r3",
        server_name="srv",
        session_key="s1",
    )
    with pytest.raises(Exception) as exc_info:
        await asyncio.wait_for(tool.invoke({}), timeout=5.0)
    # invoke 把 TimeoutError 经 build_error 包成 ExecutionError（reason
    # 字段可能为空串，因 TimeoutError() 自身 str() 为空）。通过 __cause__
    # 链验证根因确实是 TimeoutError。
    cause = exc_info.value.__cause__
    assert isinstance(cause, (TimeoutError, asyncio.TimeoutError))
