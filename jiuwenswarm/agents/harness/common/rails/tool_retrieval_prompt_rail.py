"""Prompt rail for progressive registered-tool retrieval.

Injects first-level tool categories into the system prompt and hides the
full tool definition list from the API request, leaving only the
always-visible tools (the retrieval tools themselves + critical tools).

Mirrors ``SkillRetrievalPromptRail``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.symphony.agent.agentic_retrieval_toolkit import (
    AgenticRetrievalToolKit,
)
from jiuwenswarm.symphony.tool_index.config import (
    ToolIndexConfig,
    load_tool_index_config,
)
from jiuwenswarm.symphony.tool_index.api import tool_index_status, build_tool_index
from jiuwenswarm.symphony.tool_index.scanner import ToolScanner

LOGGER = logging.getLogger(__name__)

# Tools that are ALWAYS sent to the LLM (never hidden behind the retrieval tree).
# These are the progressive-retrieval tools themselves plus other critical tools
# that must be immediately callable.
ALWAYS_VISIBLE_TOOLS: frozenset[str] = frozenset(
    {
        # Progressive retrieval tools (must be visible to start browsing)
        "tool_index_build",
        "tool_branch_explore",
        "tool_branch_peek",
        # Skill retrieval tools (must be visible for skill browsing)
        "skill_index_build",
        "skill_branch_explore",
        "skill_branch_peek",
        # Symphony orchestration tools
        "symphony_read_score",
        "symphony_refresh_score",
        "symphony_compose_score",
        # Frequently-needed tools that are safe to always expose
        "user_todos",
        # Ask-user interrupt tool
        "ask_user",
    }
)


def _resolve_tool_cards_from_cache() -> dict[str, Any] | None:
    """Use the same cached tool_cards that tool_index_build uses."""
    try:
        from jiuwenswarm.agents.harness.common.tools.tool_retrieval_toolkits import (
            _runtime_tool_cards,
        )
        if _runtime_tool_cards:
            return dict(_runtime_tool_cards)
    except Exception:
        pass
    return None


def _resolve_tool_cards_from_agent(agent: Any) -> dict[str, Any] | None:
    """Collect currently registered ToolCards from the agent's ability_manager.

    This is more reliable than reading from Runner.resource_mgr because the
    ability_manager is where ToolCards are stored in agent mode (resource_mgr's
    _tool_manager is lazily initialized and may not be populated).
    """
    try:
        am = getattr(agent, "ability_manager", None)
        if am is None:
            return None
        tools = getattr(am, "_tools", {}) or {}
        # _tools is {name: ToolCard}
        cards: dict[str, Any] = {}
        for name, card in tools.items():
            if hasattr(card, "name"):
                cards[name] = card
        return cards if cards else None
    except Exception:
        return None


def _index_is_fresh(cards: dict[str, Any], config: ToolIndexConfig) -> bool:
    """Return True when the tool index exists and matches the current tool set."""
    status = tool_index_status(cards, config=config)
    fresh = bool(status.get("index_exists") and status.get("fresh"))
    LOGGER.info(
        "[ToolRetrievalPromptRail] _index_is_fresh: exists=%s fresh=%s fingerprint_match=%s cards=%d result=%s",
        status.get("index_exists"),
        status.get("fresh"),
        status.get("fingerprint") == status.get("inventory_fingerprint"),
        len(cards),
        fresh,
    )
    return fresh


def _rerender_for_tools(markdown: str) -> str:
    """Rewrite skill-oriented labels in *markdown* to tool-oriented labels.

    ``AgenticRetrievalToolKit.root_prompt_markdown()`` always produces
    skill-flavoured output.  The tool rail calls this helper to swap every
    user-visible label so the agent sees a distinct tool-directory prompt
    without touching the shared toolkit code.
    """
    # Order matters — longer / more-specific patterns first.
    replacements = [
        # --- English ---
        ("Agentic Skill Retrieval", "Agentic Tool Retrieval"),
        ("Skill Retrieval", "Tool Retrieval"),
        ("skill_branch_explore", "tool_branch_explore"),
        ("skill_branch_peek", "tool_branch_peek"),
        ("skill_index_build", "tool_index_build"),
        ("installed skills", "registered tools"),
        ("skill-directory", "tool-directory"),
        ("skill entries", "tool entries"),
        ("a `skills` section", "a tools section"),
        ("`skills` section", "tools section"),
        ("`skills`", "`tools`"),
        # --- Chinese ---
        ("Agentic 技能检索", "Agentic 工具检索"),
        ("技能检索", "工具检索"),
        ("已安装技能", "已注册工具"),
        ("技能目录", "工具目录"),
        ("技能选择", "工具选择"),
        ("叶子技能", "叶子工具"),
        ("精确技能", "精确工具"),
        ("宽泛概览技能", "宽泛概览工具"),
        # --- Catch-all (after specific patterns) ---
        ("Skill", "Tool"),
        ("skills", "tools"),
        ("技能", "工具"),
    ]
    result = markdown
    for old, new in replacements:
        result = result.replace(old, new)
    return result


def _render_tool_prompt(
    cards: dict[str, Any],
    config: ToolIndexConfig,
    *,
    language: str = "cn",
) -> str:
    """Render the tool-category section for the system prompt.

    Returns an empty string when the tool index is unavailable.
    """
    status = tool_index_status(cards, config=config)
    if not status.get("enabled", False):
        return ""
    if not status.get("index_exists") or not status.get("fresh"):
        if language.startswith("zh") or language.startswith("cn"):
            return (
                "# Agentic 工具检索\n\n"
                "已启用适用于较大规模已注册工具场景下的 Agentic 工具检索，"
                "但当前工具索引尚未就绪。\n\n"
                "需要索引化工具检索时调用 `tool_index_build`；"
                "也可以继续按 jiuwenswarm 原有流程执行。"
            )
        return (
            "# Agentic Tool Retrieval\n\n"
            "Agentic retrieval for registered tools is enabled, but the tool "
            "index is not ready.\n\n"
            "Use `tool_index_build` when you need indexed tool retrieval, "
            "or continue with the original jiuwenswarm flow."
        )

    index_dir = config.artifact_root / "index"
    try:
        toolkit = AgenticRetrievalToolKit.from_index(index_dir)
    except Exception as exc:
        LOGGER.warning("[ToolRetrievalPromptRail] failed to load tool index: %s", exc)
        return ""

    markdown = _rerender_for_tools(toolkit.root_prompt_markdown(language=language))
    if language.startswith("zh") or language.startswith("cn"):
        markdown += (
            "\n\n"
            "**注意**：即使你在之前的对话中已经使用过某个工具，也必须优先通过 "
            "`tool_branch_explore` 确认该工具在当前环境中可用并查看其参数，"
            "而不是直接调用对话历史中出现过的工具名。"
        )
    else:
        markdown += (
            "\n\n"
            "**Note**: Even if you have used a tool in previous turns, always "
            "call `tool_branch_explore` first to confirm it is available in "
            "the current environment and to see its parameters, rather than "
            "directly calling a tool name from conversation history."
        )
    return markdown


class ToolRetrievalPromptRail(DeepAgentRail):
    """Inject lightweight tool-tree retrieval guidance into the system prompt.

    On each ``before_model_call``:
    1. Loads the tool tree index and renders first-level categories.
    2. Injects the categories as a system-prompt section.
    3. Removes all tools NOT in ``ALWAYS_VISIBLE_TOOLS`` from the model's
       ``tools`` parameter — the agent discovers them via tree browsing instead.
    """

    priority = 80  # below ContextAssembleRail(85) so we can remove its "tools" section
    SECTION_NAME = "tool_retrieval"
    SECTION_PRIORITY = 40

    def __init__(
        self,
        *,
        tool_cards: dict[str, Any] | None = None,
        config: ToolIndexConfig | None = None,
        visible_tool_names: (
            set[str] | frozenset[str] | Callable[[], set[str] | frozenset[str] | None] | None
        ) = None,
    ) -> None:
        super().__init__()
        self._tool_cards = tool_cards
        self._config = config or load_tool_index_config()
        self._visible_tool_names = visible_tool_names
        self.system_prompt_builder = None
        self._hidden_tools: dict[str, Any] = {}
        self._auto_build_triggered: bool = False

    def init(self, agent: Any) -> None:
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        # 一次性快照：记录 ability_manager 中注册的全部工具
        try:
            from jiuwenswarm.common.prompt_capture import get_capture
            cap = get_capture()
            if cap is not None:
                cap.snapshot_ability_manager(agent)
        except Exception:
            pass
        # 用 ability_manager 的全量工具（27个）刷新缓存，
        # 否则 tool_branch_explore 等运行时工具只能看到 interface_deep 预缓存的 ~10 个
        try:
            from jiuwenswarm.agents.harness.common.tools.tool_retrieval_toolkits import (
                set_runtime_tool_cards,
            )
            cards = _resolve_tool_cards_from_agent(agent)
            if cards:
                set_runtime_tool_cards(cards)
                LOGGER.info(
                    "[ToolRetrievalPromptRail] refreshed runtime_tool_cards: %d tools",
                    len(cards),
                )
        except Exception:
            pass

    def uninit(self, agent: Any) -> None:
        self._restore_all_tools(agent)
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None

    # ------------------------------------------------------------------
    # Rail lifecycle
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: Any) -> None:
        agent = getattr(ctx, "agent", None)
        if agent is not None and self.system_prompt_builder is None:
            self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        if self.system_prompt_builder is None:
            return

        language = getattr(self.system_prompt_builder, "language", "cn") or "cn"
        # 优先从 agent 的 ability_manager 拿全量工具（27个），
        # 缓存 _runtime_tool_cards 只有 jiuwenswarm 专用工具（~10个）会漏掉基础工具
        cards = _resolve_tool_cards_from_agent(agent)
        if cards is None:
            cards = self._tool_cards or _resolve_tool_cards_from_cache()
        if cards is None:
            return

        # Auto-build the tool index on first use so users never need to
        # manually call ``tool_index_build``.
        if not self._auto_build_triggered and not _index_is_fresh(cards, self._config):
            self._auto_build_triggered = True
            LOGGER.info(
                "[ToolRetrievalPromptRail] auto-build triggered (%d tools)", len(cards)
            )
            # Fire-and-forget — the NEXT model call will see the fresh index.
            asyncio.create_task(
                asyncio.to_thread(build_tool_index, cards, config=self._config)
            )

        try:
            content = await asyncio.to_thread(
                _render_tool_prompt,
                cards,
                self._config,
                language=language,
            )
        except Exception as exc:
            LOGGER.warning("[ToolRetrievalPromptRail] render failed: %s", exc)
            content = ""

        if not content.strip():
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            self._restore_all_tools(agent)
            return

        # Inject the tool-category prompt section
        self.system_prompt_builder.add_section(
            PromptSection(
                name=self.SECTION_NAME,
                content={language: content},
                priority=self.SECTION_PRIORITY,
            )
        )

        # 始终过滤 tools 参数 + 移除 "# 可用工具"，
        # 无论索引是否就绪——LLM 都只能通过树浏览发现工具
        self._filter_tools_from_request(ctx)
        self.system_prompt_builder.remove_section("tools")

        index_ready = _index_is_fresh(cards, self._config)
        if not index_ready:
            # 索引未就绪时，注入提示让 LLM 先调用 tool_index_build
            LOGGER.info(
                "[ToolRetrievalPromptRail] index not ready (%d tools), "
                "tools filtered to %d, instructing LLM to build index",
                len(cards),
                len(getattr(getattr(ctx, "inputs", None), "tools", [])),
            )

    async def after_model_call(self, ctx: Any) -> None:
        self._restore_all_tools(getattr(ctx, "agent", None))

    async def on_model_exception(self, ctx: Any) -> None:
        self._restore_all_tools(getattr(ctx, "agent", None))

    # ------------------------------------------------------------------
    # Tool filtering
    # ------------------------------------------------------------------

    def _filter_tools_from_request(self, ctx: Any) -> None:
        """Remove tools not in ALWAYS_VISIBLE_TOOLS from the model request.

        The agent discovers these tools via tool_branch_explore instead.
        """
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not tools:
            return

        visible_names = self._resolve_visible_tool_names()
        filtered: list[Any] = []
        hidden: dict[str, Any] = {}
        for tool in tools:
            name = self._tool_name(tool)
            if name in ALWAYS_VISIBLE_TOOLS or (visible_names and name in visible_names):
                filtered.append(tool)
            else:
                hidden[name] = tool

        if len(filtered) < len(tools):
            inputs.tools = filtered
            self._hidden_tools = hidden
            LOGGER.info(
                "[ToolRetrievalPromptRail] tools: %d -> %d (hidden %d)",
                len(tools),
                len(filtered),
                len(hidden),
            )

    def _restore_all_tools(self, agent: Any) -> None:
        """Restore hidden tools to the ability manager when the rail is torn down."""
        if not self._hidden_tools:
            return

        ability_manager = getattr(agent, "ability_manager", None) if agent else None
        for name, card in list(self._hidden_tools.items()):
            if ability_manager is not None and ability_manager.get(name) is None:
                ability_manager.add(card)
            self._hidden_tools.pop(name, None)

    def _resolve_visible_tool_names(self) -> set[str] | frozenset[str] | None:
        provider = self._visible_tool_names
        if callable(provider):
            return provider()
        return provider

    @staticmethod
    def _tool_name(tool: Any) -> str:
        """Extract the tool name from various representations."""
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                return str(function.get("name", "") or "")
            return str(tool.get("name", "") or "")
        return str(getattr(tool, "name", "") or "")
