# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-session SkillUseRail: isolate skill tools in Runner.resource_mgr."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.harness.rails.skill_use_rail import SkillUseRail

from jiuwenclaw.agentserver.deep_agent.tool_qualify import (
    add_tool_to_resource_mgr,
    log_session_tool,
    qualify_tool_id,
    remove_tool_from_resource_mgr,
)

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent

logger = logging.getLogger(__name__)


class JiuWenSkillUseRail(SkillUseRail):
    """SkillUseRail with per-session qualified tool ids in Runner.resource_mgr."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._qualified_tool_ids: set[str] = set()

    @staticmethod
    def _resolve_agent_card_id(agent: "DeepAgent") -> str:
        return str(getattr(getattr(agent, "card", None), "id", None) or agent.card.name)

    def _collect_base_tool_ids(self) -> list[str]:
        """Collect base tool resource ids registered by super().init()."""
        owned_ids = getattr(self, "_owned_tool_ids", None)
        if not owned_ids:
            return []
        return [str(tool_id) for tool_id in sorted(owned_ids)]

    def init(self, agent: "DeepAgent") -> None:
        super().init(agent)
        agent_card_id = self._resolve_agent_card_id(agent)
        skill_names = [s.name for s in self.skills] if self.skills else []
        qualified_ids: set[str] = set()

        for old_id in self._collect_base_tool_ids():
            tool = Runner.resource_mgr.get_tool(old_id)
            if tool is None:
                logger.warning(
                    "[JiuWenSkillUseRail] tool missing from resource_mgr during qualify: %s",
                    old_id,
                )
                continue

            original_id = old_id
            remove_tool_from_resource_mgr(old_id)
            qualified_id = qualify_tool_id(old_id, agent_card_id)
            tool.card.id = qualified_id

            try:
                add_tool_to_resource_mgr(tool)
            except Exception as exc:
                tool.card.id = original_id
                try:
                    add_tool_to_resource_mgr(tool)
                except Exception as rollback_exc:
                    logger.error(
                        "[JiuWenSkillUseRail] rollback register failed for %s: %s",
                        original_id,
                        rollback_exc,
                    )
                logger.warning(
                    "[JiuWenSkillUseRail] failed to register qualified tool %s: %s",
                    qualified_id,
                    exc,
                )
                continue

            tool_name = str(getattr(tool.card, "name", "") or qualified_id)

            if hasattr(agent, "ability_manager"):
                try:
                    existing = agent.ability_manager.get(tool_name)
                    if isinstance(existing, ToolCard):
                        agent.ability_manager.remove(tool_name)
                    agent.ability_manager.add(tool.card)
                except Exception as exc:
                    logger.warning(
                        "[JiuWenSkillUseRail] ability_manager sync failed for %s: %s",
                        tool_name,
                        exc,
                    )

            qualified_ids.add(qualified_id)
            log_session_tool(
                agent_card_id,
                tool_name,
                qualified_id,
                event="registered",
                base_id=old_id,
            )

        self._qualified_tool_ids = qualified_ids
        logger.info(
            "[JiuWenSkillUseRail] qualified tools for agent_card_id=%s tool_ids=%s skills=%s",
            agent_card_id,
            sorted(qualified_ids),
            skill_names,
        )

    def uninit(self, agent: "DeepAgent") -> None:
        ability_manager = getattr(agent, "ability_manager", None)
        owned_names = getattr(self, "_owned_tool_names", None) or set()
        if ability_manager is not None:
            for tool_name in list(owned_names):
                card = ability_manager.get(tool_name)
                if isinstance(card, ToolCard) and str(card.id) in self._qualified_tool_ids:
                    try:
                        ability_manager.remove(tool_name)
                    except Exception as exc:
                        logger.warning(
                            "[JiuWenSkillUseRail] ability_manager cleanup failed for %s: %s",
                            tool_name,
                            exc,
                        )
        for tool_id in list(self._qualified_tool_ids):
            remove_tool_from_resource_mgr(tool_id)
        self._qualified_tool_ids.clear()
        super().uninit(agent)


__all__ = ["JiuWenSkillUseRail"]
