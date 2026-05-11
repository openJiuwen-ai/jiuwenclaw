# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel

from openjiuwen_runtime.foundation.db.table_def import (
    ColumnDefinition,
    IndexDefinition,
    TableDefinition,
)


class ModelConfigInfo(BaseModel):
    """model_config 表行映射（与 MODEL_CONFIG_TABLE_DEF 一致）。"""

    id: int
    model_name: str
    model_type: str
    api_endpoint: str
    api_key_ref: str
    parameters: Optional[dict[str, Any]] = None
    rate_limit: Optional[dict[str, Any]] = None
    enabled: bool
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ChannelConfigInfo(BaseModel):
    """channel_config 表行映射（与 CHANNEL_CONFIG_TABLE_DEF 一致）。"""

    id: int
    channel_id: str
    channel_name: str
    channel_type: str
    bot_id: str
    config: Optional[dict[str, Any]] = None
    status: str
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


MODEL_CONFIG_TABLE_DEF = TableDefinition(
    table_name="model_config",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("model_name", "string", length=128, nullable=False),
        ColumnDefinition("model_type", "string", length=32, nullable=False),
        ColumnDefinition("api_endpoint", "string", length=256, nullable=False),
        ColumnDefinition("api_key_ref", "string", length=64, nullable=False),
        ColumnDefinition("parameters", "json", nullable=True),
        ColumnDefinition("rate_limit", "json", nullable=True),
        ColumnDefinition("enabled", "boolean", nullable=False, default=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["model_type"], unique=False),
        IndexDefinition(["enabled"], unique=False),
    ],
)

CHANNEL_CONFIG_TABLE_DEF = TableDefinition(
    table_name="channel_config",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("channel_id", "string", length=64, nullable=False),
        ColumnDefinition("channel_name", "string", length=128, nullable=False),
        ColumnDefinition("channel_type", "string", length=32, nullable=False),
        ColumnDefinition("bot_id", "string", length=64, nullable=False),
        ColumnDefinition("config", "json", nullable=True),
        ColumnDefinition("status", "string", length=32, nullable=False),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["channel_id"], unique=True),
        IndexDefinition(["channel_type"], unique=False),
        IndexDefinition(["bot_id"], unique=False),
        IndexDefinition(["status"], unique=False),
    ],
)
