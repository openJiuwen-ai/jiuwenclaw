# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-session SkillUseRail: isolate skill tools in Runner.resource_mgr."""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.prompts import PromptSection
from openjiuwen.harness.prompts.sections import SectionName
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

_SKILLS_QA_HINT = {
    "cn": (
        "列举/计数可用技能时，只答下方编号列表中的技能；"
        "禁止调用 `office_claw_list_skills`；"
        "禁止补充列表外技能名；勿参考对话历史"
        "（用户说「检索/不要参考前面」亦同）。"
    ),
    "en": (
        "When listing or counting available skills, answer only from the numbered list below; "
        "do not call `office_claw_list_skills`; do not add skill names not in the list; "
        "ignore conversation history (including when the user says search or "
        "do not use prior answers)."
    ),
}

_SKILLS_COUNT_PREFIX = {
    "cn": "（以上编号列表共",
    "en": "(The numbered list above contains",
}

_COUNT_LINE_CN = re.compile(r"^（以上编号列表共.*）\s*$")
_COUNT_LINE_EN = re.compile(
    r"^\(The numbered list above contains.*\)\s*$"
)

_NUMBERED_SKILL_LINE = re.compile(r"^\d+\.\s")


def _build_skills_count_line(lang: str, skill_count: int) -> str:
    if lang == "en":
        noun = "skill" if skill_count == 1 else "skills"
        return (
            f"(The numbered list above contains {skill_count} {noun}; "
            f"when counting skills, answer must be {skill_count}.)"
        )
    return f"（以上编号列表共 {skill_count} 项；回答技能数量时必须为 {skill_count}。）"


def _find_skills_list_end(text: str, marker: str) -> int:
    """Return the index after the last numbered skill line following *marker*."""
    marker_pos = text.find(marker)
    if marker_pos < 0:
        return len(text)
    pos = marker_pos + len(marker)
    last_line_end = pos
    while pos < len(text):
        if text[pos] == "\n":
            pos += 1
            continue
        line_end = text.find("\n", pos)
        if line_end < 0:
            line_end = len(text)
        line = text[pos:line_end].strip()
        if not line:
            pos = line_end + 1 if line_end < len(text) else len(text)
            continue
        if _NUMBERED_SKILL_LINE.match(line):
            last_line_end = line_end
            pos = line_end + 1 if line_end < len(text) else len(text)
        else:
            break
    return last_line_end


def _strip_skills_qa_annotations(text: str, lang: str) -> str:
    """Remove prior skills QA hint and count lines so patches can be reapplied."""
    hint_cn = _SKILLS_QA_HINT["cn"].strip()
    hint_en = _SKILLS_QA_HINT["en"].strip()
    count_prefix_cn = _SKILLS_COUNT_PREFIX["cn"]
    count_prefix_en = _SKILLS_COUNT_PREFIX["en"]
    count_prefix = _SKILLS_COUNT_PREFIX.get(lang, count_prefix_cn)

    kept: list[str] = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if stripped in (hint_cn, hint_en):
            continue
        if _COUNT_LINE_CN.match(stripped) or _COUNT_LINE_EN.match(stripped):
            continue
        if (
            stripped.startswith(count_prefix)
            or stripped.startswith(count_prefix_cn)
            or stripped.startswith(count_prefix_en)
        ):
            continue
        kept.append(line)
    return "\n".join(kept)


def _patch_skills_section_text(raw: str, lang: str, skill_count: int) -> str | None:
    """Strip stale QA annotations and inject fresh hint + skill count line."""
    marker = "可用技能：" if lang == "cn" else "Available skills:"
    if marker not in raw:
        return None

    hint = _SKILLS_QA_HINT.get(lang, _SKILLS_QA_HINT["cn"])
    stripped = _strip_skills_qa_annotations(raw, lang)
    marker_pos = stripped.find(marker)
    patched = stripped[:marker_pos] + hint + "\n" + stripped[marker_pos:]
    count_line = _build_skills_count_line(lang, skill_count)
    insert_at = _find_skills_list_end(patched, marker)
    return patched[:insert_at] + "\n" + count_line + patched[insert_at:]


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

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        await super().before_model_call(ctx)
        builder = self.system_prompt_builder
        if builder is None:
            return
        section = builder.get_section(SectionName.SKILLS)
        if section is None:
            return
        lang = getattr(builder, "language", None) or "cn"
        if lang == "zh":
            lang = "cn"
        raw = section.content.get(lang) or section.content.get("cn") or ""
        if "可用技能：" not in raw and "Available skills:" not in raw:
            return

        skill_count = len(self.skills) if self.skills else 0
        patched = _patch_skills_section_text(raw, lang, skill_count)
        if patched is None or patched == raw:
            return

        content = dict(section.content)
        content[lang] = patched
        builder.add_section(
            PromptSection(
                name=SectionName.SKILLS,
                content=content,
                priority=section.priority,
            )
        )


__all__ = ["JiuWenSkillUseRail"]
