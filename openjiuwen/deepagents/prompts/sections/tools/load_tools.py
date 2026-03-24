# coding: utf-8
"""Bilingual description and input params for LoadToolsTool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": "将选定的真实工具加载到当前 session 可见工具集合中。",
    "en": "Load selected real tools into the current session-visible tool set.",
}

LOAD_TOOLS_PARAMS: Dict[str, Dict[str, str]] = {
    "tool_names": {
        "cn": "要在当前 session 中可见的工具名称列表",
        "en": "Names of tools to make visible for the current session",
    },
    "replace": {
        "cn": "如果为 true，替换当前可见工具集，否则合并",
        "en": "If true, replace the current visible tool set instead of merging",
    },
}


def get_load_tools_input_params(language: str = "cn") -> Dict[str, Any]:
    p = LOAD_TOOLS_PARAMS
    return {
        "type": "object",
        "properties": {
            "tool_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": p["tool_names"].get(language, p["tool_names"]["cn"]),
            },
            "replace": {
                "type": "boolean",
                "description": p["replace"].get(language, p["replace"]["cn"]),
            },
        },
        "required": ["tool_names"],
    }


class LoadToolsMetadataProvider(ToolMetadataProvider):
    """LoadToolsTool 的元数据 provider"""

    def get_name(self) -> str:
        return "load_tools"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_load_tools_input_params(language)