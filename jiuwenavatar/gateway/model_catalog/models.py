# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Data models for tenant model catalogs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CatalogModelEntry(BaseModel):
    """One model exposed to a tenant (group)."""

    id: str = Field(default_factory=lambda: f"model-{uuid.uuid4().hex[:8]}")
    model_type: str = Field("chat", description="Model usage: chat / claude_code / codex_cli")
    alias: str = Field("", description="Display name in UI")
    model_name: str = Field(..., description="Provider model id")
    model_provider: str = Field("OpenAI", description="Client provider name")
    api_base: str = Field("", description="Provider API base URL")
    api_key: str = Field("", description="Tenant-scoped API key (encrypted at rest when crypto enabled)")
    secret_ref: str = Field(
        "",
        description="Optional env var name for platform-managed secret; overrides api_key when set",
    )
    is_default: bool = Field(False, description="Default chat model for the tenant")
    enabled: bool = Field(True, description="Whether members can select this model")
    temperature: float = Field(0.95, description="Default sampling temperature")
    timeout: int = Field(1800, description="Provider request timeout in seconds")
    verify_ssl: bool = Field(False, description="Whether to verify provider TLS certificates")


class GroupModelCatalog(BaseModel):
    group_id: str
    models: list[CatalogModelEntry] = Field(default_factory=list)
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def enabled_models(self) -> list[CatalogModelEntry]:
        return [item for item in self.models if item.enabled and item.model_name.strip()]
