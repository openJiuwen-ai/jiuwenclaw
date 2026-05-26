from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from jiuwenclaw.extensions.redis.redis_runtime import get_declared_deployment_mode
from jiuwenclaw.gateway.session_storage import (
    LocalSessionStorage,
    RedisSessionStorage,
    SessionStorage,
)

if TYPE_CHECKING:
    pass


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
        from jiuwenclaw.utils import logger
        logger.warning("Unknown gateway.session_map_scope %r, using %s", raw, default.value)
        return default
    except Exception as exc:  # noqa: BLE001
        from jiuwenclaw.utils import logger
        logger.warning("SessionMap scope load failed, using %s: %s", default.value, exc)
        return default


def invoke_service_id(chat_id: str, bot_id: str) -> str:
    """Stable runtime ``service_id`` from chat + bot: ``md5(chat_id + "::" + bot_id)``.

    Used wherever SessionMap-backed traffic maps to a deployable service key (HTTP
    invocations, runtime management, etc.).
    """
    return hashlib.md5("::".join((chat_id, bot_id)).encode("utf-8")).hexdigest()


def invoke_ids_from_identity(
    chat_id: str,
    bot_id: str,
    user_id: str,
    scope: SessionMapScope,
) -> tuple[str, str | None]:
    """Derive ``(service_id, agent_id)`` from stable IM identity and SessionMap scope (gateway source of truth)."""
    sid = invoke_service_id(chat_id, bot_id)
    if scope == SessionMapScope.PER_CHAT_BOT_USER:
        return sid, user_id
    return sid, None


def invoke_ids_from_session_id_string(session_id: str) -> tuple[str, str | None]:
    """Derive ``(service_id, agent_id)`` from SessionMap-shaped agent ``session_id`` string.

    For migration or client-side fallback parsing only.
    """
    parts = session_id.split("::")
    if len(parts) == 6:
        _provider, chat_id, bot_id, user_id, _ts, _suffix = parts
        return invoke_service_id(chat_id, bot_id), user_id
    if len(parts) == 5:
        _provider, chat_id, bot_id, _ts, _suffix = parts
        return invoke_service_id(chat_id, bot_id), None
    return hashlib.md5(session_id.encode("utf-8")).hexdigest(), None


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


@dataclass(frozen=True)
class Session:
    """Gateway-persisted agent session row: ``session_id`` plus runtime ``service_id`` / ``agent_id``."""

    session_id: str
    service_id: str
    agent_id: str | None


def _session_from_stored_value(raw: Any) -> Session | None:
    if isinstance(raw, str) and raw.strip():
        sid = raw.strip()
        svc, aid = invoke_ids_from_session_id_string(sid)
        return Session(session_id=sid, service_id=svc, agent_id=aid)
    if isinstance(raw, dict):
        sid = str(raw.get("session_id") or "").strip()
        if not sid:
            return None
        svc = str(raw.get("service_id") or "").strip()
        aid_raw = raw.get("agent_id")
        if aid_raw is None or aid_raw == "":
            aid: str | None = None
        else:
            aid = str(aid_raw).strip() or None
        if not svc:
            svc, aid = invoke_ids_from_session_id_string(sid)
        return Session(session_id=sid, service_id=svc, agent_id=aid)
    return None


def _session_to_json_dict(sess: Session) -> dict[str, Any]:
    out: dict[str, Any] = {
        "session_id": sess.session_id,
        "service_id": sess.service_id,
    }
    if sess.agent_id is not None:
        out["agent_id"] = sess.agent_id
    else:
        out["agent_id"] = None
    return out


class SessionMap:
    """Map stable identity (per config scope) -> :class:`Session` (agent ``session_id`` + invoke ids)."""

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
        """Return the stable identity key for the configured scope."""
        if self._scope == SessionMapScope.PER_CHAT_BOT:
            return f"{provider}::{chat_id}::{bot_id}"
        return f"{provider}::{chat_id}::{bot_id}::{user_id}"

    def get_session(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        rotate: bool = False,
    ) -> Session:
        key = self.get_identity_key(provider, chat_id, bot_id, user_id)
        existing = self._storage.get(key)
        if existing and not rotate:
            return existing

        svc, aid = invoke_ids_from_identity(chat_id, bot_id, user_id, self._scope)
        new_sid = _make_session_id(self._scope, provider, chat_id, bot_id, user_id)
        sess = Session(session_id=new_sid, service_id=svc, agent_id=aid)
        self._storage.set(key, sess)
        return sess

    def reload(self) -> None:
        """Reload all sessions from storage backend (called when promoted to PRIMARY)."""
        self._storage.load()