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
import os
import tempfile
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

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

    # 构造一个 fake aiohttp request
    class _FakeRequest:
        def __init__(self, payload: dict) -> None:
            self._payload = payload

        async def json(self) -> dict:
            return self._payload

    resp = await server._handle_run(_FakeRequest({"input": "hi"}))

    # aiohttp.web.json_response 的 .body 是 JSON bytes
    import json as _json
    data = _json.loads(resp.body)
    assert data["ok"] is True
    assert len(data["trace_id"]) == 32
    assert isinstance(data["trace"], list)
    assert data["request_id"].startswith("trace-http-")
