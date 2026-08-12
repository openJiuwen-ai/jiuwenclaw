# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""TraceHttpServer 冒烟测试：验证 input -> trace_id -> OTel trace 树闭环。

不依赖真实 agent：mock AgentManager.process_message 为 no-op，仅验证
- trace_id 是 32 位 hex 且与根 span 一致；
- force_flush 后 get_trace_tree 能取回根 span "http.run"；
- handler 返回结构正确。

deep 模式 set_telemetry_context 的修复在 interface_deep 上有独立单元测试更合适，
这里只覆盖 HTTP 端点侧的 trace 收集闭环。
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest


def _init_telemetry_with_temp_db(tmp_path: Any) -> str:
    """初始化 telemetry，导出到临时 sqlite db，返回 db 路径。"""
    db_path = str(tmp_path / "traces.db")
    os.environ["OTEL_ENABLED"] = "true"
    os.environ["OTEL_TRACES_EXPORTER"] = "sqlite"
    os.environ["OTEL_SQLITE_DB_PATH"] = db_path
    # 重置 telemetry 单例缓存并重新初始化
    import jiuwenswarm.telemetry as telemetry
    telemetry.reset_telemetry()
    telemetry.init_telemetry()
    return db_path


class _FakeRequest:
    """最小 aiohttp request 替身：仅实现 .json()。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class _RecordingAgentManager:
    """记录 process_message 请求与 cleanup_session_runtime 调用的假 manager。

    process_message 不跑真实 agent（no-op），仅让 _handle_run 走完 trace 收集
    + finally 清理闭环；cleanup_session_runtime 只记录调用，不真回收。
    """

    def __init__(self) -> None:
        self.requests: list[Any] = []
        self.cleaned: list[tuple[str, str]] = []

    async def process_message(self, request: Any) -> Any:
        self.requests.append(request)
        await asyncio.sleep(0)
        return None

    async def cleanup_session_runtime(
        self, *, channel_id: str, session_id: str
    ) -> bool:
        self.cleaned.append((channel_id, session_id))
        return True


@pytest.mark.asyncio
async def test_run_and_collect_returns_trace_tree(tmp_path: Any) -> None:
    db_path = _init_telemetry_with_temp_db(tmp_path)

    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.server.trace_http import TraceHttpServer

    # mock agent_manager.process_message：不跑真实 agent，只让 handler 走完闭环
    fake_manager = SimpleNamespace()
    fake_manager.process_message = AsyncMock(return_value=None)

    server = TraceHttpServer(agent_manager=fake_manager)

    req = AgentRequest(
        request_id="trace-http-test",
        channel_id="trace_http",
        session_id="trace-http-test-sess",
        req_method=None,
        params={"query": "hello", "mode": "agent.plan"},
        is_stream=False,
        metadata={},
    )

    trace_id, trace, ok, error = await server._run_and_collect(req, timeout=30)

    assert ok, f"expected ok=True, got error={error}"
    assert error is None
    # trace_id 是 32 位 hex
    assert len(trace_id) == 32
    assert all(c in "0123456789abcdef" for c in trace_id)
    # 至少能取回根 span "http.run"
    assert isinstance(trace, list)
    root_names = [s.get("name") for s in trace]
    assert "http.run" in root_names, f"root span http.run missing, got {root_names}"
    # 根 span 的 trace_id 与返回一致
    for span in trace:
        assert span.get("trace_id") == trace_id


@pytest.mark.asyncio
async def test_handle_run_returns_json(tmp_path: Any) -> None:
    _init_telemetry_with_temp_db(tmp_path)

    from jiuwenswarm.server.trace_http import TraceHttpServer

    fake_manager = SimpleNamespace()
    fake_manager.process_message = AsyncMock(return_value=None)
    server = TraceHttpServer(agent_manager=fake_manager)

    resp = await server._handle_run(_FakeRequest({"input": "hi"}))

    # aiohttp.web.json_response 的 .body 是 JSON bytes
    data = json.loads(resp.body)
    assert data["ok"] is True
    assert len(data["trace_id"]) == 32
    assert isinstance(data["trace"], list)
    assert data["request_id"].startswith("trace-http-")


@pytest.mark.asyncio
async def test_run_assigns_unique_session_per_request_and_cleans_runtime(
    tmp_path: Any,
) -> None:
    """同秒并发的两个 /run 必须各拿唯一 session 并各自触发 runtime 清理。"""
    _init_telemetry_with_temp_db(tmp_path)

    from jiuwenswarm.server.trace_http import TraceHttpServer

    manager = _RecordingAgentManager()
    server = TraceHttpServer(agent_manager=manager)

    responses = await asyncio.gather(
        server._handle_run(_FakeRequest({"input": "first"})),
        server._handle_run(_FakeRequest({"input": "second"})),
    )
    payloads = [json.loads(r.body) for r in responses]

    # 唯一 session：无显式 session_id 时回退到 request_id（完整 uuid）
    assert len({p["session_id"] for p in payloads}) == 2
    assert all(p["session_id"] == p["request_id"] for p in payloads)
    # trace 仍返回
    assert all(
        len(p["trace_id"]) == 32 and isinstance(p["trace"], list) for p in payloads
    )
    assert all(p["ok"] is True for p in payloads)
    # 每个 session 跑完都被清理一次
    assert set(manager.cleaned) == {
        ("trace_http", payloads[0]["session_id"]),
        ("trace_http", payloads[1]["session_id"]),
    }
    assert len(manager.cleaned) == 2


@pytest.mark.asyncio
async def test_run_preserves_explicit_session_id_and_cleans_runtime(
    tmp_path: Any,
) -> None:
    """调用方传入的 session_id 作为关联 key 保留，runtime 仍按一次性清理。"""
    _init_telemetry_with_temp_db(tmp_path)

    from jiuwenswarm.server.trace_http import TraceHttpServer

    manager = _RecordingAgentManager()
    server = TraceHttpServer(agent_manager=manager)

    resp = await server._handle_run(
        _FakeRequest({"input": "continue", "session_id": "rollout-42"})
    )
    payload = json.loads(resp.body)

    assert payload["session_id"] == "rollout-42"
    assert payload["ok"] is True
    assert len(payload["trace_id"]) == 32
    assert isinstance(payload["trace"], list)
    assert manager.cleaned == [("trace_http", "rollout-42")]


@pytest.mark.asyncio
async def test_run_cleans_runtime_even_after_agent_timeout(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """agent 超时后仍必须回收一次性 session runtime（finally 兜底）。"""
    _init_telemetry_with_temp_db(tmp_path)

    from jiuwenswarm.server import trace_http as trace_http_module
    from jiuwenswarm.server.trace_http import TraceHttpServer

    class _SlowAgentManager(_RecordingAgentManager):
        async def process_message(self, request: Any) -> Any:
            self.requests.append(request)
            await asyncio.sleep(0.3)  # 超过 timeout，必被 wait_for 取消
            return None

    # 把默认 timeout 压到 0：body 不传 timeout 时走 _DEFAULT_TIMEOUT，
    # asyncio.wait_for(..., 0) 立即超时并取消 process_message 协程。
    monkeypatch.setattr(trace_http_module, "_DEFAULT_TIMEOUT", 0)

    manager = _SlowAgentManager()
    server = TraceHttpServer(agent_manager=manager)

    resp = await server._handle_run(_FakeRequest({"input": "x"}))
    payload = json.loads(resp.body)

    assert payload["ok"] is False
    assert "timed out" in payload["error"]
    # trace 仍尽力返回（可能为空树，但字段在）
    assert "trace_id" in payload and isinstance(payload["trace"], list)
    # 超时路径下 finally 仍触发清理
    assert manager.cleaned == [("trace_http", payload["session_id"])]
