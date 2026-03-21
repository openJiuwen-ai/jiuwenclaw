# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Code execution tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "执行代码（Python 或 JavaScript）。",
    "en": "Execute code (Python or JavaScript).",
}

CODE_PARAMS: Dict[str, Dict[str, str]] = {
    "code": {
        "cn": "要执行的代码",
        "en": "Code to execute",
    },
    "language": {
        "cn": "编程语言，支持 python 或 javascript，默认 python",
        "en": "Programming language, supports python or javascript, default python",
    },
    "timeout": {
        "cn": "超时时间（秒），默认 300",
        "en": "Timeout in seconds, default 300",
    },
}


def get_code_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for code tool input_params."""
    p = CODE_PARAMS
    return {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": p["code"].get(language, p["code"]["cn"])},
            "language": {"type": "string", "description": p["language"].get(language, p["language"]["cn"])},
            "timeout": {"type": "integer", "description": p["timeout"].get(language, p["timeout"]["cn"])},
        },
        "required": ["code"],
    }


class CodeMetadataProvider(ToolMetadataProvider):
    """Code 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "code"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_code_input_params(language)
