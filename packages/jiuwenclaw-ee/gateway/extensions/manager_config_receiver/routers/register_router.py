# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例探活（Manager 主动健康检查）。"""

from __future__ import annotations

from fastapi import APIRouter

from ..infrastructure.utils import get_jiuwenclaw_id
from ..schemas.common_schemas import ResponseModel

register_router = APIRouter()


@register_router.get("/health", response_model=ResponseModel)
async def instance_health() -> ResponseModel:
    jid = get_jiuwenclaw_id()
    return ResponseModel(
        code=200,
        message="success",
        data={
            "status": "ok",
            "service_type": "gateway",
            "jiuwenclaw_id": jid,
            "configured": True,
        },
    )
