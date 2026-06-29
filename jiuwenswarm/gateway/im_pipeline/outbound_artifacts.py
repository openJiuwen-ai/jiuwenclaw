"""Session-scoped structured artifacts waiting for an outbound final message."""

from __future__ import annotations

import asyncio
import copy
import time
from typing import Any


class OutboundArtifactStore:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}

    async def register(
        self,
        channel_id: str,
        session_id: str,
        delivery: dict[str, Any],
        *,
        ttl_seconds: float = 360.0,
    ) -> None:
        key = (str(channel_id or "").strip(), str(session_id or "").strip())
        if not all(key):
            return
        async with self._lock:
            self._pending[key] = (time.monotonic() + ttl_seconds, copy.deepcopy(delivery))
            self._purge_locked()

    async def consume(self, channel_id: str, session_id: str) -> dict[str, Any] | None:
        key = (str(channel_id or "").strip(), str(session_id or "").strip())
        if not all(key):
            return None
        async with self._lock:
            self._purge_locked()
            item = self._pending.pop(key, None)
            return copy.deepcopy(item[1]) if item is not None else None

    def _purge_locked(self) -> None:
        now = time.monotonic()
        for key, (expires_at, _) in list(self._pending.items()):
            if expires_at <= now:
                self._pending.pop(key, None)


outbound_artifact_store = OutboundArtifactStore()
