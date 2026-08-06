# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Subagent thinking control: semantic thinking → vendor kwargs."""

from __future__ import annotations

from jiuwenswarm.common.thinking.adapter import adapt_thinking
from jiuwenswarm.common.thinking.rail import ThinkingInjectRail
from jiuwenswarm.common.thinking.types import (
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
