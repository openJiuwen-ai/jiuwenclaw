# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""DeepAgent prompt system — language resolution and unified exports."""
from __future__ import annotations

import os
from typing import Optional

from openjiuwen.deepagents.prompts.builder import (
    DEFAULT_LANGUAGE,
    SUPPORTED_LANGUAGES,
    PromptMode,
    PromptSection,
    SystemPromptBuilder,
)
from openjiuwen.deepagents.prompts.report import PromptReport
from openjiuwen.deepagents.prompts.sanitize import sanitize_path, sanitize_user_content
from openjiuwen.deepagents.prompts import sections  # noqa: F401 — expose subpackage


def resolve_language(config_language: Optional[str] = None) -> str:
    """Resolve prompt language. Priority: config param > env var > default."""
    if config_language is not None and config_language in SUPPORTED_LANGUAGES:
        return config_language
    env_lang = os.environ.get("AGENT_PROMPT_LANGUAGE")
    if env_lang in SUPPORTED_LANGUAGES:
        return env_lang
    return DEFAULT_LANGUAGE


def resolve_mode(config_mode: Optional[str] = None) -> PromptMode:
    """Resolve prompt mode. Default: FULL."""
    if config_mode is not None:
        try:
            return PromptMode(config_mode)
        except ValueError:
            pass
    return PromptMode.FULL


__all__ = [
    "SUPPORTED_LANGUAGES",
    "DEFAULT_LANGUAGE",
    "resolve_language",
    "resolve_mode",
    "PromptMode",
    "PromptSection",
    "PromptReport",
    "SystemPromptBuilder",
    "sanitize_path",
    "sanitize_user_content",
    "sections",
]
