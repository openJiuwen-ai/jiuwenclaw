#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Environment and settings helpers for the runtime."""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from .. import REPO_ROOT

SUPPORTED_MODEL_PROVIDERS = frozenset({"openai", "openrouter", "siliconflow", "dashscope"})
TRUTHY_ENV_VALUES = frozenset({"1", "true", "yes", "on"})
FALSY_ENV_VALUES = frozenset({"0", "false", "no", "off"})
DEFAULT_MODEL_NAME = "anthropic/claude-sonnet-4.5"
DEFAULT_BROWSER_TIMEOUT_S = 180
DEFAULT_GUARDRAIL_MAX_STEPS = 20
DEFAULT_GUARDRAIL_MAX_FAILURES = 2
DEFAULT_GUARDRAIL_RETRY_ONCE = True
DEFAULT_PLAYWRIGHT_MCP_COMMAND = "npx"
DEFAULT_PLAYWRIGHT_MCP_ARGS = "-y @playwright/mcp@latest"
MISSING_API_KEY_MESSAGE = (
    "Missing API key. Set API_KEY (or OPENROUTER_API_KEY / SILICONFLOW_API_KEY / "
    "OPENAI_API_KEY / DASHSCOPE_API_KEY)."
)


def resolve_repo_dotenv_path() -> Path:
    return Path(REPO_ROOT).expanduser().resolve() / ".env"


def load_repo_dotenv(*, override: bool = False) -> bool:
    env_path = resolve_repo_dotenv_path()
    if not env_path.exists():
        return False
    try:
        from dotenv import load_dotenv
    except ImportError:
        return False
    return bool(load_dotenv(dotenv_path=env_path, override=override))


def parse_command_args(value: str) -> list[str]:
    value = (value or "").strip()
    if not value:
        return []
    if value.startswith("["):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [str(x) for x in parsed]
        except json.JSONDecodeError:
            pass
    return shlex.split(value)


def first_non_empty_env(*keys: str) -> str:
    for key in keys:
        value = (os.getenv(key) or "").strip()
        if value:
            return value
    return ""


def normalize_provider(provider: str) -> str:
    raw = (provider or "").strip()
    lowered = raw.lower()
    if lowered in SUPPORTED_MODEL_PROVIDERS:
        return lowered
    if lowered in {"alibaba", "aliyun"}:
        return "dashscope"
    if lowered in {"silicon-flow", "silicon_flow"}:
        return "siliconflow"
    return raw


def is_truthy_env(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered in TRUTHY_ENV_VALUES


def is_falsy_env(value: str) -> bool:
    lowered = (value or "").strip().lower()
    return lowered in FALSY_ENV_VALUES


def resolve_int_env(*keys: str, default: int, minimum: int | None = None) -> int:
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        if minimum is None or value >= minimum:
            return value
    return default


def resolve_bool_env(*keys: str, default: bool) -> bool:
    for key in keys:
        raw = (os.getenv(key) or "").strip()
        if not raw:
            continue
        if is_truthy_env(raw):
            return True
        if is_falsy_env(raw):
            return False
    return default


def infer_provider_from_api_base(api_base: str) -> str:
    base = (api_base or "").strip().lower()
    if not base:
        return ""
    if "openrouter.ai" in base:
        return "openrouter"
    if "siliconflow.cn" in base or "siliconflow" in base:
        return "siliconflow"
    if "dashscope.aliyuncs.com" in base or "dashscope" in base:
        return "dashscope"
    return "openai"


def resolve_model_name() -> str:
    return first_non_empty_env("MODEL_NAME") or DEFAULT_MODEL_NAME


def resolve_browser_timeout_s() -> int:
    return resolve_int_env(
        "BROWSER_TIMEOUT_S",
        "PLAYWRIGHT_TOOL_TIMEOUT_S",
        default=DEFAULT_BROWSER_TIMEOUT_S,
        minimum=1,
    )


def resolve_model_settings() -> tuple[str, str, str]:
    provider_mode = normalize_provider(first_non_empty_env("MODEL_PROVIDER", "MODEL_CLIENT_PROVIDER"))
    if provider_mode and provider_mode not in SUPPORTED_MODEL_PROVIDERS:
        raise ValueError(
            f"Unsupported MODEL_PROVIDER '{provider_mode}'. "
            "Supported: openai, openrouter, siliconflow, dashscope."
        )

    explicit_api_key = first_non_empty_env("API_KEY", "MODEL_API_KEY")
    explicit_api_base = first_non_empty_env("API_BASE", "MODEL_API_BASE")

    if provider_mode:
        provider = provider_mode
    else:
        base_hint = explicit_api_base or first_non_empty_env(
            "OPENROUTER_BASE_URL",
            "OPENROUTER_API_BASE",
            "SILICONFLOW_BASE_URL",
            "SILICONFLOW_API_BASE",
            "DASHSCOPE_BASE_URL",
            "DASHSCOPE_API_BASE",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
        )
        provider = infer_provider_from_api_base(base_hint)
        if not provider:
            has_openrouter_key = bool(first_non_empty_env("OPENROUTER_API_KEY"))
            has_siliconflow_key = bool(first_non_empty_env("SILICONFLOW_API_KEY"))
            has_dashscope_key = bool(first_non_empty_env("DASHSCOPE_API_KEY"))
            if has_openrouter_key:
                provider = "openrouter"
            elif has_siliconflow_key:
                provider = "siliconflow"
            elif has_dashscope_key:
                provider = "dashscope"
            else:
                provider = "openai"

    if provider == "openrouter":
        api_key = first_non_empty_env(
            "API_KEY",
            "MODEL_API_KEY",
            "OPENROUTER_API_KEY",
            "OPENAI_API_KEY",
        )
        api_base = first_non_empty_env(
            "API_BASE",
            "MODEL_API_BASE",
            "OPENROUTER_BASE_URL",
            "OPENROUTER_API_BASE",
        ) or "https://openrouter.ai/api/v1"
    elif provider == "siliconflow":
        api_key = first_non_empty_env(
            "API_KEY",
            "MODEL_API_KEY",
            "SILICONFLOW_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        )
        api_base = first_non_empty_env(
            "API_BASE",
            "MODEL_API_BASE",
            "SILICONFLOW_BASE_URL",
            "SILICONFLOW_API_BASE",
        ) or "https://api.siliconflow.cn/v1"
    elif provider == "dashscope":
        api_key = first_non_empty_env(
            "API_KEY",
            "MODEL_API_KEY",
            "DASHSCOPE_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        )
        api_base = first_non_empty_env(
            "API_BASE",
            "MODEL_API_BASE",
            "DASHSCOPE_BASE_URL",
            "DASHSCOPE_API_BASE",
        ) or "https://dashscope.aliyuncs.com/compatible-mode/v1"
    else:
        api_key = first_non_empty_env(
            "API_KEY",
            "MODEL_API_KEY",
            "OPENAI_API_KEY",
            "OPENROUTER_API_KEY",
        )
        api_base = first_non_empty_env(
            "API_BASE",
            "MODEL_API_BASE",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
        ) or "https://api.openai.com/v1"
    return provider, api_key, api_base
