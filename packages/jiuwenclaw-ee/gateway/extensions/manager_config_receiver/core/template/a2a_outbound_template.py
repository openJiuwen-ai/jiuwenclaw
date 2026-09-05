"""Manager-owned A2A outbound Agent projection."""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.gateway.a2a_manager.outbound.credentials import (
    A2AOutboundCredentialStore,
)
from jiuwenswarm.gateway.a2a_manager.outbound.models import sanitize_persisted_value
from jiuwenswarm.gateway.config.enterprise.tables.template_models import (
    A2A_OUTBOUND_TEMPLATE_TABLE_DEF,
)

from ...infrastructure.repository_access import require_enterprise_repository
from ...infrastructure.utils import parse_iso_datetime, utc_now
from ...schemas.template_schemas import (
    A2AOutboundTemplateCreateRequest,
    A2AOutboundTemplateUpdateRequest,
)

_TABLE = A2A_OUTBOUND_TEMPLATE_TABLE_DEF.table_name
logger = logging.getLogger(__name__)


def _template_id(value: Any) -> str:
    template_id = str(value or "").strip()
    if not template_id:
        raise ValueError("template_id is required")
    return template_id


class A2AOutboundTemplateService:
    def __init__(
        self, credential_store: A2AOutboundCredentialStore | None = None
    ) -> None:
        self._credentials = credential_store or A2AOutboundCredentialStore()

    def _restore_credential(
        self,
        template_id: str,
        old_ref: str | None,
        old_secret: str,
    ) -> None:
        current_ref = self._credentials.reference_for(template_id)
        if old_ref and old_secret:
            self._credentials.set_for_agent(template_id, old_secret)
        else:
            self._credentials.delete(current_ref)

    async def _upsert(self, template: dict[str, Any], *, full: bool) -> str:
        template_id = _template_id(template.get("template_id"))
        request = (
            A2AOutboundTemplateCreateRequest.model_validate(template)
            if full
            else A2AOutboundTemplateUpdateRequest.model_validate(
                {key: value for key, value in template.items() if key != "template_id"}
            )
        )
        repo = require_enterprise_repository(_TABLE)
        existing = await repo.get(template_id=template_id)
        old_ref = str(existing.get("credential_ref") or "") if existing else ""
        old_secret = self._credentials.get(old_ref) if old_ref else ""

        values = request.model_dump(
            exclude_unset=True,
            exclude={"credential", "template_id"},
        )
        for field in ("agent_card", "selected_interface", "data"):
            if field in values:
                values[field] = sanitize_persisted_value(values[field])
        operation = request.credential.operation if request.credential else "keep"
        if operation == "replace":
            values["credential_ref"] = self._credentials.set_for_agent(
                template_id, request.credential.value or ""
            )
        elif operation == "clear":
            self._credentials.delete(
                old_ref or self._credentials.reference_for(template_id)
            )
            values["credential_ref"] = None
        elif existing is not None:
            values["credential_ref"] = existing.get("credential_ref")

        now = utc_now()
        values["updated_at"] = parse_iso_datetime(values.get("updated_at")) or now
        try:
            if existing is None:
                await repo.create(
                    {
                        "template_id": template_id,
                        **values,
                        "created_at": now,
                    }
                )
            else:
                updated = await repo.update({"template_id": template_id}, values)
                if updated is None:
                    raise ValueError(
                        f"a2a outbound template template_id={template_id!r} not found"
                    )
        except Exception:
            if operation in {"replace", "clear"}:
                self._restore_credential(template_id, old_ref or None, old_secret)
            raise
        return template_id

    async def create(self, template: dict[str, Any]) -> dict[str, str]:
        if not isinstance(template, dict):
            raise ValueError("a2a_outbound_templates.create requires template object")
        template_id = await self._upsert(template, full=True)
        logger.info(
            "[ManagerConfigReceiver] a2a_outbound_templates upsert template_id=%s",
            template_id,
        )
        return {"template_id": template_id}

    async def update(self, template_id: str, updates: dict[str, Any]) -> None:
        tid = _template_id(template_id)
        if not isinstance(updates, dict) or not updates:
            raise ValueError("a2a_outbound_templates.update requires non-empty updates")
        repo = require_enterprise_repository(_TABLE)
        existing = await repo.get(template_id=tid)
        if existing is None:
            raise ValueError(f"a2a outbound template template_id={tid!r} not found")
        await self._upsert({"template_id": tid, **updates}, full=False)
        logger.info(
            "[ManagerConfigReceiver] a2a_outbound_templates update template_id=%s",
            tid,
        )

    async def delete(self, template_id: str) -> None:
        tid = _template_id(template_id)
        repo = require_enterprise_repository(_TABLE)
        existing = await repo.get(template_id=tid)
        credential_ref = (
            str(existing.get("credential_ref") or "")
            if existing
            else self._credentials.reference_for(tid)
        )
        old_secret = self._credentials.get(credential_ref)
        self._credentials.delete(credential_ref)
        try:
            if existing is not None:
                await repo.delete(template_id=tid)
        except Exception:
            if old_secret:
                self._credentials.set_for_agent(tid, old_secret)
            raise
        logger.info(
            "[ManagerConfigReceiver] a2a_outbound_templates delete template_id=%s",
            tid,
        )
