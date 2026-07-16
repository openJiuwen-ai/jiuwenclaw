"""TTL and dirty tracking for Celia L0/L1 fixed context."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Awaitable, Callable


@dataclass
class _CachedContext:
    value: str
    loaded_at: float
    dirty: bool = False


class FixedContextCache:
    def __init__(self, ttl_seconds: float = 30.0) -> None:
        self._ttl = ttl_seconds
        self._values: dict[str, _CachedContext] = {}

    async def get(self, key: str, loader: Callable[[], Awaitable[str]]) -> str:
        current = self._values.get(key)
        now = time.monotonic()
        if current and not current.dirty and now - current.loaded_at < self._ttl:
            return current.value
        value = await loader()
        self._values[key] = _CachedContext(value=value, loaded_at=now)
        return value

    def mark_dirty(self, key: str) -> None:
        if key in self._values:
            self._values[key].dirty = True

    def clear(self, key: str | None = None) -> None:
        if key is None:
            self._values.clear()
        else:
            self._values.pop(key, None)


_CACHE = FixedContextCache()


def get_fixed_context_cache() -> FixedContextCache:
    return _CACHE
