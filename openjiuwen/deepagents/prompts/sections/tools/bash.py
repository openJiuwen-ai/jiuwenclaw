# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Bilingual description and input params for the Bash (shell) tool."""
from __future__ import annotations

from typing import Any, Dict

from openjiuwen.deepagents.prompts.sections.tools.base import (
    ToolMetadataProvider,
)

DESCRIPTION: Dict[str, str] = {
    "cn": "执行 Shell 命令。",
    "en": "Execute shell commands.",
}

BASH_PARAMS: Dict[str, Dict[str, str]] = {
    "command": {
        "cn": "要执行的 Shell 命令",
        "en": "Shell command to execute",
    },
    "timeout": {
        "cn": "超时时间（秒），默认 30",
        "en": "Timeout in seconds, default 30",
    },
}


def get_bash_input_params(language: str = "cn") -> Dict[str, Any]:
    """Return the full JSON Schema for bash tool input_params."""
    p = BASH_PARAMS
    return {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": p["command"].get(language, p["command"]["cn"])},
            "timeout": {"type": "integer", "description": p["timeout"].get(language, p["timeout"]["cn"])},
        },
        "required": ["command"],
    }


class BashMetadataProvider(ToolMetadataProvider):
    """Bash 工具的元数据 provider。"""

    def get_name(self) -> str:
        return "bash"

    def get_description(self, language: str = "cn") -> str:
        return DESCRIPTION.get(language, DESCRIPTION["cn"])

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return get_bash_input_params(language)
