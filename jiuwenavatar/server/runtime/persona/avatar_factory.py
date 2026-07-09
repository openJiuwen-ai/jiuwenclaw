# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Avatar Factory — Persona → Avatar 实例化工厂.

负责将 Persona 模板实例化为可运行的 Avatar，包括：
1. 安装 Persona 关联的 Skill 到用户目录
2. 生成 Avatar 的运行时配置
3. 与现有 AgentManager 集成
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenavatar.server.runtime.persona.models import AvatarConfig, PersonaConfig
from jiuwenavatar.server.runtime.persona.manager import PersonaManager

logger = logging.getLogger(__name__)


class AvatarFactory:
    """Factory for creating Avatar runtime instances from Persona templates."""

    @staticmethod
    async def instantiate_avatar(
        avatar: AvatarConfig,
        persona: PersonaConfig,
        *,
        skill_manager: Any = None,
    ) -> dict[str, Any]:
        """Instantiate an Avatar for runtime use.

        This includes:
        1. Installing persona skills (if not already installed)
        2. Building the runtime system prompt
        3. Returning the runtime configuration

        Args:
            avatar: The AvatarConfig instance.
            persona: The PersonaConfig the avatar is based on.
            skill_manager: Optional SkillManager for installing skills.

        Returns:
            Runtime configuration dict for the avatar.
        """
        # Install persona skills if skill_manager is available.
        # 统一走 ensure_avatar_skills_installed（唯一内置安装入口），避免与
        # chat_context / interface_deep 各自维护一套安装逻辑导致行为分叉。
        installed_skills: list[str] = []
        missing_skills: list[str] = []

        effective_skills = avatar.get_effective_skills(persona)

        if skill_manager is not None:
            from jiuwenavatar.server.runtime.persona.chat_context import (
                ensure_avatar_skills_installed,
            )

            installed_skills, missing_skills = await ensure_avatar_skills_installed(
                tuple(effective_skills), skill_manager,
            )
        else:
            installed_skills = list(effective_skills)

        # Build effective system prompt
        effective_prompt = avatar.get_effective_system_prompt(persona)

        return {
            "avatar_id": avatar.id,
            "avatar_name": avatar.name,
            "persona_id": persona.id,
            "system_prompt": effective_prompt,
            "skills": installed_skills,
            "missing_skills": missing_skills,
            "coding_engine": avatar.coding_engine,
            "report_template": persona.report_template.model_dump(),
            "trigger_templates": [t.model_dump() for t in persona.trigger_templates],
        }

    @staticmethod
    async def create_and_instantiate(
        *,
        persona_id: str,
        name: str | None = None,
        system_prompt: str | None = None,
        extra_skills: list[str] | None = None,
        report_channels: list[str] | None = None,
        skill_manager: Any = None,
    ) -> dict[str, Any]:
        """Convenience method: create an Avatar and instantiate it in one step.

        Returns:
            Dict with both the avatar config and runtime info.
        """
        manager = PersonaManager.get_instance()

        # Create the avatar
        avatar_dict = manager.create_avatar(
            persona_id=persona_id,
            name=name,
            system_prompt=system_prompt,
            extra_skills=extra_skills,
            report_channels=report_channels,
        )
        avatar = AvatarConfig(**avatar_dict)

        # Get the persona
        persona_dict = manager.get_persona(persona_id)
        if persona_dict is None:
            return {"error": f"Persona not found: {persona_id}"}
        persona = PersonaConfig(**persona_dict)

        # Instantiate
        runtime = await AvatarFactory.instantiate_avatar(
            avatar, persona, skill_manager=skill_manager,
        )

        return {
            "avatar": avatar_dict,
            "runtime": runtime,
        }
