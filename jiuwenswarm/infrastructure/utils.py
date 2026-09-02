# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""跨模块基础设施工具（时间等）。"""

from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """返回当前 UTC 时间（带 ``timezone.utc`` 的 aware ``datetime``）。"""
    return datetime.now(timezone.utc)
