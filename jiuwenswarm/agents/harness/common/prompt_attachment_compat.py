# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Compatibility helpers for prompt attachment enum exports.

Older openjiuwen builds expose prompt_attachment_manager without
PromptAttachmentScope / PromptAttachmentKind. JiuwenSwarm only relies on their
string values, so keep imports stable across SDK versions by falling back to
str Enum definitions with the same values.
"""
from __future__ import annotations

from enum import Enum

try:
    from openjiuwen.harness.prompts.prompt_attachment_manager import (
        PromptAttachmentKind,
        PromptAttachmentScope,
    )
except ImportError:

    class PromptAttachmentScope(str, Enum):
        """Visibility lifecycle for a prompt attachment."""

        SESSION = "session"
        TURN = "turn"

    class PromptAttachmentKind(str, Enum):
        """Prompt attachment kind values used by JiuwenSwarm."""

        GENERIC = "generic"
        TEXT = "text"
        RUNTIME = "runtime"
        MEMORY = "memory"
        FILE = "file"
        TOOL = "tool"
        DIAGNOSTIC = "diagnostic"
        TODO_REMINDER = "todo_reminder"
        WORKSPACE_DELTA = "workspace_delta"


__all__ = ["PromptAttachmentKind", "PromptAttachmentScope"]