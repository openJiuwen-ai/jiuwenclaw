# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Persistence for tenant model catalogs."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from jiuwenavatar.common.postgres_json_store import (
    PostgresJsonStore,
    PostgresJsonStoreUnavailable,
    postgres_store_enabled,
)
from jiuwenavatar.gateway.model_catalog.models import CatalogModelEntry, GroupModelCatalog

logger = logging.getLogger(__name__)


def _catalog_dir() -> Path:
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "enterprise" / "model_catalog"


class ModelCatalogStore:
    def __init__(self) -> None:
        self._pg_store: PostgresJsonStore | None = None
        if postgres_store_enabled():
            try:
                self._pg_store = PostgresJsonStore("model_catalog")
            except PostgresJsonStoreUnavailable as exc:
                logger.warning("Model catalog PostgreSQL store unavailable, falling back to JSON: %s", exc)
            except Exception:
                logger.warning("Model catalog PostgreSQL store init failed, falling back to JSON", exc_info=True)

    def _json_path(self, group_id: str) -> Path:
        safe = group_id.replace("/", "_").replace("\\", "_").strip() or "default"
        return _catalog_dir() / f"{safe}.json"

    def load(self, group_id: str) -> GroupModelCatalog | None:
        group = str(group_id or "").strip()
        if not group:
            return None

        if self._pg_store is not None:
            raw = self._pg_store.get(group)
            if not isinstance(raw, dict):
                return None
            try:
                return GroupModelCatalog.model_validate(raw)
            except Exception:
                logger.warning("Invalid model catalog in PostgreSQL for group %s", group, exc_info=True)
                return None

        path = self._json_path(group)
        if not path.is_file():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return GroupModelCatalog.model_validate(raw)
        except Exception:
            logger.warning("Failed to read model catalog %s", path, exc_info=True)
            return None

    def save(self, catalog: GroupModelCatalog) -> None:
        group = str(catalog.group_id or "").strip()
        if not group:
            raise ValueError("group_id is required")

        payload = catalog.model_dump()
        if self._pg_store is not None:
            self._pg_store.save(group, payload, group_id=group, owner_user_id="")
            return

        path = self._json_path(group)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def delete(self, group_id: str) -> bool:
        group = str(group_id or "").strip()
        if not group:
            return False
        if self._pg_store is not None:
            return self._pg_store.delete(group)
        path = self._json_path(group)
        if path.is_file():
            path.unlink()
            return True
        return False

    @staticmethod
    def normalize_entries(entries: list[CatalogModelEntry]) -> list[CatalogModelEntry]:
        valid_entries = [item for item in entries if item.model_name.strip()]
        if not valid_entries:
            return []

        grouped: dict[str, list[int]] = {}
        for idx, item in enumerate(valid_entries):
            model_type = (item.model_type or "chat").strip().lower() or "chat"
            grouped.setdefault(f"{model_type}::{item.model_name}", []).append(idx)

        normalized = list(valid_entries)
        for indices in grouped.values():
            has_default = any(normalized[idx].is_default for idx in indices)
            if not has_default:
                first_idx = indices[0]
                normalized[first_idx] = normalized[first_idx].model_copy(update={"is_default": True})
                for idx in indices[1:]:
                    normalized[idx] = normalized[idx].model_copy(update={"is_default": False})
                continue

            first = True
            for idx in indices:
                item = normalized[idx]
                if item.is_default and first:
                    first = False
                elif item.is_default:
                    normalized[idx] = item.model_copy(update={"is_default": False})
        return normalized
