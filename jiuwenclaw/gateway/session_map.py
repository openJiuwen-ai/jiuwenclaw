from __future__ import annotations

import json
import secrets
import time
from pathlib import Path

from jiuwenclaw.utils import get_checkpoint_dir, logger


def _normalize_chat_type(chat_type: str | None) -> str:
    return str(chat_type or "").strip().lower()


def _make_key(
    provider: str,
    chat_id: str,
    bot_id: str,
    user_id: str,
    chat_type: str | None = None,
) -> str:
    normalized = _normalize_chat_type(chat_type)
    if normalized == "p2p":
        return f"{provider}::p2p::{bot_id}::{user_id}"
    elif normalized == "group":
        return f"{provider}::{chat_id}::{bot_id}::{user_id}"


def _make_base_session_id(channel_id: str) -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"{channel_id}_{ts}_{suffix}"


def _build_session_id(
    base: str,
    provider: str,
    chat_id: str,
    bot_id: str,
    user_id: str,
    chat_type: str | None = None,
) -> str:
    if _normalize_chat_type(chat_type) == "p2p":
        return f"{base}_{provider}_{bot_id}_{user_id}"
    return f"{base}_{provider}_{chat_id}_{bot_id}_{user_id}"


class SessionMap:
    """Map (provider, chat_id, bot_id, user_id) to stable session_id."""

    def __init__(self, channel_id: str) -> None:
        self._channel_id = channel_id
        self._store_path: Path = get_checkpoint_dir() / f"{channel_id}_session_map.json"
        self._mapping: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            if not self._store_path.exists():
                return
            with open(self._store_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self._mapping = {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v}
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap load failed for %s: %s", self._channel_id, exc)

    def _save(self) -> None:
        try:
            self._store_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._store_path, "w", encoding="utf-8") as f:
                json.dump(self._mapping, f, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            logger.warning("SessionMap save failed for %s: %s", self._channel_id, exc)

    def get_or_create(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        chat_type: str | None = None,
    ) -> str:
        key = _make_key(provider, chat_id, bot_id, user_id, chat_type=chat_type)
        sid = self._mapping.get(key)
        if sid:
            return sid
        # Keep original session format prefix while appending tuple markers for observability.
        base = _make_base_session_id(self._channel_id)
        sid = _build_session_id(base, provider, chat_id, bot_id, user_id, chat_type=chat_type)
        self._mapping[key] = sid
        self._save()
        return sid

    def rotate(
        self,
        provider: str,
        chat_id: str,
        bot_id: str,
        user_id: str,
        chat_type: str | None = None,
    ) -> str:
        key = _make_key(provider, chat_id, bot_id, user_id, chat_type=chat_type)
        base = _make_base_session_id(self._channel_id)
        sid = _build_session_id(base, provider, chat_id, bot_id, user_id, chat_type=chat_type)
        self._mapping[key] = sid
        self._save()
        return sid
