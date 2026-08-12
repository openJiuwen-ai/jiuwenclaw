# coding: utf-8
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Literal
from urllib.parse import urlparse


ReasoningProviderKind = Literal["deepseek_official", "dashscope_bailian"]
ReasoningLevel = Literal["off", "low", "medium", "high"]
ReasoningEffort = Literal["off", "high"]

OPENAI_SDK_REASONING_PROVIDERS = {
    "openai",
    "deepseek",
    "dashscope",
}

SUPPORTED_DEEPSEEK_V4_MODELS = {
    "deepseek-v4-pro",
    "deepseek-v4-flash",
}

LEVEL_MAPPING: dict[ReasoningLevel, ReasoningEffort] = {
    "off": "off",
    "low": "high",
    "medium": "high",
    "high": "high",
}


def _normalize_provider(provider: Any) -> str:
    if isinstance(provider, Enum):
        provider = provider.value
    return str(provider or "").strip().lower()


def _parse_api_base(api_base: str | None):
    value = str(api_base or "").strip()
    if value and "://" not in value:
        value = f"https://{value}"
    return urlparse(value)


def resolve_reasoning_provider_kind(
    api_base: str | None,
) -> ReasoningProviderKind | None:
    parsed = _parse_api_base(api_base)
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").rstrip("/")

    if host == "api.deepseek.com":
        return "deepseek_official"

    if host == "dashscope.aliyuncs.com" and path.startswith("/compatible-mode"):
        return "dashscope_bailian"

    return None


def normalize_reasoning_level(raw: Any) -> ReasoningLevel | None:
    if raw is None:
        return None

    key = str(raw).strip().lower()
    if key == "":
        return None
    if key in {"off", "none", "false", "disable", "disabled"}:
        return "off"
    if key in {"on", "true", "enable", "enabled", "low"}:
        return "low"
    if key in {"medium", "med", "mid"}:
        return "medium"
    if key == "high":
        return "high"
    return None


def resolve_reasoning_target(
    *,
    client_provider: Any,
    api_base: str | None,
    model_name: str | None,
) -> tuple[ReasoningProviderKind, str] | None:
    provider = _normalize_provider(client_provider)
    if provider not in OPENAI_SDK_REASONING_PROVIDERS:
        return None

    provider_kind = resolve_reasoning_provider_kind(api_base)
    if provider_kind is None:
        return None

    model = str(model_name or "").strip().lower()
    if model not in SUPPORTED_DEEPSEEK_V4_MODELS:
        return None
    return provider_kind, model


def is_gpt5_family(model_name: str | None) -> bool:
    """OpenAI gpt-5 系列模型判断（不含 o 系列推理模型）。

    gpt-5 系列在 /v1/chat/completions 中使用 function tools 时必须显式
    ``reasoning_effort="none"``，否则 OpenAI API 返回 400；o 系列原生
    支持 function calling，不应注入以免禁用其推理能力。
    """
    return str(model_name or "").strip().lower().startswith("gpt-5")


def is_new_generation_openai_model(model_name: str | None) -> bool:
    """OpenAI gpt-5 系列与 o 系列（o1/o3/o4 等）新代际模型判断。

    这类模型已弃用 ``max_tokens``（须改用 ``max_completion_tokens``），
    否则 OpenAI API 返回 400 unsupported_parameter。
    """
    name = str(model_name or "").strip().lower()
    return is_gpt5_family(name) or bool(re.match(r"^o\d", name))


__all__ = [
    "LEVEL_MAPPING",
    "OPENAI_SDK_REASONING_PROVIDERS",
    "SUPPORTED_DEEPSEEK_V4_MODELS",
    "ReasoningEffort",
    "ReasoningLevel",
    "ReasoningProviderKind",
    "is_gpt5_family",
    "is_new_generation_openai_model",
    "normalize_reasoning_level",
    "resolve_reasoning_provider_kind",
    "resolve_reasoning_target",
]
