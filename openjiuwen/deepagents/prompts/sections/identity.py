# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Identity section for DeepAgent system prompt."""
from __future__ import annotations

from typing import Dict

from openjiuwen.deepagents.prompts.builder import PromptSection

IDENTITY: Dict[str, str] = {
    "cn": (
        "你是一个通用 AI 助手。请根据用户的需求，合理使用可用工具完成任务。\n"
        "在执行过程中保持目标聚焦，遇到问题时尝试不同策略。"
    ),
    "en": (
        "You are a general-purpose AI assistant. Use available tools to complete tasks based on user needs.\n"
        "Stay focused on the goal during execution and try different strategies when encountering problems."
    ),
}


def build_identity_section(language: str = "cn") -> PromptSection:
    """Build the identity prompt section."""
    _ = language
    return PromptSection(
        name="identity",
        content=IDENTITY,
        priority=10,
    )
