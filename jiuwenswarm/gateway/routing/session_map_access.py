# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionMap Repository process entry + sync SessionStorage adapter."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from jiuwenswarm.gateway.routing.session_map_repository import SessionMapRepository
from jiuwenswarm.gateway.routing.session_storage import SessionStorage
from jiuwenswarm.gateway.storage.async_bridge import run_awaitable

if TYPE_CHECKING:
    from jiuwenswarm.gateway.routing.session_map import Session

_repo: SessionMapRepository | None = None


def set_session_map_repository(repo: SessionMapRepository | None) -> None:
    global _repo
    _repo = repo


def get_session_map_repository() -> SessionMapRepository | None:
    return _repo


def clear_session_map_repository() -> None:
    set_session_map_repository(None)


class PersistentSessionStorage(SessionStorage):
    """Sync SessionStorage backed by SessionMapRepository (run_awaitable bridge)."""

    def __init__(self, repo: SessionMapRepository) -> None:
        self._repo = repo
        self._mapping: dict[str, Session] = {}
        self._lock = threading.Lock()
        self.load()

    def load(self) -> None:
        mapping = run_awaitable(self._repo.list_all())
        with self._lock:
            self._mapping = dict(mapping or {})

    def save(self, session: "Session") -> None:
        return None

    def get(self, identity_key: str) -> "Session | None":
        with self._lock:
            cached = self._mapping.get(identity_key)
        if cached is not None:
            return cached
        sess = run_awaitable(self._repo.get(identity_key))
        if sess is not None:
            with self._lock:
                self._mapping[identity_key] = sess
        return sess

    def set(self, identity_key: str, session: "Session") -> None:
        saved = run_awaitable(self._repo.upsert(identity_key, session))
        with self._lock:
            self._mapping[identity_key] = saved

    def remove(self, identity_key: str) -> None:
        run_awaitable(self._repo.delete(identity_key))
        with self._lock:
            self._mapping.pop(identity_key, None)

    def get_all(self) -> dict[str, "Session"]:
        with self._lock:
            return dict(self._mapping)


__all__ = [
    "PersistentSessionStorage",
    "clear_session_map_repository",
    "get_session_map_repository",
    "set_session_map_repository",
]
