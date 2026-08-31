# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager → Gateway 同步请求解析（剥离历史信封字段，无签密）。"""

from __future__ import annotations

from typing import Any

# 历史信封 / 兼容字段，不进入业务落库
_LEGACY_KEYS = frozenset({"revision", "sig", "enc"})


def split_business(body: dict[str, Any]) -> dict[str, Any]:
    """返回业务字段；忽略历史 ``revision`` / ``sig`` / ``enc``。"""
    return {k: v for k, v in body.items() if k not in _LEGACY_KEYS}
