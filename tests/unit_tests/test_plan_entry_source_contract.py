# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""P7 契约测试：plan_entry_source 字面量跨层一致。

计划 PLAN_mode_refactor_phased.md P7 / P4.2 坑点。``plan_entry_source`` 是
字符串字面量契约——后端 ``agent_ws_server._is_explicit_plan_entry_request``
只认 ``_PLAN_ENTRY_SOURCES``（slash_command / plan_toggle），TUI ``app-state.ts``
序列化 ``pendingPlanEntrySource`` 成该字段，Web ``useWebSocket.ts`` 发 plan_toggle。

P7 把后端常量提到 schema 层单源（``chat_send.PLAN_ENTRY_SOURCES``），前端 TS
各自定义同名常量。跨语言无法共享一个常量对象，故本测试保证：

1. 后端 schema 层常量值正确（slash_command / plan_toggle）
2. ``agent_ws_server`` import schema 层常量（不再本地硬编码）
3. ``_is_explicit_plan_entry_request`` 对合法值返 True、非法值返 False
4. 前端 TS 字面量与后端一致（读编译后 dist JS grep）
"""

# pylint: disable=protected-access

from __future__ import annotations

import importlib
import re
from pathlib import Path

import pytest

from jiuwenswarm.common.schema.chat_send import (
    PLAN_ENTRY_SOURCE_PLAN_TOGGLE,
    PLAN_ENTRY_SOURCE_SLASH_COMMAND,
    PLAN_ENTRY_SOURCES,
)


def test_plan_entry_source_constants_values():
    """schema 层常量值是契约字面量，不可漂移。"""
    assert PLAN_ENTRY_SOURCE_SLASH_COMMAND == "slash_command"
    assert PLAN_ENTRY_SOURCE_PLAN_TOGGLE == "plan_toggle"
    assert PLAN_ENTRY_SOURCES == frozenset({"slash_command", "plan_toggle"})


def test_agent_ws_server_uses_schema_constant():
    """agent_ws_server 不再本地硬编码 _PLAN_ENTRY_SOURCES，改 import schema 层。"""
    server = importlib.import_module("jiuwenswarm.server.agent_ws_server")
    # import 别名 _PLAN_ENTRY_SOURCES 应指向同一个 frozenset 对象
    assert server._PLAN_ENTRY_SOURCES is PLAN_ENTRY_SOURCES


@pytest.mark.parametrize("source", ["slash_command", "plan_toggle"])
def test_is_explicit_plan_entry_accepts_valid_sources(source):
    """合法 plan_entry_source 被识别为显式进入 plan。"""
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    request = AgentRequest(
        request_id="req_contract",
        channel_id="tui",
        session_id="sess_contract",
        req_method=ReqMethod.CHAT_SEND,
        params={"mode": "agent.work.plan", "plan_entry_source": source},
    )
    assert AgentWebSocketServer._is_explicit_plan_entry_request(request) is True


def test_is_explicit_plan_entry_rejects_unknown_source():
    """非法 plan_entry_source 不被识别（防重入闸门对 Web 生效的前提）。"""
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    request = AgentRequest(
        request_id="req_contract",
        channel_id="web",
        session_id="sess_contract",
        req_method=ReqMethod.CHAT_SEND,
        params={"mode": "agent.work.plan", "plan_entry_source": "stale_residue"},
    )
    assert AgentWebSocketServer._is_explicit_plan_entry_request(request) is False


def test_is_explicit_plan_entry_rejects_missing_source():
    """缺省 plan_entry_source 不被识别（残留请求模式）。"""
    from jiuwenswarm.common.schema.agent import AgentRequest
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    request = AgentRequest(
        request_id="req_contract",
        channel_id="web",
        session_id="sess_contract",
        req_method=ReqMethod.CHAT_SEND,
        params={"mode": "agent.work.plan"},
    )
    assert AgentWebSocketServer._is_explicit_plan_entry_request(request) is False


# ── 前端 TS 字面量一致性（读编译后 dist JS）─────────────────────────────


def _read_dist(rel: str) -> str:
    """读 TUI 前端 dist 编译产物。"""
    repo_root = Path(__file__).resolve().parents[2]
    path = repo_root / rel
    if not path.exists():
        pytest.skip(f"{rel} 不存在（前端未 build），跳过前端字面量检查")
    return path.read_text(encoding="utf-8")


def test_tui_frontend_uses_slash_command_literal():
    """TUI dist JS 含 slash_command 字面量（与后端常量一致）。"""
    text = _read_dist("jiuwenswarm/channels/tui/frontend/dist/app-state.js")
    assert '"slash_command"' in text or "'slash_command'" in text, (
        "TUI dist 未找到 slash_command 字面量——前端常量与后端不一致"
    )


def test_web_frontend_dist_not_required_for_backend_contract():
    """Web 前端是 vite 打包，无独立 dist JS 可 grep——本测试只覆盖后端 + TUI。

    Web 前端字面量由 wireMode.test.mjs / useWebSocket 运行时行为间接保证
    （resolvePlanEntryPayload 返回 { plan_entry_source: 'plan_toggle' }）。
    """
    pytest.skip("Web 前端 plan_toggle 字面量由前端测试覆盖，后端契约测试不读 vite 产物")
