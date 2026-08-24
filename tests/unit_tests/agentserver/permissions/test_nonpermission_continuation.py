"""Base permission rail respects Host-admitted non-Permission continuations."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)

from jiuwenswarm.agents.harness.common.rails.permissions.permission_interrupt_rail import (
    JiuwenSwarmPermissionInterruptRail,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue_rail import (
    ROOT_NON_PERMISSION_RESUME_ATTRIBUTE,
    RootNonPermissionResume,
)


def _marked_context() -> SimpleNamespace:
    ctx = SimpleNamespace(extra={})
    setattr(
        ctx,
        ROOT_NON_PERMISSION_RESUME_ATTRIBUTE,
        RootNonPermissionResume(
            "root-session",
            "call-1",
            "exit_plan_mode",
        ),
    )
    return ctx


@pytest.mark.asyncio
async def test_base_permission_does_not_consume_nonpermission_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = AsyncMock()
    monkeypatch.setattr(PermissionInterruptRail, "before_tool_call", called)
    rail = object.__new__(JiuwenSwarmPermissionInterruptRail)

    await rail.before_tool_call(_marked_context())

    called.assert_not_awaited()


@pytest.mark.asyncio
async def test_unmarked_initial_call_still_enters_base_permission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = AsyncMock()
    monkeypatch.setattr(PermissionInterruptRail, "before_tool_call", called)
    rail = object.__new__(JiuwenSwarmPermissionInterruptRail)
    ctx = SimpleNamespace(extra={})

    await rail.before_tool_call(ctx)

    called.assert_awaited_once_with(ctx)
