# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Trigger store — 触发器持久化存储."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from jiuwenavatar.common.postgres_json_store import (
    PostgresJsonStore,
    PostgresJsonStoreUnavailable,
    postgres_store_enabled,
)
from jiuwenavatar.gateway.trigger.models import TriggerConfig

logger = logging.getLogger(__name__)

_TRIGGERS_JSON = "triggers.json"


def _get_triggers_dir() -> Path:
    """Get the triggers storage directory."""
    from jiuwenavatar.common.utils import get_user_workspace_dir

    return get_user_workspace_dir() / "triggers"


def _get_triggers_json_path() -> Path:
    """Get the triggers.json file path."""
    return _get_triggers_dir() / _TRIGGERS_JSON


class TriggerStore:
    """Persist trigger configs to triggers.json in user workspace."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or _get_triggers_json_path()
        self._pg_store: PostgresJsonStore | None = None
        if path is None and postgres_store_enabled():
            try:
                self._pg_store = PostgresJsonStore("triggers")
            except PostgresJsonStoreUnavailable as exc:
                logger.warning("Trigger PostgreSQL store unavailable, falling back to JSON: %s", exc)
            except Exception:
                logger.warning("Trigger PostgreSQL store init failed, falling back to JSON", exc_info=True)

    @property
    def path(self) -> Path:
        return self._path

    def _read_json(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("Failed to read triggers.json", exc_info=True)
            return {}

    def _write_json(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_triggers(
        self,
        *,
        group_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[TriggerConfig]:
        """List triggers, optionally scoped to a tenant."""
        if self._pg_store is not None:
            triggers: list[TriggerConfig] = []
            items = self._pg_store.list(
                group_id=group_id or None,
                owner_user_id=owner_user_id or None,
            )
            for item in items:
                try:
                    triggers.append(TriggerConfig(**item))
                except Exception:
                    logger.warning("Skipping invalid trigger entry from PostgreSQL", exc_info=True)
            return triggers

        data = self._read_json()
        triggers_raw = data.get("triggers") or []
        triggers: list[TriggerConfig] = []
        for item in triggers_raw:
            if not isinstance(item, dict):
                continue
            try:
                trigger = TriggerConfig(**item)
            except Exception:
                logger.warning("Skipping invalid trigger entry", exc_info=True)
                continue
            if group_id is not None and (trigger.group_id or "") != group_id:
                continue
            if owner_user_id is not None and (trigger.owner_user_id or "") != owner_user_id:
                continue
            triggers.append(trigger)
        return triggers

    def get_trigger(self, trigger_id: str) -> TriggerConfig | None:
        """Get a trigger by ID."""
        for t in self.list_triggers():
            if t.id == trigger_id:
                return t
        return None

    def save_trigger(self, trigger: TriggerConfig) -> None:
        """Create or update a trigger."""
        if self._pg_store is not None:
            self._pg_store.save(
                trigger.id,
                trigger.model_dump(),
                group_id=trigger.group_id or "",
                owner_user_id=trigger.owner_user_id or "",
            )
            return

        triggers = self.list_triggers()
        # Replace if exists
        found = False
        for i, t in enumerate(triggers):
            if t.id == trigger.id:
                triggers[i] = trigger
                found = True
                break
        if not found:
            triggers.append(trigger)
        self._write_json({"triggers": [t.model_dump() for t in triggers]})

    def delete_trigger(self, trigger_id: str) -> bool:
        """Delete a trigger by ID. Returns True if deleted."""
        if self._pg_store is not None:
            return self._pg_store.delete(trigger_id)

        triggers = self.list_triggers()
        new_triggers = [t for t in triggers if t.id != trigger_id]
        if len(new_triggers) == len(triggers):
            return False
        self._write_json({"triggers": [t.model_dump() for t in new_triggers]})
        return True

    def list_triggers_by_avatar(
        self,
        avatar_id: str,
        *,
        group_id: str | None = None,
        owner_user_id: str | None = None,
    ) -> list[TriggerConfig]:
        """List all triggers associated with an avatar."""
        return [
            t for t in self.list_triggers(group_id=group_id, owner_user_id=owner_user_id)
            if t.avatar_id == avatar_id
        ]
