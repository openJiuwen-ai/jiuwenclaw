from __future__ import annotations

import secrets
import time
from enum import Enum

from jiuwenclaw.utils import logger
from jiuwenclaw.extensions.redis.redis_runtime import get_declared_deployment_mode
from jiuwenclaw.gateway.session_storage import (
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
        from jiuwenclaw.config import get_config

        raw = str((get_config().get("gateway") or {}).get("session_map_scope") or default.value).strip().lower()
        return SessionMapScope(raw)
    except ValueError:
        logger.warning("Unknown gateway.session_map_scope %r, using %s", raw, default.value)
        return default
    except Exception as exc:  # noqa: BLE001
        logger.warning("SessionMap scope load failed, using %s: %s", default.value, exc)
        return default


def _make_session_id(
    scope: SessionMapScope,
    provider: str,
    chat_id: str,
    bot_id: str,
    user_id: str,
) -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    if scope == SessionMapScope.PER_CHAT_BOT:
        return f"{provider}::{chat_id}::{bot_id}::{ts}::{suffix}"
    return f"{provider}::{chat_id}::{bot_id}::{user_id}::{ts}::{suffix}"


class SessionMap:
    """Map stable identity (per config scope) -> rotating agent ``session_id``.

    支持两种部署模式:
    - standalone (单机模式): 所有读写走本地文件
    - distributed (主备模式): 所有读写走 Redis
    """

    def __init__(self, *, scope: SessionMapScope | None = None) -> None:
        self._scope = scope if scope is not None else load_session_map_scope()
        self._storage: SessionStorage

        if get_declared_deployment_mode() == "standalone":
            self._storage = LocalSessionStorage()
        else:
            self._storage = RedisSessionStorage()

    def get_identity_key(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
    ) -> str:
        if self._scope == SessionMapScope.PER_CHAT_BOT:
            return f"{provider}::{chat_id}::{bot_id}"
        return f"{provider}::{chat_id}::{bot_id}::{user_id}"

    def get_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        rotate: bool = False,
    ) -> str:
        key = self.get_identity_key(provider, chat_id, bot_id, user_id)
        existing = self._storage.get(key)

        if existing and not rotate:
            return existing

        sid = _make_session_id(self._scope, provider, chat_id, bot_id, user_id)
        if existing == sid:
            return sid
        self._storage.set(key, sid)
        return sid
