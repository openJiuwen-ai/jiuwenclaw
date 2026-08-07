from __future__ import annotations

import os
from enum import Enum

from jiuwenswarm.common.utils import logger
from jiuwenswarm.extensions.redis.redis_runtime import get_declared_deployment_mode
from jiuwenswarm.gateway.routing.session_storage import (
    LocalSessionStorage,
    RedisSessionStorage,
    SessionStorage,
)


class SessionMapScope(str, Enum):
    """How SessionMap keys and agent session_id strings are derived from inbound identity."""

    # (default) One agent session per (provider, chat, bot); users in the same chat share context.
    PER_CHAT_BOT = "per_chat_bot"
    # One agent session per (provider, chat, bot, user)
    PER_CHAT_BOT_USER = "per_chat_bot_user"


def load_session_map_scope() -> SessionMapScope:
    default = SessionMapScope.PER_CHAT_BOT
    try:
        from jiuwenswarm.common.config import get_config

        raw = str((get_config().get("gateway") or {}).get("session_map_scope") or default.value).strip().lower()
        return SessionMapScope(raw)
    except ValueError:
        logger.warning("Unknown gateway.session_map_scope %r, using %s", raw, default.value)
        return default
    except Exception as exc:  # noqa: BLE001
        logger.warning("SessionMap scope load failed, using %s: %s", default.value, exc)
        return default


def _make_key(
    scope: SessionMapScope,
    provider: str,
    chat_id: str,
    bot_id: str,
    user_id: str,
) -> str:
    if scope == SessionMapScope.PER_CHAT_BOT:
        return f"{provider}::{chat_id}::{bot_id}"
    return f"{provider}::{chat_id}::{bot_id}::{user_id}"


class SessionMap:
    """Map stable identity (per config scope) -> rotating agent ``session_id``.

    支持两种部署模式:
    - standalone (单机模式): 所有读写走本地文件
    - distributed (主备模式，且 AGENT_RUNTIME 开启): 所有读写走 Redis
    """

    def __init__(self, *, scope: SessionMapScope | None = None) -> None:
        self._scope = scope if scope is not None else load_session_map_scope()
        self._storage: SessionStorage

        # 企业版：AGENT_RUNTIME + distributed 时使用 Redis；否则本地文件
        if (
            os.getenv("AGENT_RUNTIME", "").strip()
            and get_declared_deployment_mode() == "distributed"
        ):
            self._storage = RedisSessionStorage()
        else:
            self._storage = LocalSessionStorage()

    def get_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        rotate: bool = False,
    ) -> str:
        key = _make_key(self._scope, provider, chat_id, bot_id, user_id)
        existing = self._storage.get(key)
        if existing and not rotate:
            return existing
        raise RuntimeError(
            "SessionMap cannot allocate session IDs; use AgentServer session.create"
        )

    def find_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
    ) -> str | None:
        key = _make_key(self._scope, provider, chat_id, bot_id, user_id)
        return self._storage.get(key)

    def set_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        session_id: str,
    ) -> None:
        key = _make_key(self._scope, provider, chat_id, bot_id, user_id)
        sid = str(session_id).strip()
        if not sid:
            raise ValueError("session_id is required")
        if self._storage.get(key) != sid:
            self._storage.set(key, sid)
