from __future__ import annotations

import json
import secrets
import time
from pathlib import Path
from typing import Any

from jiuwenclaw.utils import get_checkpoint_dir, logger


def _make_key(provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
    return f"{provider}::{chat_id}::{bot_id}::{user_id}"


def _make_base_session_id() -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"{ts}_{suffix}"


def _make_tuple_base_key(tuple_key: str, base_id: str) -> str:
    return f"{tuple_key}::{base_id}"


def _make_session_id(base_id: str, provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
    return f"{base_id}_{provider}_{chat_id}_{bot_id}_{user_id}"


class SessionMap:
    """Two-level map: tuple -> base_id -> session_id."""

    def __init__(self) -> None:
        self._store_path: Path = get_checkpoint_dir() / "session_map.json"
        self._tuple_to_base: dict[str, str] = {}
        self._tuple_base_to_session: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self._store_path.exists():
                return
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return

            tuple_to_base = data.get("tuple_to_base")
            tuple_base_to_session = data.get("tuple_base_to_session")
            if isinstance(tuple_to_base, dict) and isinstance(tuple_base_to_session, dict):
                self._tuple_to_base = {
                    str(k): str(v) for k, v in tuple_to_base.items() if isinstance(v, str) and v
                }
                self._tuple_base_to_session = {
                    str(k): str(v) for k, v in tuple_base_to_session.items() if isinstance(v, str) and v
                }
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap load failed: %s", exc)

    def _save(self) -> None:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                payload: dict[str, Any] = {
                    "tuple_to_base": self._tuple_to_base,
                    "tuple_base_to_session": self._tuple_base_to_session,
                }
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap save failed: %s", exc)

    def _get_base_id(
        self,
        *,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        rotate: bool = False,
    ) -> tuple[str, bool]:
        """Return base_id and whether mappings were changed."""
        tuple_key = _make_key(provider, chat_id, bot_id, user_id)
        if rotate:
            base_id = _make_base_session_id()
            self._tuple_to_base[tuple_key] = base_id
            return base_id, True

        base_id = self._tuple_to_base.get(tuple_key)
        if base_id:
            return base_id, False

        base_id = _make_base_session_id()
        self._tuple_to_base[tuple_key] = base_id
        return base_id, True

    def _get_session_id_by_base(
        self,
        base_id: str,
        *,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        rotate: bool = False,
    ) -> tuple[str, bool]:
        """Return session_id and whether mapping was changed."""
        tuple_key = _make_key(provider, chat_id, bot_id, user_id)
        tuple_base_key = _make_tuple_base_key(tuple_key, base_id)
        if not rotate:
            sid = self._tuple_base_to_session.get(tuple_base_key)
            if sid:
                return sid, False

        sid = _make_session_id(base_id, provider, chat_id, bot_id, user_id)
        self._tuple_base_to_session[tuple_base_key] = sid
        return sid, True

    def get_or_create(self, provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
        return self.get_session_id(provider, chat_id, bot_id, user_id, rotate=False)

    def get_session_id(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        *,
        rotate: bool = False,
    ) -> str:
        """统一入口：先获取 base_id，再根据五元组获取 session_id。"""
        base_id, base_changed = self._get_base_id(
            provider=provider,
            chat_id=chat_id,
            bot_id=bot_id,
            user_id=user_id,
            rotate=rotate,
        )
        sid, sid_changed = self._get_session_id_by_base(
            base_id,
            provider=provider,
            chat_id=chat_id,
            bot_id=bot_id,
            user_id=user_id,
            rotate=rotate,
        )
        if base_changed or sid_changed:
            self._save()
        return sid

    def rotate(self, provider: str, chat_id: str, bot_id: str, user_id: str) -> str:
        return self.get_session_id(provider, chat_id, bot_id, user_id, rotate=True)
