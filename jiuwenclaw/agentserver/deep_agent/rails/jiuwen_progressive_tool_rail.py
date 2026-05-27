# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuWen progressive tool rail adapter.

This adapter reuses openjiuwen's ProgressiveToolRail and only changes the meta
tool contract from ``search_tools + load_tools`` to ``search_and_load_tools``.
MCP servers still follow the existing eager registration/connection flow.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.progressive_tool_rail import ProgressiveToolRail

from jiuwenclaw.agentserver.tools.search_and_load_tools import (
    SearchAndLoadToolsInput,
    SearchAndLoadToolsTool,
)

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[ProgressiveTool]"


class JiuWenProgressiveToolRail(ProgressiveToolRail):
    """ProgressiveToolRail variant with a single search-and-load meta tool."""

    def __init__(
        self,
        *,
        enabled: bool = True,
        always_visible_tools: list[str] | None = None,
        default_visible_tools: list[str] | None = None,
        max_loaded_tools: int = 12,
        search_max_results: int = 5,
        default_load_limit: int = 3,
        language: str = "cn",
        debug_context: dict[str, Any] | None = None,
    ) -> None:
        config = SimpleNamespace(
            language=language or "cn",
            progressive_tool_default_visible_tools=default_visible_tools or [],
            progressive_tool_always_visible_tools=always_visible_tools or [],
            progressive_tool_max_loaded_tools=max(1, int(max_loaded_tools or 12)),
        )
        super().__init__(config)
        self.enabled = bool(enabled)
        self.search_max_results = max(1, int(search_max_results or 5))
        self.default_load_limit = max(1, int(default_load_limit or 3))
        self._active_for_invoke = False
        self._debug_context = dict(debug_context or {})

    def _log_ctx_suffix(self) -> str:
        if not self._debug_context:
            return ""
        parts = [f"{key}={value}" for key, value in self._debug_context.items() if value is not None]
        return f" ({', '.join(parts)})" if parts else ""

    def init(self, agent) -> None:
        """Register only ``search_and_load_tools`` instead of parent dual tools."""
        language = getattr(self._config, "language", "cn") or "cn"
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        tool = SearchAndLoadToolsTool(
            self._search_and_load_tools,
            language=language,
            agent_id=agent_id,
        )
        self._meta_tool_names = {tool.card.name}

        try:
            existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
            if existing_tool is None:
                Runner.resource_mgr.add_tool(tool)
                self._owned_tool_ids.add(tool.card.id)
        except Exception as exc:
            logger.warning(
                "%s failed to add meta tool resource %s: %s",
                _LOG_PREFIX,
                tool.card.id,
                exc,
            )

        if hasattr(agent, "ability_manager"):
            try:
                result = agent.ability_manager.add(tool.card)
                if getattr(result, "added", False):
                    self._owned_tool_names.add(tool.card.name)
            except Exception as exc:
                logger.warning(
                    "%s failed to add meta tool card %s: %s",
                    _LOG_PREFIX,
                    tool.card.name,
                    exc,
                )

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled:
            self._active_for_invoke = False
            return
        await super().before_invoke(ctx)
        self._active_for_invoke = self._should_filter_tools()
        if not self._active_for_invoke:
            return
        session = getattr(ctx, "session", None)
        visible = self._get_visible_tools(session)
        logger.info(
            "%s invoke registered=%s visible=%s always=%s%s",
            _LOG_PREFIX,
            len(self._cached_all_tool_infos),
            len(visible),
            len(self.always_visible_tools),
            self._log_ctx_suffix(),
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        if not self.enabled or not self._active_for_invoke:
            return
        inputs = getattr(ctx, "inputs", None)
        before_tools = getattr(inputs, "tools", None)
        before_names = [
            str(getattr(tool, "name", "") or "")
            for tool in before_tools
            if str(getattr(tool, "name", "") or "")
        ] if isinstance(before_tools, list) else []
        await super().before_model_call(ctx)
        after_tools = getattr(inputs, "tools", None)
        after_names = [
            str(getattr(tool, "name", "") or "")
            for tool in after_tools
            if str(getattr(tool, "name", "") or "")
        ] if isinstance(after_tools, list) else []
        removed = sorted(set(before_names) - set(after_names))
        logger.info(
            "%s filter tools %s -> %s removed=%s visible=%s%s",
            _LOG_PREFIX,
            len(before_names),
            len(after_names),
            removed,
            self._get_visible_tools(getattr(ctx, "session", None)),
            self._log_ctx_suffix(),
        )

    async def _search_and_load_tools(
        self,
        session: Any,
        params: SearchAndLoadToolsInput,
    ) -> dict[str, Any]:
        """Search registered tools and mark top matches visible for this session."""
        query = params.query
        source = params.source
        detail_level = params.detail_level
        replace = params.replace
        if not self.enabled:
            return {
                "success": False,
                "query": query,
                "source": source,
                "matches": [],
                "loaded_tools": [],
                "visible_tools": [],
                "skipped_tools": [],
                "message": "progressive tool loading is disabled",
            }

        effective_limit = max(
            1,
            min(int(params.limit or self.default_load_limit), self.search_max_results),
        )
        matches = await super()._search_tools(
            query=query,
            limit=effective_limit,
            detail_level=detail_level,
        )
        loaded_names = [str(item.get("name", "") or "") for item in matches]
        load_result = await super()._load_tools(
            session=session,
            tool_names=loaded_names,
            replace=replace,
        )
        logger.info(
            "%s search_and_load query=%r matched=%s loaded=%s skipped=%s visible=%s%s",
            _LOG_PREFIX,
            query,
            loaded_names,
            load_result.get("loaded_tools", []),
            load_result.get("skipped_tools", []),
            load_result.get("visible_tools", []),
            self._log_ctx_suffix(),
        )
        self._append_trace(
            session,
            {
                "action": "search_and_load_tools",
                "query": query,
                "source": source,
                "limit": effective_limit,
                "detail_level": detail_level,
                "matches": loaded_names,
                "loaded": load_result.get("loaded_tools", []),
                "skipped": load_result.get("skipped_tools", []),
                "replace": replace,
            },
        )

        loaded_tools = load_result.get("loaded_tools", [])
        success = bool(loaded_tools)
        return {
            "success": success,
            "query": query,
            "source": source,
            "matches": matches,
            "count": len(matches),
            **load_result,
            "message": (
                f"Loaded {len(loaded_tools)} tool(s). "
                "They will be callable in the next model step."
                if success
                else "No matching registered tools were loaded. Try broader keywords or use visible tools."
            ),
        }

    def _build_progressive_tool_rules_section(self):
        """Replace parent dual-tool prompt with single-tool instructions."""
        return PromptSection(
            name=SectionName.PROGRESSIVE_TOOL_RULES,
            content={
                "cn": (
                    "## 渐进式工具使用规则\n\n"
                    "- 你当前只能调用 tools 列表中可见的工具。\n"
                    "- 如果需要当前不可见的能力，先调用 `search_and_load_tools` 搜索并加载工具。\n"
                    "- `search_and_load_tools` 返回已加载工具后，下一轮模型调用应直接调用真实工具。"
                ),
                "en": (
                    "## Progressive Tool Usage Rules\n\n"
                    "- You can only call tools currently visible in the tools list.\n"
                    "- If you need a hidden capability, call `search_and_load_tools` first.\n"
                    "- After it reports loaded tools, call the real tool in the next model step."
                ),
            },
            priority=75,
        )

    async def _build_navigation_section(self, session: Any):
        """Build navigation prompt that points to search_and_load_tools."""
        entries_cn = await self._build_navigation_entries(session, language="cn")
        entries_en = await self._build_navigation_entries(session, language="en")
        return PromptSection(
            name=SectionName.TOOL_NAVIGATION,
            content={
                "cn": self._build_navigation_prompt(entries_cn, language="cn"),
                "en": self._build_navigation_prompt(entries_en, language="en"),
            },
            priority=70,
        )

    async def _build_navigation_entries(
        self,
        session: Any,
        language: str = "cn",
    ) -> list[str]:
        """Render compact entries for all registered real tools."""
        all_tools = await self._get_real_tool_infos()
        loaded = set(self._get_visible_tools(session))
        sorted_tools = sorted(
            all_tools,
            key=lambda t: (
                self._tool_group_rank(t),
                str(getattr(t, "name", "") or ""),
            ),
        )

        entries: list[str] = []
        for tool in sorted_tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            group = self._tool_group_for_navigation(tool)
            group_label = self._tool_group_to_cn(group) if language == "cn" else group
            callable_now = name in loaded or name in self.always_visible_tools
            status = (
                ("可调用" if callable_now else "仅导航")
                if language == "cn"
                else ("callable" if callable_now else "navigation-only")
            )
            punctuation = "：" if language == "cn" else ":"
            entries.append(
                f"- {name} [{group_label}, {status}]{punctuation} "
                f"{self._tool_summary_for_navigation(tool)}"
            )

        return entries

    @staticmethod
    def _build_navigation_prompt(entries: list[str], *, language: str) -> str:
        items = [item for item in entries if item]
        if language == "en":
            header = (
                "## Tool Navigation\n"
                "The entries below help you understand tools registered for this session.\n"
                "This section is a navigation map, not the complete callable tools list.\n"
                "Only tools already visible in the model tools list are callable now.\n"
                "For a navigation-only or hidden capability, call `search_and_load_tools`; "
                "matched tools become callable in the next model step.\n"
            )
            empty = "- (no navigation entries available)"
        else:
            header = (
                "## 工具导航\n"
                "以下条目用于帮助你理解当前 session 下已注册的工具生态。\n"
                "请注意：这里展示的是“工具地图”，不是“全部可立即调用的工具清单”。\n"
                "只有已经出现在当前模型 tools 列表中的工具，才可以直接调用。\n"
                "如果需要“仅导航”或当前不可见的能力，请调用 `search_and_load_tools`；"
                "命中的工具会在下一轮模型调用中变为可调用。\n"
            )
            empty = "- （当前无可展示的导航条目）"
        return header + "\n" + ("\n".join(items) if items else empty)

    def _should_filter_tools(self) -> bool:
        return self.enabled
