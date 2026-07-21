# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Enterprise per-user configuration store."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jiuwenavatar.common.enterprise import safe_segment
from jiuwenavatar.common.postgres_json_store import (
    PostgresJsonStore,
    PostgresJsonStoreUnavailable,
    postgres_store_enabled,
)

logger = logging.getLogger(__name__)

_SENSITIVE_KEYS = {"gitcode_token"}


def is_sensitive_user_config_key(key: str) -> bool:
    normalized = str(key or "").strip().lower()
    return normalized in _SENSITIVE_KEYS or "api_key" in normalized or "token" in normalized


def _user_config_dir() -> Path:
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "enterprise" / "user_config"


class EnterpriseUserConfigStore:
    """Store config values scoped by enterprise group and user."""

    def __init__(self) -> None:
        self._pg_store: PostgresJsonStore | None = None
        if postgres_store_enabled():
            try:
                self._pg_store = PostgresJsonStore("enterprise_user_config")
            except PostgresJsonStoreUnavailable as exc:
                logger.warning("Enterprise user config PostgreSQL store unavailable, falling back to JSON: %s", exc)
            except Exception:
                logger.warning("Enterprise user config PostgreSQL store init failed, falling back to JSON", exc_info=True)

    @staticmethod
    def _crypto():
        try:
            from jiuwenavatar.extensions.registry import ExtensionRegistry

            return ExtensionRegistry.get_instance().get_crypto_provider()
        except Exception:
            return None

    def _encrypt(self, key: str, value: str) -> str:
        text = str(value or "")
        if not is_sensitive_user_config_key(key) or not text:
            return text
        crypto = self._crypto()
        if crypto is None:
            return text
        try:
            return crypto.encrypt(text)
        except Exception:
            return text

    def _decrypt(self, key: str, value: str) -> str:
        text = str(value or "")
        if not is_sensitive_user_config_key(key) or not text:
            return text
        crypto = self._crypto()
        if crypto is None:
            return text
        try:
            return crypto.decrypt(text)
        except Exception:
            return text

    @staticmethod
    def _doc_key(group_id: str, user_id: str) -> str:
        return f"{safe_segment(group_id, default='default-group')}::{safe_segment(user_id, default='default-user')}"

    def _json_path(self, group_id: str, user_id: str) -> Path:
        return (
            _user_config_dir()
            / safe_segment(group_id, default="default-group")
            / f"{safe_segment(user_id, default='default-user')}.json"
        )

    def load(self, group_id: str, user_id: str) -> dict[str, str]:
        group = str(group_id or "").strip()
        user = str(user_id or "").strip()
        if not group or not user:
            return {}
        if self._pg_store is not None:
            raw = self._pg_store.get(self._doc_key(group, user))
        else:
            path = self._json_path(group, user)
            if not path.is_file():
                return {}
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                logger.warning("Failed to read enterprise user config %s", path, exc_info=True)
                return {}
        if not isinstance(raw, dict):
            return {}
        values = raw.get("values") if isinstance(raw.get("values"), dict) else raw
        return {
            str(key): self._decrypt(str(key), str(value or ""))
            for key, value in values.items()
        }

    def save_updates(self, group_id: str, user_id: str, updates: dict[str, Any]) -> dict[str, str]:
        group = str(group_id or "").strip()
        user = str(user_id or "").strip()
        if not group or not user:
            raise ValueError("group_id and user_id are required")
        current = self.load(group, user)
        for key, value in updates.items():
            current[str(key)] = str(value or "").strip()
        payload = {
            "group_id": group,
            "user_id": user,
            "values": {
                key: self._encrypt(key, value)
                for key, value in current.items()
            },
        }
        if self._pg_store is not None:
            self._pg_store.save(self._doc_key(group, user), payload, group_id=group, owner_user_id=user)
        else:
            path = self._json_path(group, user)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return current


_STORE: EnterpriseUserConfigStore | None = None


def get_enterprise_user_config_store() -> EnterpriseUserConfigStore:
    global _STORE
    if _STORE is None:
        _STORE = EnterpriseUserConfigStore()
    return _STORE
