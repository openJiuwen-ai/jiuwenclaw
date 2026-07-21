from __future__ import annotations

import json
import secrets
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Any

from jiuwenavatar.common.utils import get_checkpoint_dir, logger


class SessionMapScope(str, Enum):
    """How SessionMap keys and agent session_id strings are derived from inbound identity."""

    # (default) One agent session per (provider, chat, bot); users in the same chat share context.
    PER_CHAT_BOT = "per_chat_bot"
    # One agent session per (provider, chat, bot, user)
    PER_CHAT_BOT_USER = "per_chat_bot_user"


def load_session_map_scope() -> SessionMapScope:
    default = SessionMapScope.PER_CHAT_BOT
    try:
        from jiuwenavatar.common.config import get_config

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


@dataclass
class SessionBinding:
    session_id: str
    service_id: str = ""
    agent_id: str = ""
    avatar_id: str = ""
    group_id: str = ""
    user_id: str = ""

    @classmethod
    def from_raw(cls, raw: Any) -> "SessionBinding | None":
        if isinstance(raw, str) and raw:
            return cls(session_id=raw)
        if isinstance(raw, dict):
            sid = str(raw.get("session_id") or "").strip()
            if not sid:
                return None
            return cls(
                session_id=sid,
                service_id=str(raw.get("service_id") or ""),
                agent_id=str(raw.get("agent_id") or ""),
                avatar_id=str(raw.get("avatar_id") or ""),
                group_id=str(raw.get("group_id") or ""),
                user_id=str(raw.get("user_id") or ""),
            )
        return None


class SessionMap:
    """Map stable identity (per config scope) -> rotating agent ``session_id``."""

    def __init__(self, *, scope: SessionMapScope | None = None) -> None:
        self._scope = scope if scope is not None else load_session_map_scope()
        self._store_path: Path = get_checkpoint_dir() / "session_map.json"
        self._mapping: dict[str, SessionBinding] = {}
        self._redis = self._try_create_redis_client()
        self._load()

    @staticmethod
    def _try_create_redis_client() -> Any | None:
        backend = ""
        try:
            from jiuwenavatar.common.enterprise import is_enterprise_mode

            backend = "redis" if is_enterprise_mode() else ""
        except Exception:
            backend = ""
        backend = (backend or __import__("os").getenv("GATEWAY_SESSION_MAP_BACKEND", "")).strip().lower()
        if backend != "redis":
            return None
        try:
            import os
            import redis  # type: ignore

            host = os.getenv("REDIS_HOST", "127.0.0.1")
            port = int(os.getenv("REDIS_PORT", "6379"))
            db = int(os.getenv("REDIS_DB", "0"))
            client = redis.Redis(host=host, port=port, db=db, decode_responses=True)
            client.ping()
            return client
        except Exception as exc:  # noqa: BLE001
            logger.warning("Redis SessionMap unavailable, falling back to file: %s", exc)
            return None

    def _redis_key_prefix(self) -> str:
        return f"sessionmap:{self._scope.value}:"

    def _load(self) -> None:
        if self._redis is not None:
            try:
                pattern = self._redis_key_prefix() + "*"
                for redis_key in self._redis.scan_iter(pattern):
                    raw = self._redis.get(redis_key)
                    if not raw:
                        continue
                    binding = SessionBinding.from_raw(json.loads(raw))
                    if binding is not None:
                        self._mapping[str(redis_key).removeprefix(self._redis_key_prefix())] = binding
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("SessionMap Redis load failed, falling back to file: %s", exc)
        try:
            if not self._store_path.exists():
                return
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                loaded: dict[str, SessionBinding] = {}
                for key, value in data.items():
                    binding = SessionBinding.from_raw(value)
                    if binding is not None:
                        loaded[str(key)] = binding
                self._mapping = loaded
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap load failed: %s", exc)

    def _save(self) -> None:
        if self._redis is not None:
            try:
                prefix = self._redis_key_prefix()
                pipe = self._redis.pipeline()
                for key, binding in self._mapping.items():
                    pipe.set(prefix + key, json.dumps(asdict(binding), ensure_ascii=False))
                pipe.execute()
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("SessionMap Redis save failed, falling back to file: %s", exc)
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(
                    {key: asdict(binding) for key, binding in self._mapping.items()},
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap save failed: %s", exc)

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
        existing = self._mapping.get(key)
        if existing and not rotate:
            return existing.session_id

        sid = _make_session_id(self._scope, provider, chat_id, bot_id, user_id)
        if existing and existing.session_id == sid:
            return sid
        self._mapping[key] = SessionBinding(session_id=sid, user_id=user_id)
        self._save()
        return sid

    def get_or_create_binding(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        service_id: str = "",
        agent_id: str = "",
        avatar_id: str = "",
        group_id: str = "",
        rotate: bool = False,
    ) -> SessionBinding:
        key = _make_key(self._scope, provider, chat_id, bot_id, user_id)
        existing = self._mapping.get(key)
        if existing and not rotate:
            changed = False
            for attr, value in {
                "service_id": service_id,
                "agent_id": agent_id,
                "avatar_id": avatar_id,
                "group_id": group_id,
                "user_id": user_id,
            }.items():
                if value and not getattr(existing, attr):
                    setattr(existing, attr, value)
                    changed = True
            if changed:
                self._save()
            return existing

        binding = SessionBinding(
            session_id=_make_session_id(self._scope, provider, chat_id, bot_id, user_id),
            service_id=service_id,
            agent_id=agent_id or user_id,
            avatar_id=avatar_id or bot_id,
            group_id=group_id,
            user_id=user_id,
        )
        self._mapping[key] = binding
        self._save()
        return binding
