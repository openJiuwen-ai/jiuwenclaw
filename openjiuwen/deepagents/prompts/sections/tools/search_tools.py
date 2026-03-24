# coding: utf-8
"""Bilingual description and input params for SearchToolsTool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import ToolMetadataProvider

DESCRIPTION: Dict[str, str] = {
    "cn": "根据能力、名称、描述或参数提示搜索候选工具。仅用于发现，不会直接调用工具。",
    "en": (
        "Search candidate tools by capability, name, description, "
        "or parameter hints. Discovery only; tools are not directly callable."
    ),
}

SEARCH_TOOLS_PARAMS: Dict[str, Dict[str, str]] = {
    "query": {
        "cn": "搜索候选工具的查询文本",
        "en": "Search query for finding relevant candidate tools",
    },
    "limit": {
        "cn": "返回候选工具的最大数量",
        "en": "Maximum number of candidate tools to return",
    },
    "detail_level": {
        "cn": "1=name+描述, 2=+参数摘要, 3=+完整参数",
        "en": "1=name+description, 2=+parameter summary, 3=+full parameters",
    },
}


def get_search_tools_input_params(language: str = "cn") -> Dict[str, Any]:
    p = SEARCH_TOOLS_PARAMS
    return {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": p["query"].get(language, p["query"]["cn"]),
            },
            "limit": {
                "type": "integer",
                "description": p["limit"].get(language, p["limit"]["cn"]),
            },
            "detail_level": {
                "type": "integer",
                "description": p["detail_level"].get(language, p["detail_level"]["cn"]),
            },
        },
        "required": ["query"],
    }


class SearchToolsMetadataProvider(ToolMetadataProvider):
    """SearchToolsTool 的元数据 provider"""

    def get_name(self) -> str:
        return "search_tools"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_search_tools_input_params(language)