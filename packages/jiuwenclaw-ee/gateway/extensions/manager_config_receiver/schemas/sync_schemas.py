# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Manager → Gateway 同步请求信封（仅 revision）与业务 Body 组合。"""

from __future__ import annotations

from typing import TypeVar

from pydantic import BaseModel, ConfigDict, Field, create_model

T = TypeVar("T", bound=BaseModel)


class SyncEnvelopeBase(BaseModel):
    """所有写同步接口公共信封字段（与业务字段同级合并）。"""

    model_config = ConfigDict(extra="ignore")

    revision: str = Field(..., min_length=1, description="配置版本号，单调递增")


def make_sync_body(name: str, business: type[BaseModel] | None = None) -> type[BaseModel]:
    """生成 ``SyncEnvelope + 业务模型``，供 FastAPI / OpenAPI 展示完整 request body。"""
    if business is None:
        return create_model(name, __base__=SyncEnvelopeBase)
    return create_model(name, __base__=(SyncEnvelopeBase, business))


# 仅信封（DELETE 等无业务字段）
SyncEnvelopeOnlyBody = make_sync_body("SyncEnvelopeOnlyBody")
