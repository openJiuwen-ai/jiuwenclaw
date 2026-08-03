"""Component-level SR-014 acceptance flow for Code-mode rails."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.rails.code.code_agent_mode_rail import (
    CodeAgentModeRail,
)
from jiuwenclaw.agentserver.deep_agent.rails.code.code_plan_approval_interrupt_rail import (
    PlanApprovalInterruptRail,
)
from jiuwenclaw.agentserver.deep_agent.rails.project_memory import SECTION_NAME
from jiuwenclaw.agentserver.deep_agent.rails.project_memory_rail import (
    ProjectMemoryRail,
)
from jiuwenclaw.agentserver.diff_service import DiffService


def _memory_agent() -> MagicMock:
    builder = MagicMock()
    builder.language = "en"
    builder.added_sections = []
    builder.add_section.side_effect = lambda section: builder.added_sections.append(section)
    builder.remove_section.side_effect = lambda _name: None
    agent = MagicMock()
    agent.system_prompt_builder = builder
    return agent


@pytest.mark.asyncio
async def test_sr014_memory_plan_approval_write_and_turn_diff(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "JIUWENSWARM.md").write_text(
        "Use the repository coding conventions.", encoding="utf-8"
    )
    plan_path = tmp_path / "plan.md"
    plan_path.write_text("Update main.py", encoding="utf-8")
    session = SimpleNamespace(session_id="sr014")

    memory_agent = _memory_agent()
    memory_rail = ProjectMemoryRail(workspace=str(tmp_path), language="en")
    memory_rail.init(memory_agent)
    await memory_rail.before_model_call(
        SimpleNamespace(
            inputs=SimpleNamespace(tool_name="read_file"),
            extra={},
            session=session,
        )
    )
    section = next(
        item
        for item in memory_agent.system_prompt_builder.added_sections
        if item.name == SECTION_NAME
    )
    assert "repository coding conventions" in section.render("en")

    agent = MagicMock()
    agent.system_prompt_builder = SimpleNamespace(language="en")
    agent.load_state.return_value = SimpleNamespace(
        plan_mode=SimpleNamespace(mode="plan")
    )
    agent.get_plan_file_path.return_value = plan_path
    approval_rail = PlanApprovalInterruptRail()
    approval_rail.init(agent)
    approval_ctx = SimpleNamespace(agent=agent, session=session, extra={})
    exit_call = SimpleNamespace(name="exit_plan_mode", arguments="{}")

    pending = await approval_rail.resolve_interrupt(approval_ctx, exit_call, None)
    assert "Update main.py" in pending.request.message

    rejected = await approval_rail.resolve_interrupt(
        approval_ctx,
        exit_call,
        {"approved": False, "feedback": "Please revise"},
    )
    assert rejected.__class__.__name__ == "RejectResult"
    assert approval_ctx.extra["_plan_rejected"] is True

    mode_rail = CodeAgentModeRail()
    mode_rail._agent = agent
    blocked_ctx = SimpleNamespace(
        session=session,
        inputs=SimpleNamespace(
            tool_name="write_file",
            tool_call=SimpleNamespace(
                id="write-before-approval",
                arguments='{"path":"main.py","content":"new"}',
            ),
            tool_args={"path": "main.py", "content": "new"},
        ),
        extra={},
    )
    await mode_rail.before_tool_call(blocked_ctx)
    assert blocked_ctx.extra["_skip_tool"] is True

    approved_ctx = SimpleNamespace(
        agent=agent,
        session=session,
        extra={},
    )
    approved = await approval_rail.resolve_interrupt(
        approved_ctx,
        exit_call,
        {"approved": True},
    )
    assert approved.__class__.__name__ == "ApproveResult"
    assert approved_ctx.extra["_plan_approved"] is True

    agent.restore_mode_after_plan_exit = MagicMock()
    await mode_rail.after_tool_call(
        SimpleNamespace(
            session=session,
            inputs=SimpleNamespace(
                tool_name="exit_plan_mode",
                tool_result="approved",
            ),
            extra=approved_ctx.extra,
        )
    )
    agent.restore_mode_after_plan_exit.assert_called_once_with(session)

    target = tmp_path / "main.py"
    target.write_text("new\n", encoding="utf-8")
    session_dir = tmp_path / "sessions" / "sr014"
    session_dir.mkdir(parents=True)
    (session_dir / "history.json").write_text(
        json.dumps(
            [
                {"role": "user", "content": "implement", "timestamp": 100.0},
                {"role": "assistant", "event_type": "chat.final", "timestamp": 101.0},
            ]
        ),
        encoding="utf-8",
    )
    diff_service = DiffService()
    diff_service._read_agent_history = MagicMock(
        return_value={
            "main.py": [
                {
                    "timestamp": "1970-01-01T00:01:40+00:00",
                    "action": "write",
                    "old_content": "old\n",
                    "new_content": "new\n",
                }
            ]
        }
    )

    turns = diff_service.get_turn_diffs("sr014", sessions_root=tmp_path / "sessions")

    assert turns[0]["files"]["main.py"]["linesAdded"] == 1
    assert turns[0]["stats"]["filesChanged"] == 1
