# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""JiuWen progressive tool rail - fixed tools schema with deferred tool access.

- eager_tools: always visible in the model tools schema
- deferred_tools: registered at runtime but hidden from schema; accessed via
  tools_search + invoke_tool

Fixed schema maximizes LLM prefix caching while keeping rarely used tools reachable.
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenclaw.agentserver.tools.tools_search import (
    ToolsSearchInput,
    ToolsSearchTool,
)
from jiuwenclaw.agentserver.tools.invoke_tool import (
    InvokeToolInput,
    InvokeToolTool,
)

logger = logging.getLogger(__name__)

_LOG_PREFIX = "[ProgressiveToolRail]"


def _json_safe_value(value: Any) -> Any:
    """Convert tool invoke results (e.g. ToolOutput) to JSON-serializable values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _json_safe_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(v) for v in value]
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_safe_value(model_dump(mode="json"))
        except TypeError:
            return _json_safe_value(model_dump())
    return str(value)


class JiuWenProgressiveToolRail(DeepAgentRail):
    """Progressive tool visibility with a fixed eager-tools schema."""

    priority = 80

    def __init__(
        self,
        *,
        enabled: bool = True,
        eager_tools: list[str] | None = None,
        language: str = "cn",
        agent_id: str | None = None,
        enable_for_models: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.enabled = bool(enabled)
        self.eager_tools = list(eager_tools or [])
        self.language = language or "cn"
        self.agent_id = agent_id
        self._enable_for_models = [
            str(item).strip().lower()
            for item in (enable_for_models or [])
            if str(item).strip()
        ]
        self._cached_model_name = ""

        if "tools_search" not in self.eager_tools:
            self.eager_tools.insert(0, "tools_search")
        if "invoke_tool" not in self.eager_tools:
            self.eager_tools.insert(1, "invoke_tool")

        self._deep_agent: Any = None
        self._meta_tools: list[Any] | None = None
        self._meta_active = False
        self._owned_tool_ids: set[str] = set()
        self._cached_all_tool_infos: list[Any] = []
        self._cached_deferred_tool_infos: list[Any] = []

    @staticmethod
    def _resolve_model_name_from_ctx(ctx: AgentCallbackContext | None) -> str:
        """Read the effective model name for the current invoke."""
        if ctx is None:
            return ""
        agent = getattr(ctx, "agent", None)
        if agent is None:
            return ""

        direct_name = str(getattr(agent, "model_name", "") or "").strip()
        if direct_name:
            return direct_name

        config = getattr(agent, "_config", None)
        if config is not None:
            name = str(getattr(config, "model_name", "") or "").strip()
            if name:
                return name

        react_agent = getattr(agent, "_react_agent", None) or getattr(agent, "react_agent", None)
        if react_agent is not None:
            react_config = getattr(react_agent, "_config", None)
            if react_config is not None:
                name = str(getattr(react_config, "model_name", "") or "").strip()
                if name:
                    return name

        deep_config = getattr(agent, "deep_config", None)
        if deep_config is not None:
            model = getattr(deep_config, "model", None)
            model_config = getattr(model, "model_config", None) if model is not None else None
            name = str(getattr(model_config, "model_name", "") or "").strip()
            if name:
                return name
        return ""

    def _resolve_model_name_for_ctx(self, ctx: AgentCallbackContext | None) -> str:
        """Resolve model name from ctx, falling back to cached invoke-time name."""
        name = self._resolve_model_name_from_ctx(ctx)
        if name:
            self._cached_model_name = name
            return name
        return self._cached_model_name

    def _is_lazy_load_enabled_for_model(self, model_name: str) -> bool:
        """Empty whitelist = all models; otherwise substring match on model_name."""
        if not self._enable_for_models:
            return True
        if not model_name:
            return False
        lowered = model_name.lower()
        return any(pattern in lowered for pattern in self._enable_for_models)

    def _lazy_load_active_for_ctx(self, ctx: AgentCallbackContext | None) -> bool:
        if not self.enabled:
            return False
        model_name = self._resolve_model_name_for_ctx(ctx)
        if not self._is_lazy_load_enabled_for_model(model_name):
            logger.info(
                "%s lazy load bypassed for model=%s enable_for_models=%s",
                _LOG_PREFIX,
                model_name,
                self._enable_for_models,
            )
            return False
        return True

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    def uninit(self, agent: Any) -> None:
        self._set_meta_tools_active(False)
        self._deep_agent = None
        self._meta_tools = None

    def _meta_tool_instances(self) -> list[Any]:
        if self._meta_tools is None:
            self._meta_tools = [
                ToolsSearchTool(
                    self._search_tools,
                    language=self.language,
                    agent_id=self.agent_id,
                ),
                InvokeToolTool(
                    self._invoke_target_tool,
                    language=self.language,
                    agent_id=self.agent_id,
                ),
            ]
        return self._meta_tools

    def _set_meta_tools_active(self, active: bool) -> None:
        if active == self._meta_active or self._deep_agent is None:
            return

        self._meta_active = active
        ability_manager = getattr(self._deep_agent, "ability_manager", None)

        for tool in self._meta_tool_instances():
            if active:
                try:
                    if Runner.resource_mgr.get_tool(tool.card.id) is None:
                        Runner.resource_mgr.add_tool(tool)
                        self._owned_tool_ids.add(tool.card.id)
                except Exception as exc:
                    logger.warning(
                        "%s failed to add meta tool resource %s: %s",
                        _LOG_PREFIX,
                        tool.card.id,
                        exc,
                    )
                if ability_manager is not None:
                    try:
                        ability_manager.add(tool.card)
                    except Exception as exc:
                        logger.warning(
                            "%s failed to add meta tool card %s: %s",
                            _LOG_PREFIX,
                            tool.card.name,
                            exc,
                        )
                continue

            if ability_manager is not None:
                try:
                    ability_manager.remove(tool.card.name)
                except Exception as exc:
                    logger.warning(
                        "%s failed to remove meta tool card %s: %s",
                        _LOG_PREFIX,
                        tool.card.name,
                        exc,
                    )
            if tool.card.id not in self._owned_tool_ids:
                continue
            try:
                Runner.resource_mgr.remove_tool(tool.card.id)
            except Exception as exc:
                logger.warning(
                    "%s failed to remove meta tool resource %s: %s",
                    _LOG_PREFIX,
                    tool.card.id,
                    exc,
                )
            self._owned_tool_ids.discard(tool.card.id)

        if not active:
            self._cached_all_tool_infos = []
            self._cached_deferred_tool_infos = []

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Register meta tools when active; cache deferred tools for navigation."""
        active = self._lazy_load_active_for_ctx(ctx)
        self._set_meta_tools_active(active)
        if not active:
            return

        all_tools = await self._get_all_tool_infos()
        self._cached_all_tool_infos = all_tools

        self._cached_deferred_tool_infos = [
            tool for tool in all_tools
            if str(getattr(tool, "name", "") or "") not in self.eager_tools
        ]

        logger.info(
            "%s invoke total=%s eager=%s deferred=%s",
            _LOG_PREFIX,
            len(all_tools),
            len(self.eager_tools),
            len(self._cached_deferred_tool_infos),
        )

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Filter tools to only include eager_tools."""
        if not self._lazy_load_active_for_ctx(ctx):
            return

        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return

        tools = getattr(inputs, "tools", None)
        if not isinstance(tools, list):
            return

        original_count = len(tools)
        filtered_tools = [
            tool for tool in tools
            if str(getattr(tool, "name", "") or "") in self.eager_tools
        ]
        inputs.tools = filtered_tools

        removed = sorted(set(
            str(getattr(tool, "name", "") or "")
            for tool in tools
        ) - set(
            str(getattr(tool, "name", "") or "")
            for tool in filtered_tools
        ))

        logger.info(
            "%s filter tools %s -> %s removed=%s",
            _LOG_PREFIX,
            original_count,
            len(filtered_tools),
            removed,
        )

        await self._add_navigation_section(ctx)

    async def _add_navigation_section(self, ctx: AgentCallbackContext) -> None:
        """Add tool navigation prompt section."""
        if ctx.agent is None:
            return

        navigation_section = await self._build_navigation_section()
        if navigation_section is None:
            return

        spb = getattr(ctx.agent, "system_prompt_builder", None)
        if spb is not None and hasattr(spb, "add_section"):
            spb.add_section(navigation_section)

    async def _build_navigation_section(self) -> PromptSection | None:
        """Build navigation prompt section for deferred tools."""
        if not self._cached_deferred_tool_infos:
            return None

        entries_cn = await self._build_navigation_entries(language="cn")
        entries_en = await self._build_navigation_entries(language="en")

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
        language: str = "cn",
    ) -> list[str]:
        """Build navigation entries for deferred tools."""
        sorted_tools = sorted(
            self._cached_deferred_tool_infos,
            key=lambda t: str(getattr(t, "name", "") or ""),
        )

        entries: list[str] = []
        punctuation = "：" if language == "cn" else ":"

        for tool in sorted_tools:
            name = str(getattr(tool, "name", "") or "")
            if not name:
                continue
            description = str(getattr(tool, "description", "") or "")
            brief_desc = description[:160] if len(description) > 160 else description
            entries.append(f"- {name}{punctuation} {brief_desc}")

        return entries

    @staticmethod
    def _build_navigation_prompt(
        entries: list[str],
        *,
        language: str,
    ) -> str:
        """Build navigation prompt text."""
        items = [item for item in entries if item]

        if language == "en":
            header = (
                "## Deferred Tool Navigation (Not in tools list)\n\n"
                "**IMPORTANT: The tools listed below are NOT in your tools list. "
                "You CANNOT call them directly.**\n\n"
                "To use any tool from this list:\n"
                "1. **First** call `tools_search` with `tool_name` (exact registered name) "
                "to get its complete schema.\n"
                "2. **Then** call `invoke_tool` with exact `tool_name` and `arguments`.\n\n"
                "**Do NOT attempt to call these tools directly - they will fail.**\n\n"
            )
            empty = "- (no deferred tools available)"
        else:
            header = (
                "## 按需可见工具导航（不可直接调用）\n\n"
                "**重要提示：以下工具不在当前 tools 列表中，无法直接调用。**\n\n"
                "使用方法：\n"
                "1. **必须先**调用 `tools_search`，传入与导航列表一致的 `tool_name`，获取完整参数 schema。\n"
                "2. **然后**调用 `invoke_tool`，传入精确 `tool_name` 和根据 schema 构造的 `arguments`。\n\n"
                "**切勿直接调用以下工具——直接调用会失败。**\n\n"
            )
            empty = "- （当前无按需可见工具）"

        return header + ("\n".join(items) if items else empty)

    async def _get_all_tool_infos(self) -> list[Any]:
        """Get all registered tool infos from ability_manager."""
        if self._deep_agent is None:
            return []

        ability_manager = getattr(self._deep_agent, "ability_manager", None)
        if ability_manager is None:
            return []

        try:
            return list(ability_manager.list())
        except Exception:
            return []

    async def _search_tools(
        self,
        session: Any,
        params: ToolsSearchInput,
    ) -> dict[str, Any]:
        """Look up a deferred tool by registered name and return its schema."""
        tool_name = str(params.tool_name or "").strip()
        tool_name_key = tool_name.lower()

        if not tool_name:
            return {
                "success": False,
                "matches": [],
                "message": "tool_name is required",
            }

        matches = []
        for tool in self._cached_deferred_tool_infos:
            name = str(getattr(tool, "name", "") or "")
            if name.lower() == tool_name_key:
                matches.append(tool)

        result_matches = []
        for tool in matches:
            name = str(getattr(tool, "name", "") or "")
            description = str(getattr(tool, "description", "") or "")
            input_params = getattr(tool, "input_params", {}) or {}

            result_matches.append({
                "name": name,
                "description": description,
                "input_schema": input_params,
            })

        logger.info(
            "%s search tool_name=%s matches=%s",
            _LOG_PREFIX,
            tool_name,
            len(result_matches),
        )

        return {
            "success": bool(result_matches),
            "matches": result_matches,
            "count": len(result_matches),
            "message": (
                f"已找到工具 '{tool_name}'，请根据 input_schema 构造 arguments 后调用 invoke_tool。"
                if result_matches
                else f"未找到名为 '{tool_name}' 的按需可见工具，请检查名称是否与导航列表一致。"
            ),
        }

    async def _invoke_target_tool(
        self,
        session: Any,
        params: InvokeToolInput,
        **kwargs,
    ) -> dict[str, Any]:
        """Invoke a deferred tool with validated arguments."""
        tool_name = str(params.tool_name or "").strip()
        arguments = dict(params.arguments or {})

        if not tool_name:
            return {
                "success": False,
                "error": "tool_name is required",
                "tool_name": "",
            }

        if tool_name in self.eager_tools:
            return {
                "success": False,
                "error": f"工具 '{tool_name}' 是常驻可见工具，可直接调用，无需通过 invoke_tool。",
                "tool_name": tool_name,
            }

        target_tool_card = None
        for tool in self._cached_deferred_tool_infos:
            if str(getattr(tool, "name", "") or "") == tool_name:
                target_tool_card = tool
                break

        if target_tool_card is None:
            return {
                "success": False,
                "error": f"工具 '{tool_name}' 未注册或不在按需可见工具列表中。",
                "tool_name": tool_name,
            }

        target_tool_id = str(getattr(target_tool_card, "id", "") or "")
        target_tool = None
        try:
            target_tool = Runner.resource_mgr.get_tool(target_tool_id)
        except Exception as e:
            logger.warning("%s failed to get tool instance: tool_id=%s error=%s", _LOG_PREFIX, target_tool_id, e)

        if target_tool is None:
            return {
                "success": False,
                "error": f"无法获取工具 '{tool_name}' 的实例。",
                "tool_name": tool_name,
            }

        try:
            kwargs_without_session = {k: v for k, v in kwargs.items() if k != "session"}
            result = await target_tool.invoke(arguments, session=session, **kwargs_without_session)
            logger.info(
                "%s invoke tool=%s success=True result_type=%s",
                _LOG_PREFIX,
                tool_name,
                type(result).__name__,
            )
            return {
                "success": True,
                "tool_name": tool_name,
                "result": _json_safe_value(result),
            }
        except Exception as exc:
            logger.warning(
                "%s invoke tool=%s failed: %s",
                _LOG_PREFIX,
                tool_name,
                exc,
            )
            return {
                "success": False,
                "error": str(exc),
                "tool_name": tool_name,
            }
