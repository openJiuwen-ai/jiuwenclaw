# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Run the existing Code plan tool guard before Permission review."""

from __future__ import annotations

from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)

from jiuwenswarm.agents.harness.common.rails.permissions.permission_interrupt_rail import (
    mark_pre_permission_hard_rejection,
)

_PLAN_GUARD_CHECKED_KEY = "_jiuwenswarm_code_plan_guard_checked"
_PLAN_GUARD_CHECKED_SENTINEL = object()


def code_plan_guard_checked(ctx: AgentCallbackContext) -> bool:
    """Return whether the Host-owned pre-Permission guard ran for this call."""

    extra = getattr(ctx, "extra", None)
    return bool(
        isinstance(extra, dict)
        and extra.get(_PLAN_GUARD_CHECKED_KEY) is _PLAN_GUARD_CHECKED_SENTINEL
    )


class CodePlanPrePermissionGuardRail(DeepAgentRail):
    """Delegate Code plan enforcement before any Permission approval work."""

    priority = PermissionInterruptRail.priority + 1

    def __init__(self, agent_mode_rail: Any) -> None:
        super().__init__()
        self._agent_mode_rail = agent_mode_rail

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Run the existing guard once and mark only newly rejected calls."""

        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        was_skipped = extra.get("_skip_tool") is True
        await self._agent_mode_rail.before_tool_call(ctx)
        extra[_PLAN_GUARD_CHECKED_KEY] = _PLAN_GUARD_CHECKED_SENTINEL
        if not was_skipped and extra.get("_skip_tool") is True:
            mark_pre_permission_hard_rejection(ctx)


__all__ = ["CodePlanPrePermissionGuardRail", "code_plan_guard_checked"]
