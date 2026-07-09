# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Avatar chat context — 主对话页选择分身时的运行时上下文解析."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from jiuwenavatar.server.runtime.persona.models import AvatarConfig, PersonaConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AvatarChatContext:
    """Resolved runtime context for web chat with a selected Avatar.

    编码后端无关：仅携带 ``coding_engine`` 名称与 ``skills_root``，
    具体引擎（jiuwen / claude-code / codex）的工作区准备、CLI 安装、提示注入
    全部由 ``server.runtime.coding`` 统一处理。
    """

    avatar_id: str
    avatar_name: str
    persona_id: str
    persona_display_name: str
    system_prompt: str
    skills: tuple[str, ...]
    coding_engine: str | None = None
    skills_root: str = ""


def resolve_avatar_chat_context(avatar_id: str | None) -> AvatarChatContext | None:
    """根据 avatar_id 解析分身对话上下文；无效 ID 返回 None."""
    raw = (avatar_id or "").strip()
    if not raw:
        return None

    from jiuwenavatar.server.runtime.persona.manager import PersonaManager

    manager = PersonaManager.get_instance()
    avatar_dict = manager.get_avatar(raw)
    if not avatar_dict:
        return None

    avatar = AvatarConfig(**avatar_dict)
    persona_dict = manager.get_persona(avatar.persona_id)
    if not persona_dict:
        return None

    persona = PersonaConfig(**persona_dict)
    skills = tuple(avatar.get_effective_skills(persona))
    if not skills:
        return None

    from jiuwenavatar.common.utils import get_agent_skills_dir

    skills_root = str(get_agent_skills_dir())
    coding_engine = avatar.coding_engine
    if coding_engine is None and persona.coding_capable:
        coding_engine = persona.default_coding_engine

    return AvatarChatContext(
        avatar_id=avatar.id,
        avatar_name=avatar.name,
        persona_id=persona.id,
        persona_display_name=persona.display_name,
        system_prompt=avatar.get_effective_system_prompt(persona).strip(),
        skills=skills,
        coding_engine=coding_engine,
        skills_root=skills_root,
    )


async def ensure_avatar_skills_installed(
    skills: tuple[str, ...],
    skill_manager: Any,
) -> tuple[list[str], list[str]]:
    """将分身绑定的内置 Skill 安装到用户 workspace（缺失时从 avatar-skills 复制）."""
    from jiuwenavatar.common.utils import resolve_builtin_skill_dir

    installed: list[str] = []
    missing: list[str] = []

    for skill_id in skills:
        try:
            if skill_manager.get_skill_meta(skill_id) is not None:
                installed.append(skill_id)
                continue

            if resolve_builtin_skill_dir(skill_id) is None:
                missing.append(skill_id)
                logger.warning("Builtin skill not found in package: %s", skill_id)
                continue

            result = await skill_manager.handle_skills_install_builtin({"name": skill_id})
            if result.get("success") or skill_manager.get_skill_meta(skill_id) is not None:
                installed.append(skill_id)
                logger.info("Ensured avatar skill installed: %s", skill_id)
            else:
                missing.append(skill_id)
                logger.warning(
                    "Failed to install avatar skill %s: %s",
                    skill_id,
                    result.get("detail", ""),
                )
        except Exception:
            missing.append(skill_id)
            logger.warning("Failed to install avatar skill %s", skill_id, exc_info=True)

    return installed, missing
