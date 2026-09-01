# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved

"""Web 会话历史公开同步 API（http.server / fastapi 线程用）。"""

from __future__ import annotations

import threading
from typing import Any

from .store import ChatHistoryStore

_default_store: ChatHistoryStore | None = None
_default_store_lock = threading.Lock()


def get_default_store() -> ChatHistoryStore:
    """进程内单例，避免 HTTP 每次请求重建库。"""
    global _default_store
    with _default_store_lock:
        if _default_store is None:
            _default_store = ChatHistoryStore.from_env()
        return _default_store


def set_default_store(store: ChatHistoryStore) -> None:
    global _default_store
    with _default_store_lock:
        _default_store = store


def _coerce_store(store: Any | None) -> ChatHistoryStore | None:
    if store is None:
        st = get_default_store()
        return st if st.available else None
    if isinstance(store, ChatHistoryStore):
        return store if store.available else None
    return None


def list_sessions_sync(
    store: ChatHistoryStore | None = None,
    *,
    limit: int = 20,
    offset: int = 0,
    user: str | None = None,
) -> list[dict[str, Any]]:
    """同步读会话列表（http.server 线程用）。库不可用返回空。"""
    st = _coerce_store(store)
    if st is None:
        return []
    return st.list_sessions_blocking(limit=limit, offset=offset, user=user)


def get_session_detail_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> dict[str, Any] | None:
    """同步读会话详情。"""
    if not session_id:
        return None
    st = _coerce_store(store)
    if st is None:
        return None
    return st.get_session_detail_blocking(str(session_id), user=user)


def delete_session_sync(
    session_id: str,
    store: ChatHistoryStore | None = None,
    *,
    user: str | None = None,
) -> bool:
    """同步删除会话（sessions 行 + messages 行）。"""
    if not session_id:
        return False
    st = _coerce_store(store)
    if st is None:
        return False
    return st.delete_session_blocking(str(session_id), user=user)
