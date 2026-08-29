"""Prompt rail for agentic installed-skill retrieval."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.prompt_attachment_manager import PromptAttachmentKind
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.rails import SkillUseRail
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.symphony.agent import (
    is_agentic_retrieval_enabled,
    render_skill_retrieval_prompt_for_visible_skills,
)

logger = logging.getLogger(__name__)

_LEGACY_LIST_SKILL_TOOL_NAMES = frozenset({"list_skill", "list_skills"})


class SkillRetrievalPromptRail(DeepAgentRail):
    """Deliver lightweight skill-tree retrieval guidance as history state."""

    # openjiuwen's callback framework executes higher priorities first, and
    # SkillUseRail rebuilds the native skills section on every model call. This
    # rail must therefore run after it, or the section it hides is immediately
    # re-added and the model sees both prompts. Derive the value instead of
    # hardcoding it: openjiuwen has already moved SkillUseRail once (100 -> 95),
    # and a stale constant silently reverses the order rather than failing.
    priority = SkillUseRail.priority - 1
    SECTION_NAME = "skill_retrieval"
    SECTION_PRIORITY = 41
    ATTACHMENT_SOURCE = "jiuwenswarm.skill_retrieval_prompt_rail"

    def __init__(
        self,
        *,
        manager: Any | None = None,
        visible_skill_names: set[str] | frozenset[str] | Callable[[], set[str] | frozenset[str] | None] | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager
        self._visible_skill_names = visible_skill_names
        self._agent = None
        self.system_prompt_builder = None
        self.attachment_manager = None
        self._hidden_legacy_abilities: dict[str, Any] = {}
        self._hidden_skills_section: PromptSection | None = None

    def init(self, agent: Any) -> None:
        self._agent = agent
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
        self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

    def uninit(self, agent: Any) -> None:
        self._restore_legacy_list_skill(agent)
        self._restore_native_skills_section()
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
        self.system_prompt_builder = None
        self._agent = None
        self.attachment_manager = None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """Stage the retrieval prompt before the first admitted user turn."""
        await self._sync_prompt_attachment(ctx)

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await self._sync_prompt_attachment(ctx)

    async def _sync_prompt_attachment(self, ctx: AgentCallbackContext) -> None:
        """Keep retrieval guidance out of the cache-stable system prefix."""
        agent = getattr(ctx, "agent", None)
        if agent is not None:
            self._agent = agent
            if self.system_prompt_builder is None:
                self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)
            if self.attachment_manager is None:
                self.attachment_manager = getattr(agent, "prompt_attachment_manager", None)

        if not is_agentic_retrieval_enabled():
            await self._clear_prompt_attachment(ctx)
            self._disable_agentic_prompt()
            return

        if self.system_prompt_builder is None:
            return

        language = getattr(self.system_prompt_builder, "language", "cn") or "cn"
        try:
            content = await asyncio.to_thread(
                render_skill_retrieval_prompt_for_visible_skills,
                self._manager,
                language=language,
                visible_skill_names=self._resolve_visible_skill_names(),
            )
        except Exception as exc:
            logger.warning("[SkillRetrievalPromptRail] render failed: %s", exc)
            content = ""

        if not content.strip():
            self.system_prompt_builder.remove_section(self.SECTION_NAME)
            self._restore_native_skills_section()
            await self._clear_prompt_attachment(ctx)
            return

        self._hide_legacy_list_skill()
        self._filter_legacy_list_skill_from_model_inputs(ctx)
        self._hide_native_skills_section()
        manager = self.attachment_manager
        if manager is None:
            self.system_prompt_builder.add_section(
                PromptSection(
                    name=self.SECTION_NAME,
                    content={language: content},
                    priority=self.SECTION_PRIORITY,
                )
            )
            return
        try:
            await manager.bind_context(ctx).add_section(
                section=self.SECTION_NAME,
                content=content,
                kind=PromptAttachmentKind.SKILL,
                source=self.ATTACHMENT_SOURCE,
                priority=self.SECTION_PRIORITY,
                content_kind="text/markdown",
            )
        except ValueError as exc:
            logger.warning("[SkillRetrievalPromptRail] attachment write failed: %s", exc)
            self.system_prompt_builder.add_section(
                PromptSection(
                    name=self.SECTION_NAME,
                    content={language: content},
                    priority=self.SECTION_PRIORITY,
                )
            )

    async def _clear_prompt_attachment(self, ctx: AgentCallbackContext) -> None:
        manager = self.attachment_manager
        if manager is None:
            return
        try:
            await manager.bind_context(ctx).clear_section(self.SECTION_NAME)
        except ValueError as exc:
            logger.warning("[SkillRetrievalPromptRail] attachment clear failed: %s", exc)

    def _disable_agentic_prompt(self) -> None:
        self._restore_legacy_list_skill(self._agent)
        self._restore_native_skills_section()
        if self.system_prompt_builder is not None:
            self.system_prompt_builder.remove_section(self.SECTION_NAME)

    def _resolve_visible_skill_names(self) -> set[str] | frozenset[str] | None:
        provider = self._visible_skill_names
        if callable(provider):
            return provider()
        return provider

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        self._restore_legacy_list_skill(getattr(ctx, "agent", None))
        self._restore_native_skills_section()

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        self._restore_legacy_list_skill(getattr(ctx, "agent", None))
        self._restore_native_skills_section()

    def _hide_legacy_list_skill(self) -> None:
        ability_manager = getattr(self._agent, "ability_manager", None)
        if ability_manager is None:
            return
        get_ability = getattr(ability_manager, "get", None)
        remove_ability = getattr(ability_manager, "remove", None)
        if not callable(get_ability) or not callable(remove_ability):
            return

        for name in _LEGACY_LIST_SKILL_TOOL_NAMES:
            if name in self._hidden_legacy_abilities:
                continue
            card = get_ability(name)
            if card is None:
                continue
            removed = remove_ability(name)
            if removed is not None:
                self._hidden_legacy_abilities[name] = removed

    def _restore_legacy_list_skill(self, agent: Any | None = None) -> None:
        if agent is not None:
            self._agent = agent
        ability_manager = getattr(self._agent, "ability_manager", None)
        if ability_manager is None or not self._hidden_legacy_abilities:
            return
        get_ability = getattr(ability_manager, "get", None)
        add_ability = getattr(ability_manager, "add", None)
        if not callable(get_ability) or not callable(add_ability):
            return

        for name, card in list(self._hidden_legacy_abilities.items()):
            if get_ability(name) is None:
                add_ability(card)
            self._hidden_legacy_abilities.pop(name, None)

    def _hide_native_skills_section(self) -> None:
        if self.system_prompt_builder is None:
            return
        if self._hidden_skills_section is None:
            self._hidden_skills_section = self.system_prompt_builder.get_section(SectionName.SKILLS)
        self.system_prompt_builder.remove_section(SectionName.SKILLS)

    def _restore_native_skills_section(self) -> None:
        if self.system_prompt_builder is None or self._hidden_skills_section is None:
            return
        if not self.system_prompt_builder.has_section(SectionName.SKILLS):
            self.system_prompt_builder.add_section(self._hidden_skills_section)
        self._hidden_skills_section = None

    @staticmethod
    def _filter_legacy_list_skill_from_model_inputs(ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        tools = getattr(inputs, "tools", None)
        if not tools:
            return

        filtered = []
        for tool in tools:
            name = SkillRetrievalPromptRail._model_tool_name(tool)
            if name not in _LEGACY_LIST_SKILL_TOOL_NAMES:
                filtered.append(tool)
        if len(filtered) != len(tools):
            inputs.tools = filtered

    @staticmethod
    def _model_tool_name(tool: Any) -> str:
        if isinstance(tool, dict):
            function = tool.get("function")
            if isinstance(function, dict):
                return str(function.get("name", "") or "")
            return str(tool.get("name", "") or "")
        return str(getattr(tool, "name", "") or "")


__all__ = ["SkillRetrievalPromptRail"]
