import json
import os
import sys
import logging
from contextvars import ContextVar, Token
from typing import Any

DEFAULT_HEADERS_ENV_KEY = "default_headers"
_DEFAULT_HEADERS_ALIASES = (
    DEFAULT_HEADERS_ENV_KEY,
    "DEFAULT_HEADERS",
    "OPENAI_DEFAULT_HEADERS",
)

logger = logging.getLogger(__name__)

ENV_CONFIG_DICT: dict[str, Any] = {}

# Pending env overrides from agent.reload_config (not yet promoted to active).
_STAGED_ENV: dict[str, Any] = {}

# Per-async-task overlay for new chats while other sessions still use active env.
_task_env_overlay: ContextVar[dict[str, Any] | None] = ContextVar(
    "jiuwenclaw_task_env_overlay", default=None
)


def get_staged_env() -> dict[str, Any]:
    """Return a copy of staged env overrides (may be empty)."""
    return dict(_STAGED_ENV)


def stage_env_overrides(env_overrides: dict[str, Any] | None) -> None:
    """Merge env reload payload into staged env without touching active storage."""
    if not isinstance(env_overrides, dict):
        return
    for env_key, env_value in env_overrides.items():
        key = str(env_key)
        if env_value is None:
            _STAGED_ENV.pop(key, None)
        else:
            _STAGED_ENV[key] = str(env_value)


def clear_staged_env() -> None:
    """Clear staged env after promote."""
    _STAGED_ENV.clear()


def promote_staged_env() -> None:
    """Promote staged env into ENV_CONFIG_DICT and os.environ."""
    if not _STAGED_ENV:
        return
    for key, value in list(_STAGED_ENV.items()):
        if value is None:
            ENV_CONFIG_DICT.pop(key, None)
            os.environ.pop(key, None)
        else:
            ENV_CONFIG_DICT[key] = value
            os.environ[key] = str(value)
    _STAGED_ENV.clear()


def apply_env_overrides_to_active(env_overrides: dict[str, Any] | None) -> None:
    """Write env overrides directly to active storage (used on cold start replay)."""
    if not isinstance(env_overrides, dict):
        return
    for env_key, env_value in env_overrides.items():
        key = str(env_key)
        if env_value is None:
            ENV_CONFIG_DICT.pop(key, None)
            os.environ.pop(key, None)
        else:
            value = str(env_value)
            ENV_CONFIG_DICT[key] = value
            os.environ[key] = value


def apply_env_removals(removals: dict[str, None] | None) -> None:
    """Remove env keys from active storage, staged env, and ``os.environ``."""
    if not isinstance(removals, dict) or not removals:
        return
    for env_key in removals:
        key = str(env_key)
        ENV_CONFIG_DICT.pop(key, None)
        _STAGED_ENV.pop(key, None)
        os.environ.pop(key, None)


def build_effective_env_overlay(*extra: dict[str, Any] | None) -> dict[str, Any]:
    """Merge staged env with optional extra dicts for task overlay binding."""
    merged: dict[str, Any] = dict(ENV_CONFIG_DICT)
    merged.update(get_staged_env())
    for part in extra:
        if isinstance(part, dict):
            for key, value in part.items():
                k = str(key)
                if value is None:
                    merged.pop(k, None)
                else:
                    merged[k] = str(value)
    return merged


def bind_task_env_overlay(overlay: dict[str, Any] | None) -> Token:
    """Bind task-scoped env overlay for get_local_config resolution."""
    return _task_env_overlay.set(overlay if overlay else None)


def reset_task_env_overlay(token: Token) -> None:
    """Restore previous task env overlay binding."""
    _task_env_overlay.reset(token)


def get_task_env_overlay() -> dict[str, Any] | None:
    """Return current task overlay if bound."""
    return _task_env_overlay.get()


# get方法agentserver和gateway都会使用
def get_local_config(name: str, default=None):
    overlay = _task_env_overlay.get()
    if overlay is not None and name in overlay:
        value = overlay[name]
        if value is None or value == "":
            return default
        return decrypt(name, value) if isinstance(value, str) else value
    if name in ENV_CONFIG_DICT:
        return ENV_CONFIG_DICT[name]
    if name in os.environ:
        return decrypt(name, os.environ[name])
    return default


def read_env(name: str, default: str = "") -> str:
    """Overlay-aware ``os.environ.get`` for hot-reload paths."""
    value = get_local_config(name, default or None)
    if value is None:
        return default
    text = str(value)
    return text if text else default


def read_env_if_set(name: str) -> str | None:
    """Return env value when *name* is explicitly set in any resolution layer.

    Resolution order matches ``build_effective_env_overlay`` (staged over active):
    task overlay → staged env → ``ENV_CONFIG_DICT`` → ``os.environ``.

    Returns ``None`` when unset (callers may fall back to config defaults).
    Returns ``""`` when explicitly cleared at the env layer (including overlay
    ``None`` / empty string).
    """
    overlay = _task_env_overlay.get()
    if overlay is not None and name in overlay:
        value = overlay[name]
        if value is None:
            return ""
        if isinstance(value, str):
            return decrypt(name, value)
        return str(value)

    if name in _STAGED_ENV:
        value = _STAGED_ENV[name]
        return "" if value is None else str(value)

    if name in ENV_CONFIG_DICT:
        value = ENV_CONFIG_DICT[name]
        if isinstance(value, str):
            return decrypt(name, value)
        return str(value)

    if name in os.environ:
        return decrypt(name, os.environ[name])

    return None


def read_default_headers_raw() -> str:
    """Overlay-aware raw JSON string for default HTTP headers."""
    for env_key in _DEFAULT_HEADERS_ALIASES:
        raw = read_env(env_key, "")
        if raw.strip():
            return raw.strip()
    return ""


def parse_default_headers(raw: str) -> dict[str, str] | None:
    """Parse and validate default_headers JSON; return None when empty."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"default_headers is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("default_headers must be a JSON object")
    return {str(k): str(v) for k, v in parsed.items() if v is not None}


def read_default_headers() -> dict[str, str] | None:
    """Read overlay-aware default_headers as a header map."""
    return parse_default_headers(read_default_headers_raw())


def is_sensitive_env_name(name: str) -> bool:
    lower = name.lower()
    return (
        "api_key" in lower
        or "token" in lower
        or lower == DEFAULT_HEADERS_ENV_KEY
        or "header" in lower
    )


# set方法只有agentserver使用
def set_local_config(name: str, value) -> None:
    if value:
        ENV_CONFIG_DICT[name] = value
    else:
        ENV_CONFIG_DICT.pop(name, None)


def decrypt(name, cipher):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            if is_sensitive_env_name(name) and crypto:
                return crypto.decrypt(cipher)
        except Exception as e:
            logger.warning(f"Decryption failed exception: {e}")
    return cipher


def encrypt(name, text):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            if is_sensitive_env_name(name) and crypto:
                return crypto.encrypt(text)
        except Exception as e:
            logger.warning(f"Encryption failed exception: {e}")
    return text
