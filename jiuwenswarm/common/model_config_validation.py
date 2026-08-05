"""Shared validation helpers for model configuration values."""

from __future__ import annotations

from urllib.parse import urlparse

PLACEHOLDER_API_BASES = frozenset({"https://example.com/compatible-mode/v1"})
EXAMPLE_DOMAINS = frozenset({"example.com", "example.org", "example.net"})


def is_placeholder_api_base(api_base: str) -> bool:
    """Return True when api_base is a documentation placeholder URL."""
    value = str(api_base or "").strip()
    if not value:
        return False
    if value in PLACEHOLDER_API_BASES:
        return True
    try:
        host = urlparse(value).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in EXAMPLE_DOMAINS)


def is_model_client_config_usable(mcc: dict) -> bool:
    """Return True when model_client_config has real-looking API credentials."""
    from openjiuwen.core.foundation.llm.utils.provider_utils import is_openai_account_provider

    api_base = str(mcc.get("api_base", "") or "").strip()
    if not api_base or is_placeholder_api_base(api_base):
        return False

    provider = mcc.get("client_provider", "")
    provider = getattr(provider, "value", provider)
    if is_openai_account_provider(str(provider or "")):
        return True

    return bool(str(mcc.get("api_key", "") or "").strip())


def default_model_preflight_error(config: dict | None = None) -> str | None:
    """Return a startup warning when no default model is usable, else None."""
    from jiuwenswarm.common.config import get_default_models
    from jiuwenswarm.common.utils import get_env_file

    try:
        models = get_default_models(config)
    except Exception:
        return None
    if any(is_model_client_config_usable(m.get("model_client_config", {}) or {}) for m in models):
        return None
    return (
        "No model configured. Set API_BASE / API_KEY / MODEL_NAME in "
        f"{get_env_file()} or open the Web config panel (Model Config), then restart."
    )
