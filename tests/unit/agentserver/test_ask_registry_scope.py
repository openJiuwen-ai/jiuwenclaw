# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.deep_agent.ask_user_question_registry import (
    AskUserQuestionRegistry,
    ask_user_question_request_scope,
)
from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey


@pytest.fixture(autouse=True)
def _reset_ask_registry():
    AskUserQuestionRegistry.reset_instance_for_tests()
    yield
    AskUserQuestionRegistry.reset_instance_for_tests()


@pytest.mark.asyncio
async def test_resolve_requires_matching_tenant() -> None:
    reg = AskUserQuestionRegistry.get_instance()
    scope_a = RuntimeScopeKey.from_ids("svc1", "aid1", "sess")
    scope_b = RuntimeScopeKey.from_ids("svc2", "aid2", "sess")

    fut = reg.register(scope_a, "ask_uq_1")
    assert reg.resolve(scope_b, "ask_uq_1", [{"ok": True}]) is False
    assert not fut.done()
    assert reg.resolve(scope_a, "ask_uq_1", [{"ok": True}]) is True
    assert fut.result() == [{"ok": True}]


@pytest.mark.asyncio
async def test_resolve_preserves_explicit_skipped_status() -> None:
    reg = AskUserQuestionRegistry.get_instance()
    scope = RuntimeScopeKey.from_ids("svc1", "aid1", "sess")

    fut = reg.register(scope, "ask_uq_skip")
    assert reg.resolve(scope, "ask_uq_skip", [], status="skipped") is True
    assert fut.result() == {"status": "skipped", "answers": []}


@pytest.mark.asyncio
async def test_cancel_for_session_is_tenant_scoped() -> None:
    reg = AskUserQuestionRegistry.get_instance()
    scope_a = RuntimeScopeKey.from_ids("svc1", "aid1", "sess")
    scope_b = RuntimeScopeKey.from_ids("svc2", "aid2", "sess")  # same session string

    fut_a = reg.register(scope_a, "ask_uq_a")
    fut_b = reg.register(scope_b, "ask_uq_b")

    reg.cancel_for_session(scope_a)
    assert fut_a.cancelled()
    assert not fut_b.done()


@pytest.mark.asyncio
async def test_request_scope_binds_tenant_flags() -> None:
    reg = AskUserQuestionRegistry.get_instance()
    scope = RuntimeScopeKey.from_ids("svc", "aid", "sess-1")
    async with ask_user_question_request_scope(
        interactive_ask=True,
        session_id="sess-1",
        stream_request_id="req-1",
        channel_id="web",
        scope=scope,
    ):
        assert reg.stream_interactive_ask_enabled(scope, "req-1") is True
        assert reg.session_interactive_ask_enabled(scope, "sess-1") is True
        other = RuntimeScopeKey.from_ids("other", "aid", "sess-1")
        assert reg.session_interactive_ask_enabled(other, "sess-1") is False
