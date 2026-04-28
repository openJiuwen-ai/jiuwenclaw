# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AK/SK 鉴权模块 — VibeSkillChannel HTTP & WebSocket 通用鉴权（stub 实现）。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def check_ws_auth(auth_enabled: bool, query_params: dict) -> tuple[bool, str]:
    """WebSocket 鉴权入口（stub 实现，直接返回通过）。

    Args:
        auth_enabled: 是否开启鉴权。
        query_params: parse_qs 返回的参数字典。

    Returns:
        (ok=True, error_message="")
    """
    return True, ""


def check_http_auth(auth_enabled: bool, headers: dict) -> tuple[bool, str]:
    """HTTP 鉴权入口（stub 实现，直接返回通过）。

    Args:
        auth_enabled: 是否开启鉴权。
        headers: HTTP headers 字典。

    Returns:
        (ok=True, error_message="")
    """
    return True, ""