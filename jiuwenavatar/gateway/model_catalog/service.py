# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Tenant model catalog service — admin CRUD, member listing, runtime resolution."""

from __future__ import annotations

import copy
import logging
import os
import sys
import uuid
from datetime import datetime
from typing import Any

from jiuwenavatar.gateway.model_catalog.models import CatalogModelEntry, GroupModelCatalog
from jiuwenavatar.gateway.model_catalog.store import ModelCatalogStore

logger = logging.getLogger(__name__)

_SERVICE: "ModelCatalogService | None" = None
MODEL_TYPE_CHAT = "chat"
MODEL_TYPE_CLAUDE_CODE = "claude_code"
MODEL_TYPE_CODEX_CLI = "codex_cli"
_VALID_MODEL_TYPES = {MODEL_TYPE_CHAT, MODEL_TYPE_CLAUDE_CODE, MODEL_TYPE_CODEX_CLI}


def get_model_catalog_service() -> "ModelCatalogService":
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = ModelCatalogService()
    return _SERVICE


class ModelCatalogService:
    def __init__(self, store: ModelCatalogStore | None = None) -> None:
        self._store = store or ModelCatalogStore()

    # ------------------------------------------------------------------
    # Crypto helpers (same extension as config.yaml)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_model_type(value: Any) -> str:
        normalized = str(value or MODEL_TYPE_CHAT).strip().lower().replace("-", "_")
        return normalized if normalized in _VALID_MODEL_TYPES else MODEL_TYPE_CHAT

    @staticmethod
    def _crypto():
        reg_mod = sys.modules.get("jiuwenavatar.extensions.registry")
        if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
            try:
                return reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            except Exception:
                return None
        return None

    def _encrypt_secret(self, value: str) -> str:
        text = str(value or "")
        if not text:
            return ""
        crypto = self._crypto()
        if crypto is None:
            return text
        try:
            return crypto.encrypt(text)
        except Exception:
            return text

    def _decrypt_secret(self, value: str) -> str:
        text = str(value or "")
        if not text:
            return ""
        crypto = self._crypto()
        if crypto is None:
            return text
        try:
            return crypto.decrypt(text)
        except Exception:
            return text

    # ------------------------------------------------------------------
    # Catalog CRUD
    # ------------------------------------------------------------------

    def get_catalog(self, group_id: str, *, create_if_missing: bool = False) -> GroupModelCatalog | None:
        group = str(group_id or "").strip()
        if not group:
            return None
        catalog = self._store.load(group)
        if catalog is None and create_if_missing:
            catalog = self.bootstrap_from_platform(group)
        elif catalog is not None:
            normalized = self._store.normalize_entries(catalog.models)
            if normalized != catalog.models:
                catalog = catalog.model_copy(
                    update={
                        "models": normalized,
                        "updated_at": datetime.now().isoformat(),
                    },
                )
                self._persist_catalog(catalog, encrypt_keys=False)
        return catalog

    def bootstrap_from_platform(self, group_id: str) -> GroupModelCatalog:
        """Seed tenant catalog from platform models.defaults (deploy-time config)."""
        from jiuwenavatar.common.config import get_default_models
        from jiuwenavatar.common.enterprise import bind_tenant_context

        group = str(group_id or "").strip()
        entries: list[CatalogModelEntry] = []
        # Runtime calls may bootstrap while a tenant ContextVar is active. Clear it
        # here so the seed source is the platform/deployment defaults, not the
        # tenant catalog being created.
        with bind_tenant_context(None):
            platform_defaults = get_default_models()
        for idx, item in enumerate(platform_defaults):
            mcc = item.get("model_client_config") if isinstance(item, dict) else {}
            if not isinstance(mcc, dict):
                continue
            model_name = str(mcc.get("model_name") or "").strip()
            if not model_name:
                continue
            mco = item.get("model_config_obj") if isinstance(item, dict) else {}
            if not isinstance(mco, dict):
                mco = {}
            entries.append(
                CatalogModelEntry(
                    model_type=MODEL_TYPE_CHAT,
                    alias=str(item.get("alias") or model_name),
                    model_name=model_name,
                    model_provider=str(mcc.get("client_provider") or "OpenAI"),
                    api_base=str(mcc.get("api_base") or ""),
                    api_key=str(mcc.get("api_key") or ""),
                    is_default=bool(item.get("is_default")) if "is_default" in item else idx == 0,
                    enabled=True,
                    temperature=float(mco.get("temperature") or 0.95),
                    timeout=int(mcc.get("timeout") or 1800),
                    verify_ssl=bool(mcc.get("verify_ssl", False)),
                )
            )
        catalog = GroupModelCatalog(
            group_id=group,
            models=self._store.normalize_entries(entries),
            updated_at=datetime.now().isoformat(),
        )
        self._persist_catalog(catalog, encrypt_keys=True)
        logger.info("Bootstrapped model catalog for group %s with %d models", group, len(catalog.models))
        return catalog

    def _persist_catalog(self, catalog: GroupModelCatalog, *, encrypt_keys: bool) -> GroupModelCatalog:
        stored = catalog.model_copy(deep=True)
        if encrypt_keys:
            for item in stored.models:
                if item.secret_ref:
                    item.api_key = ""
                elif item.api_key:
                    item.api_key = self._encrypt_secret(item.api_key)
        self._store.save(stored)
        return catalog

    def save_catalog_entries(self, group_id: str, entries: list[CatalogModelEntry]) -> GroupModelCatalog:
        group = str(group_id or "").strip()
        if not group:
            raise ValueError("group_id is required")

        existing = self._store.load(group)
        existing_by_id = {item.id: item for item in (existing.models if existing else [])}
        merged: list[CatalogModelEntry] = []
        for entry in self._store.normalize_entries(entries):
            prior = existing_by_id.get(entry.id)
            api_key = entry.api_key
            if not api_key.strip() and prior is not None:
                api_key = self._decrypt_secret(prior.api_key)
            merged.append(entry.model_copy(update={"api_key": api_key}))

        catalog = GroupModelCatalog(
            group_id=group,
            models=merged,
            updated_at=datetime.now().isoformat(),
        )
        self._persist_catalog(catalog, encrypt_keys=True)
        return catalog

    def save_from_api_payload(self, group_id: str, models: list[dict[str, Any]]) -> GroupModelCatalog:
        entries: list[CatalogModelEntry] = []
        for raw in models:
            if not isinstance(raw, dict):
                continue
            model_name = str(raw.get("model_name") or raw.get("model") or "").strip()
            if not model_name:
                continue
            entry_id = str(raw.get("id") or raw.get("model_id") or "").strip()
            try:
                temperature = float(raw.get("temperature") or 0.95)
            except (TypeError, ValueError):
                temperature = 0.95
            try:
                timeout = int(raw.get("timeout") or 1800)
            except (TypeError, ValueError):
                timeout = 1800
            entries.append(
                CatalogModelEntry(
                    id=entry_id or f"model-{uuid.uuid4().hex[:8]}",
                    model_type=self._normalize_model_type(raw.get("model_type") or raw.get("type")),
                    alias=str(raw.get("alias") or ""),
                    model_name=model_name,
                    model_provider=str(raw.get("model_provider") or raw.get("provider") or "OpenAI"),
                    api_base=str(raw.get("api_base") or ""),
                    api_key=str(raw.get("api_key") or ""),
                    secret_ref=str(raw.get("secret_ref") or ""),
                    is_default=bool(raw.get("is_default")),
                    enabled=raw.get("enabled", True) is not False,
                    temperature=temperature,
                    timeout=timeout,
                    verify_ssl=bool(raw.get("verify_ssl", False)),
                )
            )
        return self.save_catalog_entries(group_id, entries)

    # ------------------------------------------------------------------
    # API / runtime views
    # ------------------------------------------------------------------

    def _resolve_api_key(self, entry: CatalogModelEntry) -> str:
        secret_ref = str(entry.secret_ref or "").strip()
        if secret_ref:
            return os.getenv(secret_ref, "") or self._decrypt_secret(entry.api_key)
        return self._decrypt_secret(entry.api_key)

    def entry_to_runtime_dict(self, entry: CatalogModelEntry) -> dict[str, Any]:
        return {
            "model_type": self._normalize_model_type(entry.model_type),
            "alias": entry.alias,
            "is_default": entry.is_default,
            "model_client_config": {
                "api_base": entry.api_base,
                "api_key": self._resolve_api_key(entry),
                "model_name": entry.model_name,
                "client_provider": entry.model_provider,
                "timeout": entry.timeout,
                "verify_ssl": entry.verify_ssl,
            },
            "model_config_obj": {"temperature": entry.temperature},
        }

    def resolve_runtime_entries(self, group_id: str, *, model_type: str = MODEL_TYPE_CHAT) -> list[dict[str, Any]]:
        catalog = self.get_catalog(group_id, create_if_missing=True)
        if catalog is None:
            return []
        normalized_type = self._normalize_model_type(model_type)
        return [
            self.entry_to_runtime_dict(item)
            for item in catalog.enabled_models()
            if self._normalize_model_type(item.model_type) == normalized_type
        ]

    def resolve_coding_env(self, group_id: str, kind: str) -> dict[str, str]:
        model_type = {
            "claude-code": MODEL_TYPE_CLAUDE_CODE,
            "codex": MODEL_TYPE_CODEX_CLI,
        }.get(str(kind or "").strip().lower())
        if not model_type:
            return {}
        catalog = self.get_catalog(group_id, create_if_missing=True)
        if catalog is None:
            return {}
        entries = [
            item for item in catalog.enabled_models()
            if self._normalize_model_type(item.model_type) == model_type
        ]
        if not entries:
            return {}
        entry = next((item for item in entries if item.is_default), entries[0])
        api_key = self._resolve_api_key(entry)
        if model_type == MODEL_TYPE_CLAUDE_CODE:
            return {
                "ANTHROPIC_API_KEY": api_key,
                "ANTHROPIC_BASE_URL": entry.api_base,
                "ANTHROPIC_MODEL": entry.model_name,
            }
        return {
            "OPENAI_API_KEY": api_key,
            "OPENAI_BASE_URL": entry.api_base,
            "CODEX_MODEL": entry.model_name,
        }

    def list_api_models(
        self,
        group_id: str,
        *,
        include_secrets: bool,
        include_disabled: bool = False,
    ) -> list[dict[str, Any]]:
        catalog = self.get_catalog(group_id, create_if_missing=True)
        if catalog is None:
            return []

        result: list[dict[str, Any]] = []
        entries = catalog.models if include_disabled else catalog.enabled_models()
        for idx, entry in enumerate(entries):
            api_key = self._resolve_api_key(entry) if include_secrets else ""
            has_key = bool(self._resolve_api_key(entry))
            context_window_tokens = 0
            try:
                from openjiuwen.core.context_engine.context.context_utils import ContextUtils

                context_window_tokens = ContextUtils.resolve_context_max(model_name=entry.model_name)
            except Exception:
                logger.debug("Failed to resolve context window for %s", entry.model_name, exc_info=True)
            result.append(
                {
                    "id": entry.id,
                    "model_type": self._normalize_model_type(entry.model_type),
                    "model_name": entry.model_name,
                    "alias": entry.alias or entry.model_name,
                    "api_base": entry.api_base,
                    "api_key": api_key,
                    "has_key": has_key,
                    "model_provider": entry.model_provider,
                    "temperature": entry.temperature,
                    "timeout": entry.timeout,
                    "verify_ssl": entry.verify_ssl,
                    "is_default": entry.is_default,
                    "enabled": entry.enabled,
                    "secret_ref": entry.secret_ref if include_secrets else "",
                    "origin_index": idx,
                    "context_window_tokens": context_window_tokens,
                }
            )
        return result

    def sanitize_config_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Remove sensitive keys from config.get for enterprise members."""
        sensitive = (
            "api_key",
            "video_api_key",
            "audio_api_key",
            "vision_api_key",
            "embed_api_key",
            "anthropic_api_key",
            "openai_api_key",
            "jina_api_key",
            "bocha_api_key",
            "perplexity_api_key",
            "serper_api_key",
        )
        out = copy.deepcopy(payload)
        for key in sensitive:
            if key in out and out[key]:
                out[key] = ""
        return out
