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


class ResourceConfigInfo(BaseModel):
    """resource_config 表行映射（与 RESOURCE_CONFIG_TABLE_DEF 一致）。"""

    id: int
    component: str
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    storage_request: Optional[str] = None
    data: Optional[dict[str, Any]] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


RESOURCE_CONFIG_TABLE_DEF = TableDefinition(
    table_name="resource_config",
    columns=[
        ColumnDefinition(
            "id",
            "integer",
            primary_key=True,
            autoincrement=True,
            nullable=False,
        ),
        ColumnDefinition("component", "string", length=32, nullable=False),
        ColumnDefinition("cpu_request", "string", length=16, nullable=False),
        ColumnDefinition("cpu_limit", "string", length=16, nullable=False),
        ColumnDefinition("memory_request", "string", length=16, nullable=False),
        ColumnDefinition("memory_limit", "string", length=16, nullable=False),
        ColumnDefinition("storage_request", "string", length=16, nullable=True),
        ColumnDefinition("data", "json", nullable=True),
        ColumnDefinition("created_at", "datetime", nullable=False),
        ColumnDefinition("updated_at", "datetime", nullable=False),
    ],
    indexes=[
        IndexDefinition(["component"], unique=True),
    ],
)
