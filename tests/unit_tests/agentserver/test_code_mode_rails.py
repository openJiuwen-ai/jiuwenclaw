"""Focused tests for migrated Code-mode plan rails."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.code.code_agent_mode_rail import (
    CodeAgentModeRail,
)
from jiuwenclaw.agentserver.deep_agent.rails.code.code_confirm_interrupt_rail import (
    CodeConfirmInterruptRail,
    build_confirm_interrupt_message,
)
from jiuwenclaw.agentserver.deep_agent.rails.code.code_plan_approval_interrupt_rail import (
    PlanApprovalInterruptRail,
    build_plan_approval_options_from_message,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("target_mode", ["normal", "auto"])
async def test_code_mode_blocks_switch_mode_exit_in_plan(target_mode: str) -> None:
    rail = CodeAgentModeRail(allowed_tools=["switch_mode"])
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan", plan_slug="test-plan")
    )
    rail._agent = agent
    parent = AsyncMock()
    with patch.object(CodeAgentModeRail.__bases__[0], "before_tool_call", parent):
        ctx = SimpleNamespace(
            session=SimpleNamespace(),
            inputs=SimpleNamespace(
                tool_name="switch_mode",
                tool_call=SimpleNamespace(
                    id="call_1",
                    arguments=f'{{"mode": "{target_mode}"}}',
                ),
                tool_args={"mode": target_mode},
            ),
            extra={},
        )
        await rail.before_tool_call(ctx)

    parent.assert_not_awaited()
    assert ctx.extra["_skip_tool"] is True


@pytest.mark.asyncio
async def test_code_mode_blocks_bash_writes_but_allows_reads() -> None:
    rail = CodeAgentModeRail()
    agent = MagicMock()
    agent.system_prompt_builder = SimpleNamespace(language="en")
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    rail._agent = agent
    parent = AsyncMock()

    blocked_ctx = SimpleNamespace(
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="bash",
            tool_call=SimpleNamespace(
                id="call-write", arguments='{"command":"mkdir x"}'
            ),
            tool_args={"command": "mkdir x"},
        ),
        extra={},
    )
    with patch.object(CodeAgentModeRail.__bases__[0], "before_tool_call", parent):
        await rail.before_tool_call(blocked_ctx)
    assert blocked_ctx.extra["_skip_tool"] is True

    read_ctx = SimpleNamespace(
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="bash",
            tool_call=SimpleNamespace(id="call-read", arguments='{"command":"ls"}'),
            tool_args={"command": "ls"},
        ),
        extra={},
    )
    with patch.object(CodeAgentModeRail.__bases__[0], "before_tool_call", parent):
        await rail.before_tool_call(read_ctx)
    assert "_skip_tool" not in read_ctx.extra


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["write_file", "edit_file"])
async def test_plan_mode_rejects_writes_outside_plan_file(
    tmp_path: Path,
    tool_name: str,
) -> None:
    rail = CodeAgentModeRail()
    agent = MagicMock()
    agent.system_prompt_builder = SimpleNamespace(language="en")
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    agent.get_plan_file_path.return_value = tmp_path / "plan.md"
    rail._agent = agent
    ctx = SimpleNamespace(
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name=tool_name,
            tool_call=SimpleNamespace(
                id="call-file",
                arguments='{"path":"src/main.py","content":"x"}',
            ),
            tool_args={"path": "src/main.py", "content": "x"},
        ),
        extra={},
    )

    await rail.before_tool_call(ctx)

    assert ctx.extra["_skip_tool"] is True


@pytest.mark.asyncio
async def test_code_confirm_rejects_switch_mode_exit_in_plan() -> None:
    rail = CodeConfirmInterruptRail(tool_names=["switch_mode"])
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan", plan_slug="test-plan")
    )
    agent.system_prompt_builder = SimpleNamespace(language="cn")
    tool_call = SimpleNamespace(
        name="switch_mode",
        arguments='{"mode": "normal"}',
    )

    decision = await rail.resolve_interrupt(
        SimpleNamespace(agent=agent, session=SimpleNamespace()),
        tool_call,
        user_input={"approved": True},
    )

    assert "switch_mode" in str(decision.tool_result)


def test_code_confirm_message_contains_mode_and_argument_preview() -> None:
    message = build_confirm_interrupt_message(
        "write_file",
        {"path": "src/main.py", "content": "finish plan"},
    )

    assert "write_file" in message
    assert "src/main.py" in message
    assert "finish plan" in message


def test_plan_approval_options_are_structured() -> None:
    options = build_plan_approval_options_from_message(
        "**计划审批**\n\nAgent 已完成计划制定，等待你审批：\n\nplan"
    )

    assert [option["value"] for option in options] == ["approve", "reject"]


@pytest.mark.asyncio
async def test_plan_approval_shows_plan_and_reject_stays_in_plan() -> None:
    with TemporaryDirectory() as temp_dir:
        plan_path = Path(temp_dir) / "plan.md"
        plan_path.write_text("write the implementation", encoding="utf-8")
        rail = PlanApprovalInterruptRail()
        agent = MagicMock()
        agent.system_prompt_builder = SimpleNamespace(language="en")
        agent.load_state.return_value = SimpleNamespace(
            plan_mode=SimpleNamespace(mode="plan")
        )
        agent.get_plan_file_path.return_value = plan_path
        rail.init(agent)
        ctx = SimpleNamespace(
            agent=agent,
            session=SimpleNamespace(),
            extra={},
        )
        tool_call = SimpleNamespace(name="exit_plan_mode", arguments="{}")

        interrupt = await rail.resolve_interrupt(ctx, tool_call, None)
        assert "write the implementation" in interrupt.request.message

        decision = await rail.resolve_interrupt(
            ctx,
            tool_call,
            {"approved": False, "feedback": "revise it"},
        )
        assert ctx.extra["_plan_rejected"] is True
        assert "revise it" in str(decision.tool_result)

        approved = await rail.resolve_interrupt(
            SimpleNamespace(
                agent=agent,
                session=SimpleNamespace(),
                extra={},
            ),
            tool_call,
            {"approved": True},
        )
        assert approved.__class__.__name__ == "ApproveResult"


def test_plan_preview_reads_only_bounded_content() -> None:
    with TemporaryDirectory() as temp_dir:
        plan_path = Path(temp_dir) / "plan.md"
        plan_path.write_text("x" * 10_000, encoding="utf-8")
        rail = PlanApprovalInterruptRail()
        agent = MagicMock()
        agent.get_plan_file_path.return_value = plan_path
        rail.init(agent)

        content = rail._read_plan_content(SimpleNamespace(session=SimpleNamespace()))

        assert len(content) <= 3_001


@pytest.mark.asyncio
async def test_plan_approval_rejects_exit_outside_plan_mode() -> None:
    rail = PlanApprovalInterruptRail()
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="normal")
    )
    agent.system_prompt_builder = SimpleNamespace(language="en")
    rail.init(agent)
    ctx = SimpleNamespace(agent=agent, session=SimpleNamespace(), extra={})
    tool_call = SimpleNamespace(name="exit_plan_mode", arguments="{}")

    decision = await rail.resolve_interrupt(ctx, tool_call, None)

    assert "plan mode" in str(decision.tool_result)


@pytest.mark.asyncio
async def test_plan_approval_invalid_payload_keeps_interrupt_pending() -> None:
    rail = PlanApprovalInterruptRail()
    agent = MagicMock()
    agent.system_prompt_builder = SimpleNamespace(language="en")
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    rail.init(agent)
    ctx = SimpleNamespace(agent=agent, session=SimpleNamespace(), extra={})
    tool_call = SimpleNamespace(name="exit_plan_mode", arguments="{}")

    decision = await rail.resolve_interrupt(ctx, tool_call, {"invalid": True})

    assert decision.__class__.__name__ == "InterruptResult"
    assert ctx.extra["_plan_rejected"] is True
    assert ctx.extra["_plan_pending"] is True


@pytest.mark.asyncio
async def test_plan_approval_exception_marks_rejection(monkeypatch) -> None:
    rail = PlanApprovalInterruptRail()
    ctx = SimpleNamespace(extra={})

    async def raise_timeout(*_args, **_kwargs):
        raise TimeoutError("approval timed out")

    monkeypatch.setattr(
        PlanApprovalInterruptRail.__bases__[0],
        "resolve_interrupt",
        raise_timeout,
    )

    with pytest.raises(TimeoutError):
        await rail.resolve_interrupt(ctx, None, {"approved": True})

    assert ctx.extra["_plan_rejected"] is True
    assert ctx.extra["_plan_approved"] is False


@pytest.mark.asyncio
async def test_plan_approval_pending_or_rejected_never_restores_normal() -> None:
    rail = CodeAgentModeRail()
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    rail._agent = agent
    ctx = SimpleNamespace(
        agent=agent,
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="exit_plan_mode",
            tool_result="rejected",
        ),
        extra={"_plan_rejected": True, "_plan_approved": False},
    )

    await rail.after_tool_call(ctx)

    agent.restore_mode_after_plan_exit.assert_not_called()


@pytest.mark.asyncio
async def test_plan_approval_explicit_approve_allows_mode_restore() -> None:
    rail = CodeAgentModeRail()
    agent = MagicMock()
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    rail._agent = agent
    ctx = SimpleNamespace(
        agent=agent,
        session=SimpleNamespace(),
        inputs=SimpleNamespace(
            tool_name="exit_plan_mode",
            tool_result="empty plan",
        ),
        extra={"_plan_approved": True},
    )

    await rail.after_tool_call(ctx)

    agent.restore_mode_after_plan_exit.assert_called_once_with(ctx.session)


@pytest.mark.asyncio
async def test_empty_plan_still_requires_explicit_approval() -> None:
    rail = PlanApprovalInterruptRail()
    agent = MagicMock()
    agent.system_prompt_builder = SimpleNamespace(language="en")
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    agent.get_plan_file_path.return_value = Path("missing-plan.md")
    rail.init(agent)
    ctx = SimpleNamespace(agent=agent, session=SimpleNamespace(), extra={})
    tool_call = SimpleNamespace(name="exit_plan_mode", arguments="{}")

    decision = await rail.resolve_interrupt(ctx, tool_call, None)

    assert decision.__class__.__name__ == "InterruptResult"
    assert "empty" in decision.request.message.lower()
