# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""ProgressiveToolRail for large-scale tool usage with progressive disclosure."""

from __future__ import annotations

import inspect
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm.schema.message import SystemMessage
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.runner.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.prompts.builder import SystemPromptBuilder
from openjiuwen.deepagents.prompts.sections.progressive_tool_rail import (
    build_multilingual_navigation_section,
    build_multilingual_progressive_tool_rules_section,
    build_navigation_entry,
)
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.deepagents.tools.load_tools import LoadToolsTool
from openjiuwen.deepagents.tools.search_tools import SearchToolsTool

_VISIBLE_TOOLS_KEY = "__progressive_visible_tool_names__"
_DISCOVERY_TRACE_KEY = "__progressive_tool_discovery_trace__"


class ProgressiveToolRail(DeepAgentRail):
    """Rail that enables progressive tool discovery and callable-tool filtering."""

    priority = 90

    def __init__(self, config: DeepAgentConfig):
        """Initialize ProgressiveToolRail.

        Args:
            config: DeepAgentConfig containing progressive tool settings.
        """
        super().__init__()
        self._config = config
        self.default_visible_tools = set(config.progressive_tool_default_visible_tools or [])
        self.always_visible_tools = set(config.progressive_tool_always_visible_tools or [])
        self.max_loaded_tools = config.progressive_tool_max_loaded_tools

        self._meta_tool_names: Set[str] = set()
        self._owned_tool_names: Set[str] = set()
        self._owned_tool_ids: Set[str] = set()
        self._cached_all_tool_infos: List[ToolInfo] = []

    def init(self, agent) -> None:
        """Register progressive meta tools to resource manager and ability manager."""
        language = getattr(self._config, "language", "cn") or "cn"

        tools = [
            SearchToolsTool(
                search_tools=self._search_tools,
                append_trace=self._append_trace,
                language=language,
            ),
            LoadToolsTool(
                load_tools=self._load_tools,
                language=language,
            ),
        ]

        self._meta_tool_names = {tool.card.name for tool in tools}

        for tool in tools:
            try:
                existing_tool = Runner.resource_mgr.get_tool(tool.card.id)
                if existing_tool is None:
                    Runner.resource_mgr.add_tool(tool)
                    self._owned_tool_ids.add(tool.card.id)
            except Exception as exc:
                logger.warning(
                    f"[ProgressiveToolRail] failed to add tool resource '{tool.card.id}' "
                    f"to resource_mgr: {exc}"
                )

        if hasattr(agent, "ability_manager"):
            for tool in tools:
                try:
                    result = agent.ability_manager.add(tool.card)
                    if result.added:
                        self._owned_tool_names.add(tool.card.name)
                except Exception as exc:
                    logger.warning(
                        f"[ProgressiveToolRail] failed to add tool card '{tool.card.name}' "
                        f"to ability_manager: {exc}"
                    )

    def uninit(self, agent) -> None:
        """Remove meta tools registered by this rail."""
        if hasattr(agent, "ability_manager"):
            for tool_name in list(self._owned_tool_names):
                try:
                    agent.ability_manager.remove(tool_name)
                except Exception as exc:
                    logger.warning(
                        f"[ProgressiveToolRail] failed to remove tool '{tool_name}' "
                        f"from ability_manager: {exc}"
                    )

        self._owned_tool_names.clear()
        self._owned_tool_ids.clear()
        self._meta_tool_names.clear()
        self._cached_all_tool_infos = []

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Cache full tool inventory and initialize per-session visible tools."""
        self._cached_all_tool_infos = await self._list_tool_infos(ctx.agent)

        session = getattr(ctx, "session", None)
        self._init_visible_tools(
            session,
            default_visible_tools=list(self.default_visible_tools),
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject prompt sections and filter callable tools for the current turn."""
        session = getattr(ctx, "session", None)

        # --------------------------------------------------
        # DEBUG 1: ctx.agent 到底是谁，builder 挂没挂上
        # --------------------------------------------------
        logger.info(
            "[ProgressiveToolRail][DEBUG] before_model_call | agent_type=%s | has_system_prompt_builder=%s",
            type(getattr(ctx, "agent", None)).__name__,
            hasattr(getattr(ctx, "agent", None), "system_prompt_builder"),
        )

        builder = self._get_prompt_builder(ctx)

        navigation_section = await self._build_navigation_section(session)
        rules_section = self._build_progressive_tool_rules_section()

        builder.add_section(navigation_section)
        builder.add_section(rules_section)

        rebuilt_prompt = builder.build()
        self._replace_system_prompt(ctx, rebuilt_prompt)

        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not isinstance(tools, list):
            logger.info(
                "[ProgressiveToolRail][DEBUG] before_model_call | inputs.tools is not a list: %s",
                type(tools).__name__,
            )
            return

        # --------------------------------------------------
        # DEBUG 2: 过滤前工具数量
        # --------------------------------------------------
        original_tool_names = [
            str(getattr(tool, "name", "") or "")
            for tool in tools
            if str(getattr(tool, "name", "") or "")
        ]
        logger.info(
            "[ProgressiveToolRail][DEBUG] before filter | tool_count=%s | first_20=%s",
            len(original_tool_names),
            original_tool_names[:20],
        )

        session_visible_tools = set(self._get_visible_tools(session))
        baseline_visible_tools = set(self.always_visible_tools)
        meta_visible_tools = set(self._meta_tool_names)

        logger.info(
            "[ProgressiveToolRail][DEBUG] visibility | meta=%s | baseline=%s | session_visible=%s",
            sorted(meta_visible_tools),
            sorted(baseline_visible_tools),
            sorted(session_visible_tools),
        )

        filtered_tools: List[ToolInfo] = []

        for tool in tools:
            tool_name = str(getattr(tool, "name", "") or "")
            if not tool_name:
                continue

            if tool_name in meta_visible_tools:
                filtered_tools.append(tool)
                continue

            if tool_name in baseline_visible_tools:
                filtered_tools.append(tool)
                continue

            if tool_name in session_visible_tools:
                filtered_tools.append(tool)
                continue

        filtered_tool_names = [
            str(getattr(tool, "name", "") or "")
            for tool in filtered_tools
            if str(getattr(tool, "name", "") or "")
        ]

        # --------------------------------------------------
        # DEBUG 3: 过滤后工具数量
        # --------------------------------------------------
        logger.info(
            "[ProgressiveToolRail][DEBUG] after filter | tool_count=%s | tools=%s",
            len(filtered_tool_names),
            filtered_tool_names,
        )

        inputs.tools = filtered_tools

    async def _list_tool_infos(self, agent) -> List[ToolInfo]:
        """List all tool infos currently registered on the agent."""
        if not hasattr(agent, "ability_manager"):
            return []
        try:
            tool_infos = await agent.ability_manager.list_tool_info()
            return list(tool_infos or [])
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to list tool infos: {exc}")
            return []

    async def _list_all_tool_infos(self) -> List[ToolInfo]:
        """Return cached full tool inventory."""
        return list(self._cached_all_tool_infos or [])

    async def _get_real_tool_infos(self) -> List[ToolInfo]:
        """Return non-meta tools from the cached inventory."""
        infos = await self._list_all_tool_infos()
        return [
            tool
            for tool in infos
            if getattr(tool, "name", "") not in self._meta_tool_names
        ]

    async def _search_tools(
        self,
        query: str,
        limit: int = 10,
        detail_level: int = 1,
    ) -> List[Dict[str, Any]]:
        """Search the cached tool inventory by name, description, and parameter text."""
        query = (query or "").strip().lower()
        if not query:
            return []

        all_tools = await self._get_real_tool_infos()
        scored: List[tuple[int, ToolInfo]] = []

        for tool in all_tools:
            name = str(getattr(tool, "name", "") or "")
            description = str(getattr(tool, "description", "") or "")
            parameters = getattr(tool, "parameters", None)

            haystack = " ".join(
                [
                    name.lower(),
                    description.lower(),
                    self._parameters_to_text(parameters).lower(),
                ]
            )

            score = 0
            if query == name.lower():
                score += 100
            if query in name.lower():
                score += 40
            if query in description.lower():
                score += 25
            if query in haystack:
                score += 10

            for token in query.split():
                if token and token in haystack:
                    score += 3

            if score > 0:
                scored.append((score, tool))

        scored.sort(key=lambda item: (-item[0], getattr(item[1], "name", "")))
        matched = [tool for _, tool in scored[: max(1, limit)]]

        return [
            self._build_tool_summary(tool, detail_level=detail_level)
            for tool in matched
        ]

    async def _load_tools(
        self,
        session: Any,
        tool_names: List[str],
        replace: bool = False,
    ) -> Dict[str, Any]:
        """Mark tools as callable in the current session."""
        if session is None:
            return {
                "loaded_tools": [],
                "visible_tools": [],
                "skipped_tools": list(tool_names or []),
                "message": "session is required for load_tools",
            }

        all_tools = await self._get_real_tool_infos()
        available_names = {str(getattr(tool, "name", "") or "") for tool in all_tools}

        requested = [str(name).strip() for name in tool_names if str(name).strip()]
        valid_names: List[str] = []
        skipped_names: List[str] = []

        for name in requested:
            if name in self.always_visible_tools:
                valid_names.append(name)
                continue

            if name in available_names:
                valid_names.append(name)
            else:
                skipped_names.append(name)

        current_visible = self._get_visible_tools(session)

        if replace:
            next_visible = list(dict.fromkeys(valid_names))
        else:
            next_visible = list(dict.fromkeys(current_visible + valid_names))

        if len(next_visible) > self.max_loaded_tools:
            overflow = next_visible[self.max_loaded_tools:]
            skipped_names.extend(overflow)
            next_visible = next_visible[: self.max_loaded_tools]

        self._set_visible_tools(session, next_visible)

        self._append_trace(
            session,
            {
                "action": "load_tools",
                "requested": requested,
                "loaded": valid_names,
                "visible_before": list(current_visible),
                "visible_after": next_visible,
                "skipped": skipped_names,
                "replace": replace,
            },
        )

        return {
            "loaded_tools": valid_names,
            "visible_tools": next_visible,
            "skipped_tools": skipped_names,
            "message": (
                f"loaded {len(valid_names)} tool(s), "
                f"visible now: {', '.join(next_visible) if next_visible else '(none)'}"
            ),
        }

    async def _build_navigation_section(self, session: Any):
        """Build multilingual tool-navigation section."""
        entries_cn = await self._build_navigation_entries(session, language="cn")
        entries_en = await self._build_navigation_entries(session, language="en")
        return build_multilingual_navigation_section(entries_cn, entries_en)

    def _build_progressive_tool_rules_section(self):
        """Build multilingual progressive-tool-rules section."""
        return build_multilingual_progressive_tool_rules_section()

    async def _build_navigation_entries(
        self,
        session: Any,
        language: str = "cn",
    ) -> List[str]:
        """Render a compact list of navigation-worthy tools."""
        all_tools = await self._get_real_tool_infos()
        loaded = set(self._get_visible_tools(session))
        baseline = set(self.always_visible_tools) | set(self.default_visible_tools)

        entries: List[str] = []
        seen: Set[str] = set()

        def include_tool(name: str) -> bool:
            if name in seen:
                return False
            if name in baseline:
                return True
            if name in loaded:
                return True
            if name in {"code", "read_file", "bash", "list_skill", "pdf", "xlsx"}:
                return True
            return False

        sorted_tools = sorted(
            all_tools,
            key=lambda t: (
                self._tool_group_rank(t),
                str(getattr(t, "name", "") or ""),
            ),
        )

        for tool in sorted_tools:
            name = str(getattr(tool, "name", "") or "")
            if not name or not include_tool(name):
                continue
            seen.add(name)

            summary = self._tool_summary_for_navigation(tool)
            group = self._tool_group_for_navigation(tool)

            if language == "en":
                status = (
                    "callable"
                    if name in loaded or name in self.always_visible_tools
                    else "navigation-only"
                )
                group_label = group
            else:
                status = (
                    "可调用"
                    if name in loaded or name in self.always_visible_tools
                    else "仅导航"
                )
                group_label = self._tool_group_to_cn(group)

            entries.append(
                build_navigation_entry(
                    name=name,
                    group=group_label,
                    status=status,
                    summary=summary,
                    language=language,
                )
            )

        return entries

    @staticmethod
    def _tool_summary_for_navigation(tool: ToolInfo) -> str:
        """Return a short summary line for navigation display."""
        description = str(getattr(tool, "description", "") or "").strip()
        if not description:
            return "No summary available."
        line = description.splitlines()[0].strip()
        return line[:160]

    @staticmethod
    def _tool_group_for_navigation(tool: ToolInfo) -> str:
        """Infer a coarse navigation group for a tool."""
        name = str(getattr(tool, "name", "") or "").lower()
        description = str(getattr(tool, "description", "") or "").lower()

        if any(k in name for k in ["read", "write", "edit", "file", "bash", "code"]):
            return "runtime"
        if any(k in name for k in ["pdf", "invoice", "document"]):
            return "document"
        if any(k in name for k in ["xlsx", "excel", "sheet", "spreadsheet"]):
            return "spreadsheet"
        if "skill" in name:
            return "skill"
        if any(k in description for k in ["pdf", "invoice", "document"]):
            return "document"
        if any(k in description for k in ["xlsx", "excel", "spreadsheet"]):
            return "spreadsheet"
        return "general"

    @staticmethod
    def _tool_group_to_cn(group: str) -> str:
        """Translate group label into Chinese."""
        mapping = {
            "skill": "技能",
            "runtime": "运行时",
            "document": "文档",
            "spreadsheet": "表格",
            "general": "通用",
        }
        return mapping.get(group, "通用")

    @staticmethod
    def _tool_group_rank(tool: ToolInfo) -> int:
        """Sort order for navigation groups."""
        group = ProgressiveToolRail._tool_group_for_navigation(tool)
        order = {
            "skill": 0,
            "runtime": 1,
            "document": 2,
            "spreadsheet": 3,
            "general": 9,
        }
        return order.get(group, 99)

    def _get_visible_tools(self, session: Any) -> List[str]:
        """Read current session-visible tool names."""
        if session is None:
            return []
        state = session.get_state(_VISIBLE_TOOLS_KEY)
        if isinstance(state, list):
            return [str(item).strip() for item in state if str(item).strip()]
        return []

    def _set_visible_tools(self, session: Any, names: List[str]) -> None:
        """Persist current session-visible tool names."""
        if session is None:
            return
        normalized = list(
            dict.fromkeys([str(name).strip() for name in names if str(name).strip()])
        )
        session.update_state({_VISIBLE_TOOLS_KEY: normalized})

    def _init_visible_tools(
        self,
        session: Any,
        *,
        default_visible_tools: Optional[List[str]] = None,
    ) -> None:
        """Initialize session visibility state once per session."""
        if session is None:
            return

        current = session.get_state(_VISIBLE_TOOLS_KEY)
        if isinstance(current, list):
            return

        initial = list(
            dict.fromkeys(
                [
                    *list(self.always_visible_tools),
                    *(list(default_visible_tools or [])),
                ]
            )
        )
        session.update_state({_VISIBLE_TOOLS_KEY: initial})
        session.update_state({_DISCOVERY_TRACE_KEY: []})

    def _append_trace(self, session: Any, event: Dict[str, Any]) -> None:
        """Append progressive-tool discovery trace into session state."""
        if session is None:
            return
        trace = session.get_state(_DISCOVERY_TRACE_KEY)
        if not isinstance(trace, list):
            trace = []
        trace.append(event)
        session.update_state({_DISCOVERY_TRACE_KEY: trace})

    @staticmethod
    def _build_tool_summary(tool: ToolInfo, *, detail_level: int = 1) -> Dict[str, Any]:
        """Build structured tool summary payload."""
        name = str(getattr(tool, "name", "") or "")
        description = str(getattr(tool, "description", "") or "")
        parameters = getattr(tool, "parameters", None)

        payload: Dict[str, Any] = {
            "name": name,
            "description": description,
        }

        if detail_level >= 2:
            payload["parameter_summary"] = ProgressiveToolRail._parameters_summary(parameters)

        if detail_level >= 3:
            payload["parameters"] = ProgressiveToolRail._safe_serialize_parameters(parameters)

        return payload

    @staticmethod
    def _safe_serialize_parameters(parameters: Any) -> Any:
        """Safely serialize tool parameter schema."""
        try:
            if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
                try:
                    return parameters.model_json_schema()
                except Exception:
                    return str(parameters)
            if isinstance(parameters, dict):
                return parameters
            return str(parameters)
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to serialize parameters: {exc}")
            return str(parameters)

    @staticmethod
    def _parameters_summary(parameters: Any) -> str:
        """Build a short textual summary of parameters."""
        try:
            if inspect.isclass(parameters) and issubclass(parameters, BaseModel):
                fields = getattr(parameters, "model_fields", None)
                if isinstance(fields, dict):
                    names = list(fields.keys())
                    return f"fields: {', '.join(names)}" if names else "no declared fields"

            if isinstance(parameters, dict):
                props = parameters.get("properties")
                if isinstance(props, dict) and props:
                    return f"fields: {', '.join(props.keys())}"
                if parameters:
                    return f"schema keys: {', '.join(parameters.keys())}"
                return "empty schema"

            if parameters is None:
                return "no parameters"

            return str(parameters)
        except Exception as exc:
            logger.warning(f"[ProgressiveToolRail] failed to summarize parameters: {exc}")
            return "parameter summary unavailable"

    @staticmethod
    def _parameters_to_text(parameters: Any) -> str:
        """Flatten parameter summary and raw schema into searchable text."""
        summary = ProgressiveToolRail._parameters_summary(parameters)
        raw = ProgressiveToolRail._safe_serialize_parameters(parameters)
        return f"{summary} {raw}"

    @staticmethod
    def _get_prompt_builder(ctx: AgentCallbackContext) -> SystemPromptBuilder:
        """Fetch persistent SystemPromptBuilder from agent."""
        agent = getattr(ctx, "agent", None)
        if agent is None:
            raise RuntimeError("ProgressiveToolRail requires ctx.agent to exist.")

        builder = getattr(agent, "system_prompt_builder", None)
        if not isinstance(builder, SystemPromptBuilder):
            raise RuntimeError(
                "ProgressiveToolRail requires agent.system_prompt_builder "
                "to be an instance of SystemPromptBuilder."
            )
        return builder

    @staticmethod
    def _replace_system_prompt(ctx: AgentCallbackContext, new_system_prompt: str) -> None:
        """Replace the current system message content with rebuilt prompt."""
        inputs = getattr(ctx, "inputs", None)
        messages = getattr(inputs, "messages", None)
        if not isinstance(messages, list):
            return

        for msg in messages:
            if isinstance(msg, dict) and msg.get("role") == "system":
                msg["content"] = new_system_prompt
                return

            if isinstance(msg, SystemMessage):
                msg.content = new_system_prompt
                return

        messages.insert(0, SystemMessage(content=new_system_prompt))


__all__ = [
    "ProgressiveToolRail",
]