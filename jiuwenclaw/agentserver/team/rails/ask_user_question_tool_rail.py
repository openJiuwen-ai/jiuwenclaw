# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Register ``ask_user_question`` on the team leader."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.tools.ask_user_question_tool import get_ask_user_question_tool

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


class AskUserQuestionToolRail(DeepAgentRail):
    """Mount the ENT ``ask_user_question`` tool on a team member (leader only).

    Teammates intentionally omit this rail: team stream filtering drops
    teammate ask frames, and HITL must go through the leader channel.
    """

    priority = 94

    def __init__(self) -> None:
        super().__init__()
        self._tool = None

    def init(self, agent: "DeepAgent") -> None:
        if self._tool is not None:
            return

        tool = get_ask_user_question_tool()
        existing = agent.ability_manager.get(tool.card.name)
        if isinstance(existing, ToolCard):
            agent.ability_manager.remove(tool.card.name)

        if not Runner.resource_mgr.get_tool(tool.card.id):
            Runner.resource_mgr.add_tool(tool)
        agent.ability_manager.add(tool.card)
        self._tool = tool
        logger.info(
            "[AskUserQuestionToolRail] registered ask_user_question for agent_id=%s",
            getattr(getattr(agent, "card", None), "id", None),
        )

    def uninit(self, agent: "DeepAgent") -> None:
        if self._tool is None:
            return
        name = getattr(self._tool.card, "name", None)
        if name and hasattr(agent.ability_manager, "remove"):
            try:
                agent.ability_manager.remove(name)
            except Exception:
                logger.debug(
                    "[AskUserQuestionToolRail] remove ability failed name=%s",
                    name,
                    exc_info=True,
                )
        self._tool = None


__all__ = ["AskUserQuestionToolRail"]
