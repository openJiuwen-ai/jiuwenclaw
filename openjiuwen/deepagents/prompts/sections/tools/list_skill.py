# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the ListSkill tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "列出可用技能或为当前任务选择相关技能。",
    "en": "List available skills or select relevant skills for the current task.",
}

LIST_SKILL_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {
        "cn": "可选。当前用户任务。为空时返回所有可用技能。",
        "en": "Optional. Current user task. If empty, return all available skills.",
    },
}


def get_list_skill_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for list_skill tool input_params."""
    p = LIST_SKILL_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": p["query"].get(language, p["query"]["cn"])},
        },
        "required": [],
    }


class ListSkillMetadataProvider(ToolMetadataProvider):
    """ListSkill 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "list_skill"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_list_skill_input_params(language)
