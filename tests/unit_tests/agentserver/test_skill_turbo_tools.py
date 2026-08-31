# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Contracts for the skill_acceleration_exec tool wrapper."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenswarm.server.runtime.skill_turbo.plan_node import AbortError
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import skill_turbo

_QUERY = "做一份关于黄仁勋GTC讲话的PPT"


async def _empty_stream(*_args, **_kwargs):
    if False:
        yield None


def _fake_turbo():
    inst = MagicMock()
    inst.artifact_holder = {}
    inst.resume_stream = MagicMock(side_effect=_empty_stream)
    inst.run_stream = MagicMock(side_effect=_empty_stream)
    return inst


@pytest.fixture
def _skill_turbo_runtime():
    adapter = MagicMock()
    adapter.build_skill_turbo_config.return_value = MagicMock()
    adapter._instance = MagicMock(card=MagicMock())
    turbo_session = MagicMock()
    turbo_session.post_run = AsyncMock()
    return adapter, turbo_session


def test_skill_acceleration_exec_defers_timeout_to_pipeline() -> None:
    assert skill_turbo.card.properties["resilience"]["timeout_s"] is None


@pytest.mark.asyncio
async def test_load_resume_ctx_failure_still_post_runs_turbo_session(_skill_turbo_runtime) -> None:
    adapter, turbo_session = _skill_turbo_runtime
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_fake_turbo(),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(side_effect=RuntimeError("checkpointer down")),
        ),
    ):
        result = await skill_turbo.invoke({"query": _QUERY})

    turbo_session.post_run.assert_awaited_once()
    assert result.get("success") is True


@pytest.mark.asyncio
async def test_clear_resume_ctx_failure_does_not_mask_resume_success(_skill_turbo_runtime) -> None:
    adapter, turbo_session = _skill_turbo_runtime
    resume_ctx = {
        "plan_code": "plan()",
        "inputs": {"query": _QUERY},
        "pending_tool_call_id": "tc-1",
        "task_states": [],
    }
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_fake_turbo(),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(return_value=resume_ctx),
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.clear_resume_ctx",
            AsyncMock(side_effect=RuntimeError("clear failed")),
        ),
    ):
        result = await skill_turbo.invoke({"query": _QUERY})

    turbo_session.post_run.assert_awaited_once()
    assert result.get("success") is True
    assert "执行失败" not in str(result.get("error") or "")


def _raise_on_resume(exc: BaseException):
    async def _stream(*_args, **_kwargs):
        if False:
            yield None
        raise exc

    inst = MagicMock()
    inst.artifact_holder = {}
    inst.resume_stream = MagicMock(side_effect=_stream)
    inst.run_stream = MagicMock(side_effect=_empty_stream)
    return inst


@pytest.mark.asyncio
async def test_hitl_abort_does_not_clear_or_post_run_turbo_session(
    _skill_turbo_runtime,
) -> None:
    adapter, turbo_session = _skill_turbo_runtime
    turbo_session.close_stream = AsyncMock()
    resume_ctx = {
        "plan_code": "plan()",
        "inputs": {"query": _QUERY},
        "pending_tool_call_id": "tc-1",
        "task_states": [],
    }
    tic = SimpleNamespace(tool_call=SimpleNamespace(id="tc-ask"))
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_raise_on_resume(AbortError("ask_user")),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(return_value=resume_ctx),
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.clear_resume_ctx",
            AsyncMock(),
        ) as clear_ctx,
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.extract_tool_interrupt",
            return_value=tic,
        ),
    ):
        from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
            _SKILL_TURBO_HITL_PLACEHOLDER,
            set_skill_turbo_hitl_tic,
        )

        set_skill_turbo_hitl_tic(None)
        try:
            result = await skill_turbo.invoke({"query": _QUERY})
        finally:
            set_skill_turbo_hitl_tic(None)

    assert result == _SKILL_TURBO_HITL_PLACEHOLDER
    clear_ctx.assert_not_awaited()
    turbo_session.post_run.assert_not_awaited()
    turbo_session.close_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_abort_without_tic_keeps_resume_ctx(_skill_turbo_runtime) -> None:
    adapter, turbo_session = _skill_turbo_runtime
    resume_ctx = {
        "plan_code": "plan()",
        "inputs": {"query": _QUERY},
        "pending_tool_call_id": "tc-1",
        "task_states": [],
    }
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_raise_on_resume(AbortError("paused")),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(return_value=resume_ctx),
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.clear_resume_ctx",
            AsyncMock(),
        ) as clear_ctx,
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.extract_tool_interrupt",
            return_value=None,
        ),
    ):
        result = await skill_turbo.invoke({"query": _QUERY})

    assert result.get("success") is False
    clear_ctx.assert_not_awaited()
    turbo_session.post_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_generic_exception_keeps_resume_ctx(_skill_turbo_runtime) -> None:
    adapter, turbo_session = _skill_turbo_runtime
    resume_ctx = {
        "plan_code": "plan()",
        "inputs": {"query": _QUERY},
        "pending_tool_call_id": "tc-1",
        "task_states": [],
    }
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_raise_on_resume(RuntimeError("boom")),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(return_value=resume_ctx),
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.clear_resume_ctx",
            AsyncMock(),
        ) as clear_ctx,
    ):
        result = await skill_turbo.invoke({"query": _QUERY})

    assert result.get("success") is False
    clear_ctx.assert_not_awaited()
    turbo_session.post_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_not_handled_clears_resume_ctx(_skill_turbo_runtime) -> None:
    from jiuwenswarm.server.runtime.skill_turbo.agent import SkillTurboNotHandled

    adapter, turbo_session = _skill_turbo_runtime
    resume_ctx = {
        "plan_code": "plan()",
        "inputs": {"query": _QUERY},
        "pending_tool_call_id": "tc-1",
        "task_states": [],
    }
    with (
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_skill_turbo_adapter",
            return_value=adapter,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_task_id",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_skill_turbo_resume_answers",
            return_value={"audience": "高管"},
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools.get_current_request_metadata",
            return_value={},
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.get_subagent_parent_session",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.agents.harness.common.tools.subagent_executor.context_vars.get_effective_request_workspace_dir",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.agent.SkillTurbo",
            return_value=_raise_on_resume(SkillTurboNotHandled("no skill")),
        ),
        patch(
            "openjiuwen.core.session.agent.create_agent_session",
            return_value=turbo_session,
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.set_skill_turbo_id",
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.load_resume_ctx",
            AsyncMock(return_value=resume_ctx),
        ),
        patch(
            "jiuwenswarm.server.runtime.skill_turbo.permission_bridge.clear_resume_ctx",
            AsyncMock(),
        ) as clear_ctx,
    ):
        result = await skill_turbo.invoke({"query": _QUERY})

    assert result.get("success") is False
    clear_ctx.assert_awaited_once_with(turbo_session)
    turbo_session.post_run.assert_awaited_once()
