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
from jiuwenclaw.agentserver.deep_agent.tool_qualify import (
    add_tool_to_resource_mgr,
    remove_tool_from_resource_mgr,
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
        agent_card_id: str | None = None,
        enable_for_models: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.enabled = bool(enabled)
        self.eager_tools = list(eager_tools or [])
        self.language = language or "cn"
        self.agent_id = agent_id
        self.agent_card_id = str(agent_card_id or "").strip() or None
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
        self._runtime_agent: Any = None
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

    def update_agent_card_id(self, card_id: str | None) -> None:
        """Update session card id and invalidate cached meta tools when it changes."""
        resolved = str(card_id or "").strip() or None
        if resolved and resolved != self.agent_card_id:
            self.agent_card_id = resolved
            self._meta_tools = None
            self._meta_active = False

    def init(self, agent: Any) -> None:
        card = getattr(agent, "card", None)
        resolved_card_id = str(getattr(card, "id", "") or "").strip() or None
        self.update_agent_card_id(resolved_card_id)
        self._deep_agent = agent
        self._runtime_agent = agent
        self.invalidate_deferred_tool_cache()

    def _meta_tool_scope_id(self) -> str | None:
        """Session/subagent scope for meta tool resource ids."""
        return self.agent_card_id or (
            str(self.agent_id or "").strip() or None
        )

    @staticmethod
    def _legacy_meta_tool_ids(tool: Any, tenant_agent_id: str | None) -> set[str]:
        """Unqualified or tenant-scoped ids that must not shadow session tools."""
        base = str(getattr(type(tool), "TOOL_ID", "") or "").strip()
        if not base:
            return set()
        legacy = {base}
        if tenant_agent_id:
            legacy.add(f"{base}_{tenant_agent_id}")
        return legacy

    def uninit(self, agent: Any) -> None:
        self._set_meta_tools_active(False)
        self._deep_agent = None
        self._runtime_agent = None
        self._meta_tools = None

    def invalidate_deferred_tool_cache(self) -> None:
        """Clear deferred tool caches (after agent rebind or configure reload)."""
        self._cached_all_tool_infos = []
        self._cached_deferred_tool_infos = []

    def _resolve_runtime_agent(
        self,
        ctx: AgentCallbackContext | None = None,
    ) -> Any:
        """Resolve the agent whose ability_manager is authoritative for this invoke."""
        agent = None
        if ctx is not None:
            agent = getattr(ctx, "agent", None)
        if agent is None:
            agent = self._runtime_agent
        if agent is None:
            agent = self._deep_agent
        if agent is not None and agent is not self._deep_agent:
            logger.debug(
                "%s runtime agent swap old_id=%s new_id=%s",
                _LOG_PREFIX,
                id(self._deep_agent),
                id(agent),
            )
            self._deep_agent = agent
            self.invalidate_deferred_tool_cache()
        return agent

    def _meta_tool_instances(self) -> list[Any]:
        if self._meta_tools is None:
            scope_id = self._meta_tool_scope_id()
            self._meta_tools = [
                ToolsSearchTool(
                    self._search_tools,
                    language=self.language,
                    agent_card_id=scope_id,
                ),
                InvokeToolTool(
                    self._invoke_target_tool,
                    language=self.language,
                    agent_card_id=scope_id,
                ),
            ]
        return self._meta_tools

    def _register_meta_tool_in_resource_mgr(self, tool: Any) -> None:
        """Register or replace session-scoped meta tool in resource_mgr."""
        qualified_id = str(tool.card.id)
        for stale_id in self._legacy_meta_tool_ids(tool, self.agent_id):
            if stale_id != qualified_id:
                remove_tool_from_resource_mgr(stale_id)
        remove_tool_from_resource_mgr(qualified_id)
        add_tool_to_resource_mgr(tool)
        self._owned_tool_ids.add(qualified_id)

    def _set_meta_tools_active(self, active: bool) -> None:
        if active == self._meta_active or self._deep_agent is None:
            return

        self._meta_active = active
        ability_manager = getattr(self._deep_agent, "ability_manager", None)

        for tool in self._meta_tool_instances():
            if active:
                try:
                    self._register_meta_tool_in_resource_mgr(tool)
                except Exception as exc:
                    logger.warning(
                        "%s failed to add meta tool resource %s: %s",
                        _LOG_PREFIX,
                        tool.card.id,
                        exc,
                    )
                if ability_manager is not None:
                    try:
                        existing = ability_manager.get(tool.card.name)
                        if existing is not None:
                            ability_manager.remove(tool.card.name)
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
            self.invalidate_deferred_tool_cache()

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Register meta tools when active; cache deferred tools for navigation."""
        if getattr(ctx, "agent", None) is not None:
            self._runtime_agent = ctx.agent
        self._resolve_runtime_agent(ctx)
        active = self._lazy_load_active_for_ctx(ctx)
        self._set_meta_tools_active(active)
        if not active:
            return

        await self._refresh_deferred_tool_cache()

        logger.info(
            "%s invoke total=%s eager=%s deferred=%s",
            _LOG_PREFIX,
            len(self._cached_all_tool_infos),
            len(self.eager_tools),
            len(self._cached_deferred_tool_infos),
        )

    async def _refresh_deferred_tool_cache(self, agent: Any = None) -> None:
        """Refresh cached tool lists from live ability_manager."""
        resolved = agent or self._resolve_runtime_agent()
        all_tools = await self._get_all_tool_infos(resolved)
        self._cached_all_tool_infos = all_tools
        self._cached_deferred_tool_infos = [
            tool for tool in all_tools
            if str(getattr(tool, "name", "") or "") not in self.eager_tools
        ]

    @staticmethod
    def _tool_name_set(tools: list[Any]) -> set[str]:
        """Return registered tool names from ability_manager entries."""
        return {
            str(getattr(tool, "name", "") or "")
            for tool in tools
            if str(getattr(tool, "name", "") or "")
        }

    async def _refresh_deferred_tool_cache_if_stale(self) -> None:
        """Refresh cache when ability_manager has tools not reflected in cache."""
        agent = self._resolve_runtime_agent()
        live_tools = await self._get_all_tool_infos(agent)
        if len(live_tools) != len(self._cached_all_tool_infos):
            await self._refresh_deferred_tool_cache(agent)
            return
        if self._tool_name_set(live_tools) != self._tool_name_set(
            self._cached_all_tool_infos
        ):
            await self._refresh_deferred_tool_cache(agent)
            return
        live_deferred = [
            tool for tool in live_tools
            if str(getattr(tool, "name", "") or "") not in self.eager_tools
        ]
        if live_deferred and not self._cached_deferred_tool_infos:
            await self._refresh_deferred_tool_cache(agent)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Filter tools to only include eager_tools."""
        if not self._lazy_load_active_for_ctx(ctx):
            return

        if getattr(ctx, "agent", None) is not None:
            self._runtime_agent = ctx.agent
        self._resolve_runtime_agent(ctx)
        await self._refresh_deferred_tool_cache_if_stale()

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
                "2. **Then** call `invoke_tool` with exact `tool_name` and `arguments`.\n"
                "3. **Do NOT call `tools_search` again** for a tool whose schema already "
                "appears in conversation history — reuse it and call `invoke_tool` directly.\n"
                "4. **One tool per question.** If you already have (or have previously used) a tool "
                "that can answer the user's question, do NOT call `tools_search` to discover "
                "alternative tools or invoke multiple tools to cross-validate — one tool is "
                "sufficient unless the user explicitly requests comparison.\n\n"
                "**Do NOT attempt to call these tools directly - they will fail.**\n\n"
            )
            empty = "- (no deferred tools available)"
        else:
            header = (
                "## 按需可见工具导航（不可直接调用）\n\n"
                "**重要提示：以下工具不在当前 tools 列表中，无法直接调用。**\n\n"
                "使用方法：\n"
                "1. **必须先**调用 `tools_search`，传入与导航列表一致的 `tool_name`，获取完整参数 schema。\n"
                "2. **然后**调用 `invoke_tool`，传入精确 `tool_name` 和根据 schema 构造的 `arguments`。\n"
                "3. **如果当前对话历史中已有该工具的 schema（来自之前的 `tools_search` 返回结果），"
                "切勿再次调用 `tools_search`，直接根据已有 schema 调用 `invoke_tool` 即可。\n"
                "4. **一个问题只用一个工具。** 如果你已经有（或之前已用过）能回答用户问题的工具，"
                "切勿再调 `tools_search` 去发现替代工具，也不要调用多个工具做交叉验证——"
                "一个工具就够了，除非用户明确要求比较。\n\n"
                "**切勿直接调用以下工具——直接调用会失败。**\n\n"
            )
            empty = "- （当前无按需可见工具）"

        return header + ("\n".join(items) if items else empty)

    async def _get_all_tool_infos(self, agent: Any = None) -> list[Any]:
        """Get all registered tool infos from ability_manager."""
        resolved = agent or self._runtime_agent or self._deep_agent
        if resolved is None:
            return []

        ability_manager = getattr(resolved, "ability_manager", None)
        if ability_manager is None:
            return []

        try:
            return list(ability_manager.list())
        except Exception:
            return []

    def _find_deferred_tool_matches(self, tool_name_key: str) -> list[Any]:
        """Return deferred tools whose name matches tool_name_key (case-insensitive)."""
        matches = []
        for tool in self._cached_deferred_tool_infos:
            name = str(getattr(tool, "name", "") or "")
            if name.lower() == tool_name_key:
                matches.append(tool)
        return matches

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

        matches = self._find_deferred_tool_matches(tool_name_key)
        if not matches:
            await self._refresh_deferred_tool_cache()
            matches = self._find_deferred_tool_matches(tool_name_key)
        if not matches:
            runtime = self._runtime_agent
            if runtime is not None and runtime is not self._deep_agent:
                logger.info(
                    "%s search retry with runtime agent old_id=%s new_id=%s",
                    _LOG_PREFIX,
                    id(self._deep_agent),
                    id(runtime),
                )
                self._deep_agent = runtime
                self.invalidate_deferred_tool_cache()
                await self._refresh_deferred_tool_cache(runtime)
                matches = self._find_deferred_tool_matches(tool_name_key)

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
