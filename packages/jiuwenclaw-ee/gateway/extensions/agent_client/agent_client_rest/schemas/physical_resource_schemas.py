from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ResourceConfigUpdateRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    component: str = Field(..., min_length=1, description="组件标识，必填")
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    storage_request: str | None = None


class ResourceConfigRecord(BaseModel):
    id: int
    component: str
    cpu_request: str
    cpu_limit: str
    memory_request: str
    memory_limit: str
    storage_request: str | None = None
    created_at: datetime | str
    updated_at: datetime | str
