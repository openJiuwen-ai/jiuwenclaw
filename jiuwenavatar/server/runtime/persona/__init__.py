# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persona system — 数字分身身份模板管理.

Persona 是数字分身的身份模板，定义了分身的系统提示、技能集、触发器模板和报告模板。
创建 Avatar（数字分身）时选择一个 Persona，Persona 引用的 Skill 会自动安装。
"""

from jiuwenavatar.server.runtime.persona.models import (
    PersonaConfig,
    PersonaTriggerTemplate,
    PersonaReportSection,
    PersonaReportTemplate,
    AvatarConfig,
    AvatarStatus,
)
from jiuwenavatar.server.runtime.persona.manager import PersonaManager, get_persona_manager

__all__ = [
    "PersonaConfig",
    "PersonaTriggerTemplate",
    "PersonaReportSection",
    "PersonaReportTemplate",
    "AvatarConfig",
    "AvatarStatus",
    "PersonaManager",
    "get_persona_manager",
]
