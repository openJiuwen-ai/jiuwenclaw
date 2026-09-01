# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Per-agent filtering for tools disabled by configuration.

Tools listed in ``react.disabled_tools`` are detached from the current
agent's ``ability_manager`` so they are neither advertised to the model nor
executable by that agent. Concrete tool instances stay in the process-global
``Runner.resource_mgr`` because stateless tools can be shared by many agents.
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class DisabledToolsRail(DeepAgentRail):
    """Rail that disables tools based on configuration.

    Detaches disabled cards from the current agent during init and before each
    model call. The repeated enforcement catches tools registered after rail
    initialization, including runtime MCP, Cron, and extension tools.

    Example config in config.yaml::

        react:
          disabled_tools: ["bash", "write_file", "mcp_exec_command"]

    The process-global resource manager is deliberately left untouched. Tool
    ownership and cleanup remain the responsibility of ``AbilityManager`` and
    ``jiuwenswarm.common.tool_ownership``.
    """

    priority: int = 1  # 低优先级，确保最后执行（工具已注册后再注销）

    def __init__(
        self,
        disabled_tools: list[str] | None = None,
    ) -> None:
        """Initialize DisabledToolsRail.

        Args:
            disabled_tools: List of tool names to disable. Can be None/empty.
        """
        super().__init__()
        self._disabled_tools: set[str] = set(disabled_tools or [])
        self._agent: Any | None = None
        # Keep the latest detached card so hot-reload can re-enable it.
        self._removed_cards: dict[str, ToolCard] = {}

    def init(self, agent: Any) -> None:
        """Initialize rail and unregister disabled tools."""
        super().init(agent)
        self._agent = agent

        logger.info(
            "[DisabledToolsRail] initialized with disabled_tools: %s",
            list(self._disabled_tools),
        )

        self._unregister_tools(self._disabled_tools)

    def uninit(self, agent: Any) -> None:
        """Restore cards detached by this rail when it is removed."""
        if self._agent:
            self._register_tools(set(self._removed_cards))
        self._agent = None
        self._removed_cards.clear()

    def _unregister_tools(self, tool_names: set[str]) -> None:
        """Detach matching abilities from only the current agent.

        Args:
            tool_names: Set of tool names to unregister.
        """
        if not self._agent:
            logger.warning("[DisabledToolsRail] _unregister_tools: no agent reference")
            return

        for tool_name in tool_names:
            tool_card = self._agent.ability_manager.get(tool_name)
            if not tool_card:
                logger.debug(
                    "[DisabledToolsRail] tool '%s' not currently visible", tool_name
                )
                continue
            self._agent.ability_manager.remove(tool_name)
            self._removed_cards[tool_name] = tool_card
            logger.info(
                "[DisabledToolsRail] detached tool from agent: name=%s id=%s",
                tool_name,
                getattr(tool_card, "id", None),
            )

    def _register_tools(self, tool_names: set[str]) -> None:
        """Restore previously detached cards to the current agent.

        Args:
            tool_names: Set of tool names to re-register.
        """
        if not self._agent:
            logger.warning("[DisabledToolsRail] _register_tools: no agent reference")
            return

        for tool_name in tool_names:
            tool_card = self._removed_cards.pop(tool_name, None)
            if not tool_card:
                logger.debug(
                    "[DisabledToolsRail] no detached card for tool '%s'", tool_name
                )
                continue
            self._agent.ability_manager.add(tool_card)
            logger.info(
                "[DisabledToolsRail] re-registered ToolCard: name=%s", tool_name
            )

    async def before_model_call(self, ctx: Any) -> None:
        """Enforce the blacklist after late tool registration.

        ``ReactAgent`` prepares ``ctx.inputs.tools`` before this hook. Filtering
        that snapshot is therefore required in addition to detaching cards
        from ``ability_manager``; the latter also prevents execution of a name
        remembered or hallucinated by the model.
        """
        self._unregister_tools(self._disabled_tools)
        inputs = getattr(ctx, "inputs", None)
        tool_infos = getattr(inputs, "tools", None)
        if isinstance(tool_infos, list):
            inputs.tools = [
                tool_info
                for tool_info in tool_infos
                if getattr(tool_info, "name", None) not in self._disabled_tools
            ]

    def update_config(self, disabled_tools: list[str] | None) -> None:
        """Update disabled tools configuration with hot-reload support.

        Computes diff between old and new ``disabled_tools``, then:
        - Unregisters newly disabled tools
        - Re-registers newly enabled tools

        Args:
            disabled_tools: New list of tool names to disable.
        """
        new_set = set(disabled_tools or [])
        old_set = self._disabled_tools

        to_disable = new_set - old_set
        to_enable = old_set - new_set

        logger.info(
            "[DisabledToolsRail] update_config: old=%s, new=%s, to_disable=%s, "
            "to_enable=%s",
            list(old_set), list(new_set), list(to_disable), list(to_enable),
        )

        if to_disable:
            self._unregister_tools(to_disable)

        if to_enable:
            self._register_tools(to_enable)

        self._disabled_tools = new_set
