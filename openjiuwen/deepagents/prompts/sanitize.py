# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Prompt injection defense utilities."""
from __future__ import annotations

import re

_INJECTION_PATTERN = re.compile(r"[<>\{\}\[\]`\$]|\.{3,}|\\n|\\r")


def sanitize_path(path: str) -> str:
    """Sanitize user-controllable path strings.

    Removes special characters that could be used for prompt injection
    while preserving normal path separators.
    """
    return _INJECTION_PATTERN.sub("", path)


def sanitize_user_content(content: str, max_len: int = 2000) -> str:
    """Remove injection-prone characters from user content and cap length.

    Args:
        content: Raw user-provided text.
        max_len: Upper bound on returned string length.

    Returns:
        Sanitized string with dangerous characters stripped.
    """
    safe_text = _INJECTION_PATTERN.sub("", content)
    if len(safe_text) > max_len:
        safe_text = safe_text[:max_len]
    return safe_text
