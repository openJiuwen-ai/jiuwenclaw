# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

# pylint: disable=protected-access
# 测试代码访问私有成员是合理的测试实践

"""SendFileToolkit 路由隔离测试。

回归场景：send_file_to_user 工具按全局名注册成单例，并发请求间会互相覆盖
实例字段（session_id / request_id / channel_id / metadata）。改造后工具执行时
优先从请求级 ContextVar 解析路由，按 async 上下文隔离，避免会话串扰。
"""

import asyncio

import pytest

from jiuwenclaw.agentserver.tools.send_file_to_user import SendFileToolkit
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_send_file_request_context,
    reset_send_file_request_context,
    set_send_file_request_context,
)


def test_resolve_route_falls_back_to_instance_fields_when_no_context():
    """无 ContextVar 时回退到实例字段（保持旧行为）。"""
    toolkit = SendFileToolkit(
        request_id="req-1",
        session_id="sess-1",
        channel_id="officeclaw",
        metadata={"k": "v"},
    )
    route = toolkit._resolve_route()
    assert route.request_id == "req-1"
    assert route.session_id == "sess-1"
    assert route.channel_id == "officeclaw"
    assert route.metadata == {"k": "v"}


def test_resolve_route_prefers_context_over_instance_fields():
    """ContextVar 存在时优先于实例字段（修复并发覆盖串扰）。"""
    # 实例字段模拟「最后一次注册的 session」（被其它请求覆盖后的脏值）
    toolkit = SendFileToolkit(
        request_id="stale-req",
        session_id="stale-sess",
        channel_id="officeclaw",
        metadata={"stale": True},
    )
    token = set_send_file_request_context(
        request_id="real-req",
        session_id="real-sess",
        channel_id="officeclaw",
        metadata={"real": True},
    )
    try:
        route = toolkit._resolve_route()
        assert route.request_id == "real-req"
        assert route.session_id == "real-sess"
        assert route.metadata == {"real": True}
    finally:
        reset_send_file_request_context(token)


@pytest.mark.asyncio
async def test_concurrent_contexts_are_isolated():
    """并发协程各自的路由上下文互不串扰。"""
    # 两个并发请求共享同一个单例 toolkit（实例字段被后注册者覆盖）
    shared_toolkit = SendFileToolkit(
        request_id="last-registered",
        session_id="last-registered",
        channel_id="officeclaw",
        metadata=None,
    )

    async def run_request(session_id: str, request_id: str) -> tuple[str, str]:
        token = set_send_file_request_context(
            request_id=request_id,
            session_id=session_id,
            channel_id="officeclaw",
        )
        try:
            # 让出控制权，模拟与另一请求交错执行
            await asyncio.sleep(0)
            route = shared_toolkit._resolve_route()
            await asyncio.sleep(0)
            return route.session_id, route.request_id
        finally:
            reset_send_file_request_context(token)

    results = await asyncio.gather(
        run_request("sess-A", "req-A"),
        run_request("sess-B", "req-B"),
    )
    assert ("sess-A", "req-A") in results
    assert ("sess-B", "req-B") in results


def test_context_reset_restores_previous_value():
    """reset 后恢复到先前的上下文值。"""
    assert get_send_file_request_context() is None
    token = set_send_file_request_context(session_id="sess-x")
    try:
        assert get_send_file_request_context() == {"session_id": "sess-x"}
    finally:
        reset_send_file_request_context(token)
    assert get_send_file_request_context() is None
