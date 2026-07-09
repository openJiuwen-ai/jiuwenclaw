# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register member-scoped skill-management tools for team mode."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenavatar.server.runtime.skill.skill_manager import SkillManager
from jiuwenavatar.agents.harness.common.tools.skill_toolkits import SkillToolkit

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


class MemberSkillToolkitRail(DeepAgentRail):
    """Bind skill-management tools to a team member workspace."""

    priority = 95

    def __init__(
        self,
        workspace_dir: str,
        *,
        manager: Any | None = None,
        refresh_links: Callable[[dict[str, object]], None] | None = None,
    ) -> None:
        super().__init__()
        self._workspace_dir = workspace_dir
        self._manager = manager
        self._refresh_links = refresh_links
        self._tools = None

    def init(self, agent: "DeepAgent") -> None:
        """Register member-scoped skill tools on the agent."""
        if self._tools is not None:
            return

        agent_id = str(agent.card.id or agent.card.name)
        if self._manager is None:
            self._manager = SkillManager(workspace_dir=self._workspace_dir)
        toolkit = SkillToolkit(manager=self._manager)
        tools = self._wrap_skill_tools(toolkit.get_tools())

        for tool in tools:
            tool.card.id = self._qualify_tool_id(tool.card.id, agent_id)

        for tool in tools:
            existing = agent.ability_manager.get(tool.card.name)
            if isinstance(existing, ToolCard):
                agent.ability_manager.remove(tool.card.name)

        Runner.resource_mgr.add_tool(list(tools))
        for tool in tools:
            agent.ability_manager.add(tool.card)

        self._tools = tools
        logger.info(
            "[MemberSkillToolkitRail] Registered %d skill tools for workspace=%s agent_id=%s",
            len(tools),
            self._workspace_dir,
            agent_id,
        )

    def uninit(self, agent: "DeepAgent") -> None:
        """Remove member-scoped skill tools from the agent."""
        if not self._tools:
            return

        for tool in self._tools:
            agent.ability_manager.remove(tool.card.name)
            Runner.resource_mgr.remove_tool(tool.card.id)

        logger.info(
            "[MemberSkillToolkitRail] Unregistered %d skill tools for workspace=%s",
            len(self._tools),
            self._workspace_dir,
        )
        self._tools = None

    @staticmethod
    def _qualify_tool_id(tool_id: str, agent_id: str) -> str:
        return f"{tool_id}_{agent_id}"

    def _wrap_skill_tools(self, tools: list[Tool]) -> list[Tool]:
        """Refresh linked skill views after mutating skill operations."""
        if self._refresh_links is None:
            return tools

        wrapped: list[Tool] = []
        for tool in tools:
            if tool.card.name not in {"install_skill", "uninstall_skill"}:
                wrapped.append(tool)
                continue

            async def refresh_after_call(_tool: Tool = tool, **kwargs):
                result = await _tool.invoke(kwargs, skip_inputs_validate=True)
                if isinstance(result, dict) and result.get("success"):
                    self._refresh_links(result)
                return result

            wrapped.append(LocalFunction(card=tool.card, func=refresh_after_call))
        return wrapped


__all__ = ["MemberSkillToolkitRail"]
