"""Capability rules for first-party subscription-backed model providers."""

from __future__ import annotations

from dataclasses import dataclass

from openjiuwen.core.foundation.llm import ProviderType
from openjiuwen.core.foundation.llm.utils.provider_utils import (
    is_openai_account_provider,
)

from .claude_constants import CLAUDE_MODEL_ALIAS, CLAUDE_PROVIDER_NAME
from .constants import CODEX_MODEL_ALIAS, CODEX_PROVIDER_NAME


@dataclass(frozen=True)
class ModelProviderCapabilities:
    requires_api_base: bool = True
    requires_api_key: bool = True
    fixed_model_name: str | None = None
    subscription_auth: bool = False


_CODEX_CAPABILITIES = ModelProviderCapabilities(
    requires_api_base=False,
    requires_api_key=False,
    fixed_model_name=CODEX_MODEL_ALIAS,
    subscription_auth=True,
)
_OPENAI_ACCOUNT_CAPABILITIES = ModelProviderCapabilities(
    requires_api_base=True,
    requires_api_key=False,
    subscription_auth=True,
)
# Claude carries NO credential in Jiuwen config: the CLI resolves credentials
# natively from the operator's environment - an exported ANTHROPIC_API_KEY, or
# the operator's own Claude login performed outside this product. The config
# therefore requires (and must contain) neither an API key nor an API base.
# subscription_auth=False: no in-product auth controller, and the provider routes
# through the normal model path (not the Codex subscription-admission wrapper).
_CLAUDE_CAPABILITIES = ModelProviderCapabilities(
    requires_api_base=False,
    requires_api_key=False,
    fixed_model_name=CLAUDE_MODEL_ALIAS,
    subscription_auth=False,
)


def provider_name(value: object) -> str:
    return str(getattr(value, "value", value) or "").strip()


def get_model_provider_capabilities(value: object) -> ModelProviderCapabilities:
    normalized = provider_name(value)
    if normalized == CODEX_PROVIDER_NAME:
        return _CODEX_CAPABILITIES
    if normalized == CLAUDE_PROVIDER_NAME:
        return _CLAUDE_CAPABILITIES
    if is_openai_account_provider(normalized):
        return _OPENAI_ACCOUNT_CAPABILITIES
    return ModelProviderCapabilities()


def available_model_provider_names() -> list[str]:
    providers = [item.value for item in ProviderType]
    if CODEX_PROVIDER_NAME not in providers:
        providers.append(CODEX_PROVIDER_NAME)
    if CLAUDE_PROVIDER_NAME not in providers:
        providers.append(CLAUDE_PROVIDER_NAME)
    return providers


def missing_model_fields(
    *,
    model_name: object,
    model_provider: object,
    api_base: object,
    api_key: object,
) -> list[str]:
    provider = provider_name(model_provider)
    capabilities = get_model_provider_capabilities(provider)
    values = {
        "model": str(model_name or "").strip(),
        "model_provider": provider,
        "api_base": str(api_base or "").strip(),
        "api_key": str(api_key or "").strip(),
    }
    required = ["model", "model_provider"]
    if capabilities.requires_api_base:
        required.append("api_base")
    if capabilities.requires_api_key:
        required.append("api_key")
    return [field for field in required if not values[field]]


def validate_provider_model_name(model_provider: object, model_name: object) -> bool:
    capabilities = get_model_provider_capabilities(model_provider)
    actual = str(model_name or "").strip()
    return capabilities.fixed_model_name is None or actual == capabilities.fixed_model_name


def model_client_config_looks_usable(config: dict) -> bool:
    provider = config.get("client_provider", config.get("model_provider", ""))
    capabilities = get_model_provider_capabilities(provider)
    model_name = config.get("model_name", config.get("model", ""))
    if missing_model_fields(
        model_name=model_name,
        model_provider=provider,
        api_base=config.get("api_base", ""),
        api_key=config.get("api_key", ""),
    ):
        return False
    if not capabilities.requires_api_base and str(config.get("api_base", "") or "").strip():
        return False
    if not capabilities.requires_api_key and str(config.get("api_key", "") or "").strip():
        return False
    return validate_provider_model_name(provider, model_name)
