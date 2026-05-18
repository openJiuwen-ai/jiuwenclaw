"""模型模板 model_template 业务逻辑。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx
from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.models.schemas import (
    ModelTemplateCreateBody,
    ModelTemplateOut,
    ModelTemplateUpdateBody,
)
from jiuwenclaw_manager.repositories.instance_repo import InstanceRepository
from jiuwenclaw_manager.repositories.template_repo import ModelTemplateRepository
from jiuwenclaw_manager.core.gateway_forward import (
    EnterpriseGatewayForward,
    forward_headers,
    forward_upstream_data,
    forward_upstream_data_or_none,
    normalize_list_page,
)

_ALLOWED_MODEL_TYPES = frozenset({"default", "video", "audio", "vision"})


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat().replace("+00:00", "Z")


def _validate_model_type(value: str | list[str]) -> str | list[str]:
    if isinstance(value, str):
        if value not in _ALLOWED_MODEL_TYPES:
            raise ValueError(
                f"model_type must be one of {sorted(_ALLOWED_MODEL_TYPES)}, got {value!r}"
            )
        return value
    if isinstance(value, list):
        if not value:
            raise ValueError("model_type list cannot be empty")
        for item in value:
            if item not in _ALLOWED_MODEL_TYPES:
                raise ValueError(
                    f"model_type entries must be in {sorted(_ALLOWED_MODEL_TYPES)}, got {item!r}"
                )
        return value
    raise ValueError("model_type must be a string or a list of strings")


def _row_to_out(row: Any) -> ModelTemplateOut:
    model_type = row.model_type
    if not isinstance(model_type, (str, list)):
        model_type = str(model_type)
    model_tags = row.model_tags
    if model_tags is not None and not isinstance(model_tags, list):
        model_tags = list(model_tags) if model_tags else None
    return ModelTemplateOut(
        id=row.id,
        jiuwenclaw_id=row.jiuwenclaw_id,
        display_name=row.display_name,
        description=row.description,
        model_type=model_type,
        model_tags=model_tags,
        api_base=row.api_base,
        api_key=row.api_key,
        model_id=row.model_id,
        model_provider=row.model_provider,
        parameters=row.parameters,
        timeout=row.timeout,
        retry_count=row.retry_count,
        enable_streaming=row.enable_streaming,
        enable_function_calling=row.enable_function_calling,
        verify_ssl=row.verify_ssl,
        enabled=row.enabled,
        data=row.data,
        created_at=_iso(row.created_at),
        updated_at=_iso(row.updated_at),
    )


def _out_from_gateway(data: dict[str, Any]) -> ModelTemplateOut:
    return ModelTemplateOut.model_validate(data)


class ModelTemplateService:
    def __init__(
        self,
        handler: DBHandler,
        http_client: httpx.AsyncClient,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._handler = handler
        self._repo = ModelTemplateRepository(handler)
        self._instance_repo = InstanceRepository(handler)
        self._gw = EnterpriseGatewayForward(
            handler, http_client, extra_headers or forward_headers()
        )

    async def _validate_jiuwenclaw_id(self, jiuwenclaw_id: str) -> str:
        normalized = jiuwenclaw_id.strip()
        if not normalized:
            raise ValueError("jiuwenclaw_id is required")
        inst = await self._instance_repo.get(normalized)
        if inst is None:
            raise ValueError(f"unknown jiuwenclaw_id={normalized!r}")
        return normalized

    def _build_row_for_create(
        self, normalized: str, body: ModelTemplateCreateBody, *, template_id: int
    ) -> dict[str, Any]:
        model_type = _validate_model_type(body.model_type)
        return {
            "id": template_id,
            "jiuwenclaw_id": normalized,
            "display_name": body.display_name.strip(),
            "description": body.description,
            "model_type": model_type,
            "model_tags": body.model_tags,
            "api_base": body.api_base.strip(),
            "api_key": body.api_key,
            "model_id": body.model_id.strip(),
            "model_provider": body.model_provider.strip(),
            "parameters": body.parameters,
            "timeout": body.timeout,
            "retry_count": body.retry_count,
            "enable_streaming": body.enable_streaming,
            "enable_function_calling": body.enable_function_calling,
            "verify_ssl": body.verify_ssl,
            "enabled": body.enabled,
            "data": body.data,
        }

    async def create(self, jiuwenclaw_id: str, body: ModelTemplateCreateBody) -> ModelTemplateOut:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        _validate_model_type(body.model_type)
        gw_data = forward_upstream_data(
            await self._gw.create_model_template(
                normalized, body.model_dump(mode="json")
            )
        )
        if not isinstance(gw_data, dict) or gw_data.get("id") is None:
            raise ValueError("gateway create model template returned no id")
        row = self._build_row_for_create(
            normalized, body, template_id=int(gw_data["id"])
        )
        created = await self._repo.create(row)
        return _row_to_out(created)

    async def get(self, jiuwenclaw_id: str, template_id: int) -> ModelTemplateOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.get_model_template(normalized, template_id)
        )
        if gw_data is None:
            return None
        if not isinstance(gw_data, dict):
            raise ValueError("invalid gateway response for model template")
        return _out_from_gateway(gw_data)

    async def list_templates(
        self,
        jiuwenclaw_id: str,
        *,
        page: int,
        page_size: int,
        enabled: bool | None,
        model_type: str | None,
    ) -> dict[str, Any]:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        gw_data = forward_upstream_data(
            await self._gw.list_model_templates(
                normalized,
                enabled=enabled,
                model_type=model_type,
                page_num=page,
                page_size=page_size,
            )
        )
        if not isinstance(gw_data, dict):
            return normalize_list_page(None, page=page, page_size=page_size)
        return normalize_list_page(gw_data, page=page, page_size=page_size)

    async def update(
        self, jiuwenclaw_id: str, template_id: int, body: ModelTemplateUpdateBody
    ) -> ModelTemplateOut | None:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)

        updates = body.model_dump(exclude_unset=True)
        if "model_type" in updates and updates["model_type"] is not None:
            updates["model_type"] = _validate_model_type(updates["model_type"])
        if "display_name" in updates and updates["display_name"] is not None:
            updates["display_name"] = updates["display_name"].strip()
        if "api_base" in updates and updates["api_base"] is not None:
            updates["api_base"] = updates["api_base"].strip()
        if "model_id" in updates and updates["model_id"] is not None:
            updates["model_id"] = updates["model_id"].strip()
        if "model_provider" in updates and updates["model_provider"] is not None:
            updates["model_provider"] = updates["model_provider"].strip()

        gw_data = forward_upstream_data_or_none(
            await self._gw.update_model_template(
                normalized, template_id, updates
            )
        )
        if gw_data is None:
            return None

        row = await self._repo.update(normalized, template_id, updates)
        if row is None:
            if not isinstance(gw_data, dict):
                raise ValueError("invalid gateway response for model template")
            return _out_from_gateway(gw_data)
        return _row_to_out(row)

    async def delete(self, jiuwenclaw_id: str, template_id: int) -> bool:
        normalized = await self._validate_jiuwenclaw_id(jiuwenclaw_id)
        gw_data = forward_upstream_data_or_none(
            await self._gw.delete_model_template(normalized, template_id)
        )
        if gw_data is None:
            return False
        return await self._repo.delete(normalized, template_id)
