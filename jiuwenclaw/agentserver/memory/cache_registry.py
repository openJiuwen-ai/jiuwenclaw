# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Session-bound memory cache registry with fingerprint-scoped ref counting."""

from __future__ import annotations

import asyncio
import contextvars
from typing import Any

from jiuwenclaw.utils import logger

_MEMORY_FP_CV: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "memory_cache_fingerprint",
    default=None,
)

_REF_COUNTS: dict[str, int] = {}
_SESSION_BINDINGS: dict[str, str] = {}
_REGISTRY_LOCK = asyncio.Lock()


def build_memory_cache_key(
        agent_id: str,
        workspace_dir: str,
        fingerprint: str,
) -> str:
    return f"{agent_id}:{workspace_dir}:{fingerprint}"


def bind_memory_cache_fingerprint(fingerprint: str) -> contextvars.Token:
    return _MEMORY_FP_CV.set(fingerprint)


def reset_memory_cache_fingerprint(token: contextvars.Token) -> None:
    _MEMORY_FP_CV.reset(token)


def get_bound_memory_cache_fingerprint() -> str | None:
    return _MEMORY_FP_CV.get()


def get_memory_cache_ref_count(cache_key: str) -> int:
    return _REF_COUNTS.get(cache_key, 0)


def clear_memory_cache_registry() -> None:
    """Reset registry state (tests only)."""
    _REF_COUNTS.clear()
    _SESSION_BINDINGS.clear()


def _release_binding_locked(session_key: str) -> str | None:
    """Update bindings/refcounts under ``_REGISTRY_LOCK``.

    Returns a cache_key that reached ref count zero and should be closed
    outside the lock, or None when no close is required.
    """
    cache_key = _SESSION_BINDINGS.pop(session_key, None)
    if not cache_key:
        return None
    count = _REF_COUNTS.get(cache_key, 0) - 1
    if count <= 0:
        _REF_COUNTS.pop(cache_key, None)
        logger.debug(
            "[MemoryCacheRegistry] closed cache_key=%s after session=%s release",
            cache_key,
            session_key,
        )
        return cache_key
    _REF_COUNTS[cache_key] = count
    logger.debug(
        "[MemoryCacheRegistry] release session=%s cache_key=%s ref=%s",
        session_key,
        cache_key,
        count,
    )
    return None


async def _close_cache_keys(cache_keys: list[str]) -> None:
    for cache_key in dict.fromkeys(cache_keys):
        await close_memory_cache_entry(cache_key)


async def acquire_memory_cache_session(
        session_key: str,
        agent_id: str,
        workspace_dir: str,
        fingerprint: str,
) -> None:
    """Bind a session to a fingerprint-scoped cache key and increment ref count."""
    if not fingerprint:
        return
    cache_key = build_memory_cache_key(agent_id, workspace_dir, fingerprint)
    to_close: list[str] = []
    async with _REGISTRY_LOCK:
        current = _SESSION_BINDINGS.get(session_key)
        if current == cache_key:
            return
        if current is not None:
            closed_key = _release_binding_locked(session_key)
            if closed_key is not None:
                to_close.append(closed_key)
        _SESSION_BINDINGS[session_key] = cache_key
        _REF_COUNTS[cache_key] = _REF_COUNTS.get(cache_key, 0) + 1
        logger.debug(
            "[MemoryCacheRegistry] acquire session=%s cache_key=%s ref=%s",
            session_key,
            cache_key,
            _REF_COUNTS[cache_key],
        )
    await _close_cache_keys(to_close)


async def release_memory_cache_session(session_key: str) -> None:
    """Release session binding; close cached managers when ref count reaches zero."""
    to_close: list[str] = []
    async with _REGISTRY_LOCK:
        closed_key = _release_binding_locked(session_key)
        if closed_key is not None:
            to_close.append(closed_key)
    await _close_cache_keys(to_close)


async def close_memory_cache_entry(cache_key: str) -> None:
    """Close and remove index + wiki managers for a cache key."""
    from .manager import INDEX_CACHE, release_workspace_file_watcher
    from .wiki_manager import MemoryWikiManager

    manager = INDEX_CACHE.pop(cache_key, None)
    if manager is not None and not manager.closed:
        release_workspace_file_watcher(manager)
        await manager.close()

    wiki = MemoryWikiManager.pop_cached(cache_key)
    if wiki is not None and not wiki.is_closed:
        await wiki.close()


async def release_all_memory_cache_sessions() -> None:
    """Release every session binding (process shutdown / tests)."""
    to_close: list[str] = []
    async with _REGISTRY_LOCK:
        for session_key in list(_SESSION_BINDINGS):
            closed_key = _release_binding_locked(session_key)
            if closed_key is not None:
                to_close.append(closed_key)
    await _close_cache_keys(to_close)
