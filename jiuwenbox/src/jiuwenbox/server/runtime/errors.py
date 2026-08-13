# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Shared runtime exceptions."""

from __future__ import annotations


class BackgroundJobNotFoundError(Exception):
    """Raised when a background job id is unknown for a sandbox."""
