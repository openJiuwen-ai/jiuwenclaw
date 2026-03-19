#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Runtime configuration helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

from openjiuwen.core.foundation.tool import McpServerConfig

from .. import REPO_ROOT
from ..utils.env import (
    DEFAULT_BROWSER_TIMEOUT_S,
    DEFAULT_GUARDRAIL_MAX_FAILURES,
    DEFAULT_GUARDRAIL_MAX_STEPS,
    DEFAULT_GUARDRAIL_RETRY_ONCE,
    DEFAULT_MODEL_NAME,
    DEFAULT_PLAYWRIGHT_MCP_ARGS,
    DEFAULT_PLAYWRIGHT_MCP_COMMAND,
    MISSING_API_KEY_MESSAGE,
    first_non_empty_env,
    is_truthy_env,
    load_repo_dotenv,
    parse_command_args,
    resolve_bool_env,
    resolve_browser_timeout_s,
    resolve_int_env,
    resolve_model_name,
    resolve_model_settings,
    resolve_repo_dotenv_path,
)


@dataclass
class BrowserRunGuardrails:
    max_steps: int = 20
    max_failures: int = 2
    timeout_s: int = 180
    retry_once: bool = True


@dataclass(frozen=True)
class RuntimeSettings:
    provider: str
    api_key: str
    api_base: str
    model_name: str
    mcp_cfg: McpServerConfig
    guardrails: BrowserRunGuardrails


def resolve_playwright_mcp_cwd() -> str:
    """Resolve MCP working directory with relocatable defaults."""
    process_cwd = Path.cwd().expanduser()
    if (process_cwd / "openjiuwen" / "deepagents" / "tools" / "browser_move" / "playwright_runtime").exists():
        return str(process_cwd.resolve())
    return str(Path(REPO_ROOT).expanduser().resolve())


def build_browser_guardrails() -> BrowserRunGuardrails:
    return BrowserRunGuardrails(
        max_steps=resolve_int_env(
            "BROWSER_GUARDRAIL_MAX_STEPS",
            default=DEFAULT_GUARDRAIL_MAX_STEPS,
            minimum=1,
        ),
        max_failures=resolve_int_env(
            "BROWSER_GUARDRAIL_MAX_FAILURES",
            default=DEFAULT_GUARDRAIL_MAX_FAILURES,
            minimum=0,
        ),
        timeout_s=resolve_browser_timeout_s(),
        retry_once=resolve_bool_env(
            "BROWSER_GUARDRAIL_RETRY_ONCE",
            default=DEFAULT_GUARDRAIL_RETRY_ONCE,
        ),
    )


def build_playwright_mcp_config() -> McpServerConfig:
    command = (
        os.getenv("PLAYWRIGHT_MCP_COMMAND", DEFAULT_PLAYWRIGHT_MCP_COMMAND).strip()
        or DEFAULT_PLAYWRIGHT_MCP_COMMAND
    )
    args = parse_command_args(os.getenv("PLAYWRIGHT_MCP_ARGS", DEFAULT_PLAYWRIGHT_MCP_ARGS))
    cwd = resolve_playwright_mcp_cwd()
    driver_mode = (os.getenv("BROWSER_DRIVER") or "").strip().lower()
    extension_mode = driver_mode == "extension" or is_truthy_env(os.getenv("PLAYWRIGHT_MCP_EXTENSION") or "")
    timeout_s = resolve_int_env(
        "PLAYWRIGHT_MCP_TIMEOUT_S",
        "BROWSER_TIMEOUT_S",
        default=DEFAULT_BROWSER_TIMEOUT_S,
        minimum=1,
    )

    env_map: Dict[str, str] = {}
    for key in (
        "PLAYWRIGHT_BROWSERS_PATH",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ):
        value = os.getenv(key)
        if value:
            env_map[key] = value

    extra_env_json = (os.getenv("PLAYWRIGHT_MCP_ENV_JSON") or "").strip()
    if extra_env_json:
        try:
            extra = json.loads(extra_env_json)
            if isinstance(extra, dict):
                for k, v in extra.items():
                    env_map[str(k)] = str(v)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid PLAYWRIGHT_MCP_ENV_JSON: {exc}") from exc

    if extension_mode:
        env_map["PLAYWRIGHT_MCP_EXTENSION"] = "true"
        extension_token = first_non_empty_env("PLAYWRIGHT_MCP_EXTENSION_TOKEN")
        if extension_token:
            env_map["PLAYWRIGHT_MCP_EXTENSION_TOKEN"] = extension_token
        if "--extension" not in args:
            args.append("--extension")
    else:
        # CDP support for official Playwright MCP server.
        cdp_endpoint = first_non_empty_env("PLAYWRIGHT_MCP_CDP_ENDPOINT", "PLAYWRIGHT_CDP_URL")
        cdp_headers = first_non_empty_env("PLAYWRIGHT_MCP_CDP_HEADERS", "PLAYWRIGHT_CDP_HEADERS")
        cdp_timeout = first_non_empty_env("PLAYWRIGHT_MCP_CDP_TIMEOUT", "PLAYWRIGHT_CDP_TIMEOUT_MS")
        browser_name = first_non_empty_env("PLAYWRIGHT_MCP_BROWSER")
        device_name = first_non_empty_env("PLAYWRIGHT_MCP_DEVICE")

        if cdp_endpoint:
            if device_name:
                raise ValueError("PLAYWRIGHT_MCP_DEVICE is not supported with CDP endpoint mode.")
            env_map["PLAYWRIGHT_MCP_CDP_ENDPOINT"] = cdp_endpoint
            if not browser_name:
                # CDP mode is Chromium-only.
                env_map["PLAYWRIGHT_MCP_BROWSER"] = "chrome"
        if cdp_headers:
            env_map["PLAYWRIGHT_MCP_CDP_HEADERS"] = cdp_headers
        if cdp_timeout:
            env_map["PLAYWRIGHT_MCP_CDP_TIMEOUT"] = cdp_timeout

    params: Dict[str, Any] = {
        "command": command,
        "args": args,
        "cwd": cwd,
    }
    if timeout_s > 0:
        params["timeout_s"] = timeout_s
    if env_map:
        params["env"] = env_map

    return McpServerConfig(
        server_id="playwright_official_stdio",
        server_name="playwright-official",
        server_path="stdio://playwright",
        client_type="stdio",
        params=params,
    )

    
def build_runtime_settings() -> RuntimeSettings:
    provider, api_key, api_base = resolve_model_settings()
    return RuntimeSettings(
        provider=provider,
        api_key=api_key,
        api_base=api_base,
        model_name=resolve_model_name(),
        mcp_cfg=build_playwright_mcp_config(),
        guardrails=build_browser_guardrails(),
    )
