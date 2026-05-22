# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ChannelConfigCreateRequest(BaseModel):
    """创建渠道配置（WS ``channel_config.create`` 的 ``channel`` 对象）。"""

    model_config = ConfigDict(str_strip_whitespace=True)

    channel_id: str = Field(..., min_length=1)
    channel_name: str = Field(..., min_length=1)
    channel_type: str = Field(..., min_length=1)
    bot_id: str = Field(..., min_length=1)
    config: dict[str, Any] | None = None
    status: Literal["active", "inactive"] = "active"
