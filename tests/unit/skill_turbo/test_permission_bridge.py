# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""permission_bridge 纯函数胶水的单元测试。"""

from __future__ import annotations

from typing import Any

import pytest

from openjiuwen.core.runner.callback import AbortError
from openjiuwen.core.single_agent.interrupt.exception import ToolInterruptException
from openjiuwen.core.single_agent.interrupt.response import InterruptRequest
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY

from jiuwenclaw.agentserver.skill_turbo.permission_bridge import (
    SKILL_TURBO_RESUME_CTX_KEY,
    SkillTurboToolCall,
    build_tool_ctx,
    clear_resume_ctx,
    extract_tool_interrupt,
    is_blocking_abort,
    load_resume_ctx,
    save_resume_ctx,
    set_skill_turbo_id,
)


class FakeSession:
    """最小 Session stub，实现 resume ctx 读写所需的 session 接口。"""

    def __init__(self, session_id: str = "fake-sid") -> None:
        self.session_id = session_id
        self._state: dict[str, Any] = {}

    async def pre_run(self, inputs: Any = None) -> None:
        pass

    async def post_run(self) -> None:
        pass

    def get_state(self, key: str) -> Any:
        return self._state.get(key)

    def update_state(self, patch: dict[str, Any]) -> None:
        self._state.update(patch)


# ──────────────────────── build_tool_ctx ────────────────────────


class TestBuildToolCtx:
    @pytest.mark.unit
    def test_first_call_has_no_resume_input(self) -> None:
        ctx = build_tool_ctx(
            session=FakeSession(),
            tool_name="read_file",
            tool_args={"path": "a.txt"},
            tool_call_id="tc-1",
            resume_user_input=None,
        )
        assert ctx.inputs.tool_name == "read_file"
        assert ctx.inputs.tool_call.id == "tc-1"
        assert ctx.inputs.tool_args == {"path": "a.txt"}
        # 首次调用 ctx.extra 不应携带 resume key（否则 rail 会误以为是 resume）
        assert RESUME_USER_INPUT_KEY not in ctx.extra

    @pytest.mark.unit
    def test_resume_user_input_injected_into_extra(self) -> None:
        payload = {"approved": True}
        ctx = build_tool_ctx(
            session=None,
            tool_name="write_file",
            tool_args={"path": "b.txt", "content": "x"},
            tool_call_id="tc-2",
            resume_user_input=payload,
        )
        assert ctx.extra[RESUME_USER_INPUT_KEY] is payload


# ──────────────────────── extract_tool_interrupt / is_blocking_abort ────────────────────────


def _make_abort_with_tool_interrupt() -> AbortError:
    req = InterruptRequest(message="approve?", payload_schema={})
    tc = SkillTurboToolCall(id="tc-x", name="write_file", arguments={})
    tic = ToolInterruptException(request=req, tool_call=tc)
    return AbortError(reason="tool interrupted", cause=tic)


class TestExtractToolInterrupt:
    @pytest.mark.unit
    def test_extract_from_abort_with_cause(self) -> None:
        exc = _make_abort_with_tool_interrupt()
        tic = extract_tool_interrupt(exc)
        assert tic is not None
        assert tic.request.message == "approve?"
        assert tic.tool_call.id == "tc-x"

    @pytest.mark.unit
    def test_extract_returns_none_for_plain_exception(self) -> None:
        assert extract_tool_interrupt(RuntimeError("boom")) is None

    @pytest.mark.unit
    def test_extract_via_raise_from_chain(self) -> None:
        req = InterruptRequest(message="m", payload_schema={})
        tic = ToolInterruptException(
            request=req, tool_call=SkillTurboToolCall(id="tc", name="t", arguments={})
        )
        try:
            try:
                raise tic
            except ToolInterruptException as inner:
                raise RuntimeError("wrap") from inner
        except RuntimeError as outer:
            got = extract_tool_interrupt(outer)
            assert got is tic

    @pytest.mark.unit
    def test_is_blocking_abort_true(self) -> None:
        assert is_blocking_abort(_make_abort_with_tool_interrupt()) is True

    @pytest.mark.unit
    def test_is_blocking_abort_false_for_plain_abort(self) -> None:
        # AbortError 不带 ToolInterruptException → 不是 HITL 阻塞
        assert is_blocking_abort(AbortError(reason="cancelled")) is False

    @pytest.mark.unit
    def test_is_blocking_abort_false_for_runtime_error(self) -> None:
        assert is_blocking_abort(RuntimeError("oops")) is False


# ──────────────────────── resume ctx 读写 ────────────────────────


class TestResumeCtx:
    @pytest.fixture(autouse=True)
    def _clear_store(self) -> None:
        """每个用例使用独立 FakeSession，无需清理模块级 store。"""
        pass

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self) -> None:
        session = FakeSession(session_id="sid-roundtrip")
        await save_resume_ctx(
            session,
            plan_code="root = 1",
            inputs={"k": "v", "nested": {"a": 1}},
            pending_tool_call_id="tc-9",
        )
        got = await load_resume_ctx(session)
        assert got is not None
        assert got["plan_code"] == "root = 1"
        assert got["inputs"] == {"k": "v", "nested": {"a": 1}}
        assert got["pending_tool_call_id"] == "tc-9"
        # 修改返回值不得污染 session state
        got["plan_code"] = "hacked"
        got["inputs"]["k"] = "mutated"
        got["inputs"]["nested"]["a"] = 999
        again = await load_resume_ctx(session)
        assert again is not None
        assert again["plan_code"] == "root = 1"
        assert again["inputs"] == {"k": "v", "nested": {"a": 1}}

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_load_returns_none_when_absent(self) -> None:
        assert await load_resume_ctx(FakeSession(session_id="sid-absent")) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_load_returns_none_when_empty_plan_code(self) -> None:
        sid = "sid-empty"
        session = FakeSession(session_id=sid)
        session.update_state({SKILL_TURBO_RESUME_CTX_KEY: {
            "plan_code": "",
            "inputs": {},
            "pending_tool_call_id": "",
        }})
        assert await load_resume_ctx(session) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_resume_ctx(self) -> None:
        session = FakeSession(session_id="sid-clear")
        await save_resume_ctx(
            session, plan_code="x", inputs={}, pending_tool_call_id="tc"
        )
        assert await load_resume_ctx(session) is not None
        await clear_resume_ctx(session)
        assert await load_resume_ctx(session) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_with_none_session_is_noop(self) -> None:
        # 不应抛错
        await save_resume_ctx(None, plan_code="x", inputs={}, pending_tool_call_id="t")
        await clear_resume_ctx(None)
        assert await load_resume_ctx(None) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_save_writes_to_session_state(self) -> None:
        """save 后 session state 中应有 resume_ctx。"""
        session = FakeSession(session_id="sid-state")
        await save_resume_ctx(
            session,
            plan_code="root = state",
            inputs={"k": "v"},
            pending_tool_call_id="tc-state",
        )
        entry = session.get_state(SKILL_TURBO_RESUME_CTX_KEY)
        assert isinstance(entry, dict)
        assert entry["plan_code"] == "root = state"
        assert entry["pending_tool_call_id"] == "tc-state"

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_clear_clears_session_state(self) -> None:
        """clear 后 session state 为 None。"""
        session = FakeSession(session_id="sid-dbl-clear")
        await save_resume_ctx(
            session,
            plan_code="root = dbl",
            inputs={},
            pending_tool_call_id="tc-dbl",
        )
        await clear_resume_ctx(session)
        assert await load_resume_ctx(session) is None
        assert session.get_state(SKILL_TURBO_RESUME_CTX_KEY) is None

    @pytest.mark.unit
    @pytest.mark.asyncio
    async def test_load_returns_none_when_session_state_empty(self) -> None:
        """session state 为空时返回 None。"""
        session = FakeSession(session_id="sid-empty-state")
        session.update_state({SKILL_TURBO_RESUME_CTX_KEY: None})
        assert await load_resume_ctx(session) is None
