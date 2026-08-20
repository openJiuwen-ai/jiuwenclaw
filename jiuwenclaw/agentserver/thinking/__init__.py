# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Subagent thinking control: semantic thinking → vendor kwargs."""

from __future__ import annotations

from jiuwenclaw.agentserver.thinking.adapter import adapt_thinking
from jiuwenclaw.agentserver.thinking.rail import ThinkingInjectRail
from jiuwenclaw.agentserver.thinking.types import (
    THINKING_VALUES,
    ThinkingProfile,
    normalize_thinking,
)

__all__ = [
    "THINKING_VALUES",
    "ThinkingInjectRail",
    "ThinkingProfile",
    "adapt_thinking",
    "normalize_thinking",
]
