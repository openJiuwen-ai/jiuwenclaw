import os
import sys
import logging
from contextvars import ContextVar, Token
from typing import Any

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


# set方法只有agentserver使用
def set_local_config(name: str, value):
    if not value:
        ENV_CONFIG_DICT.pop(name, None)
        return
    ENV_CONFIG_DICT[name] = value


def decrypt(name, cipher):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            is_need_decrypt = "api_key" in name.lower() or "token" in name.lower()
            if is_need_decrypt and crypto:
                return crypto.decrypt(cipher)
        except Exception as e:
            logger.warning(f"Decryption failed exception: {e}")
    return cipher


def encrypt(name, text):
    reg_mod = sys.modules.get("jiuwenclaw.extensions.registry")
    if reg_mod is not None and hasattr(reg_mod, "ExtensionRegistry"):
        try:
            crypto = reg_mod.ExtensionRegistry.get_instance().get_crypto_provider()
            is_need_decrypt = "api_key" in name.lower() or "token" in name.lower()
            if is_need_decrypt and crypto:
                return crypto.encrypt(text)
        except Exception as e:
            logger.warning(f"Encryption failed exception: {e}")
    return text
