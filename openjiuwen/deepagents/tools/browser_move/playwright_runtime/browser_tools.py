#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Browser MCP integration helpers for playwright_runtime."""

from __future__ import annotations

import json
import os
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.runner import Runner

from .. import REPO_ROOT

_DEFAULT_SERVER_ID = "playwright_runtime_wrapper"
_DEFAULT_SERVER_NAME = "playwright-runtime-wrapper"
_SUPPORTED_CLIENT_TYPES = {"stdio", "sse", "streamable-http", "streamable_http", "http"}
_AUTO_HTTP_FALLBACK = "PLAYWRIGHT_RUNTIME_MCP_AUTO_HTTP_FALLBACK"
_PROXY_BLOCKLIST = {"http://127.0.0.1:9", "http://localhost:9"}
_SERVER_MODULE = "openjiuwen.deepagents.tools.browser_move.playwright_runtime_mcp_server"
_LOCAL_SERVER_PROCESS: subprocess.Popen[str] | None = None
_LOCAL_SERVER_URL: str | None = None
_OPENJIUWEN_CLIENTS_PATCHED = False


def _env_first(*names: str, default: str = "") -> str:
    for name in names:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return default


def _env_bool(*names: str, default: bool = False) -> bool:
    value = _env_first(*names)
    if not value:
        return default
    return value.lower() in {"1", "true", "yes", "on"}


def _module_launch_cwd() -> str:
    current = Path(REPO_ROOT).expanduser().resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "pyproject.toml").exists() and (candidate / "openjiuwen" / "__init__.py").exists():
            return str(candidate)
    raise RuntimeError(f"Could not resolve module launch cwd from {current}")


def _parse_args(raw: str) -> list[str]:
    text = (raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(item) for item in parsed]
        except Exception:
            pass
    try:
        return shlex.split(text, posix=(os.name != "nt"))
    except Exception:
        return text.split()


def _normalize_client_type(client_type: str) -> str:
    value = (client_type or "").strip().lower()
    if value in {"http", "streamable_http"}:
        return "streamable-http"
    return value


def _ensure_openjiuwen_client_patch() -> None:
    global _OPENJIUWEN_CLIENTS_PATCHED
    if _OPENJIUWEN_CLIENTS_PATCHED:
        return

    import openjiuwen.core.runner.resources_manager.tool_manager as tool_mgr

    from ..clients.streamable_http_client import BrowserMoveStreamableHttpClient

    original_create_client = tool_mgr.ToolMgr._create_client

    def _patched_create_client(config: McpServerConfig):
        normalized = _normalize_client_type(getattr(config, "client_type", ""))
        if normalized == "streamable-http":
            return BrowserMoveStreamableHttpClient(config)
        return original_create_client(config)

    tool_mgr.StreamableHttpClient = BrowserMoveStreamableHttpClient
    tool_mgr.ToolMgr._create_client = staticmethod(_patched_create_client)
    _OPENJIUWEN_CLIENTS_PATCHED = True


def _build_child_env() -> dict[str, str]:
    env = dict(os.environ)
    passthrough_keys = [
        "MODEL_NAME",
        "MODEL_PROVIDER",
        "API_KEY",
        "API_BASE",
        "OPENROUTER_API_KEY",
        "OPENROUTER_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "PLAYWRIGHT_MCP_COMMAND",
        "PLAYWRIGHT_MCP_ARGS",
        "PLAYWRIGHT_CDP_URL",
        "PLAYWRIGHT_CDP_HEADERS",
        "PLAYWRIGHT_MCP_CDP_ENDPOINT",
        "PLAYWRIGHT_MCP_CDP_TIMEOUT",
        "PLAYWRIGHT_MCP_BROWSER",
        "PLAYWRIGHT_MCP_DEVICE",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PLAYWRIGHT_TOOL_TIMEOUT_S",
        "PLAYWRIGHT_RUNTIME_MCP_COMMAND",
        "PLAYWRIGHT_RUNTIME_MCP_ARGS",
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH",
        "PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S",
        "BROWSER_TIMEOUT_S",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
    ]
    for key in passthrough_keys:
        value = os.getenv(key)
        if value:
            env[key] = value

    api_key = (os.getenv("API_KEY") or "").strip()
    api_base = (os.getenv("API_BASE") or "").strip()
    model_provider = (os.getenv("MODEL_PROVIDER") or "").strip().lower()

    if api_key and not env.get("OPENROUTER_API_KEY") and "openrouter.ai" in api_base:
        env["OPENROUTER_API_KEY"] = api_key
    if api_base and not env.get("OPENROUTER_BASE_URL") and "openrouter.ai" in api_base:
        env["OPENROUTER_BASE_URL"] = api_base

    if api_key and not env.get("OPENAI_API_KEY") and "openrouter.ai" not in api_base:
        env["OPENAI_API_KEY"] = api_key
    if api_base and not env.get("OPENAI_BASE_URL") and "openrouter.ai" not in api_base:
        env["OPENAI_BASE_URL"] = api_base

    if model_provider == "openrouter":
        env["MODEL_PROVIDER"] = "openrouter"
    elif model_provider in {"openai", "siliconflow"}:
        env["MODEL_PROVIDER"] = model_provider

    for proxy_key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        proxy_value = (env.get(proxy_key) or "").strip().lower()
        if proxy_value in _PROXY_BLOCKLIST:
            env.pop(proxy_key, None)
    return env


def _runtime_host() -> str:
    return _env_first("PLAYWRIGHT_RUNTIME_MCP_HOST", "BROWSER_RUNTIME_MCP_HOST", default="127.0.0.1")


def _runtime_port() -> str:
    return _env_first("PLAYWRIGHT_RUNTIME_MCP_PORT", "BROWSER_RUNTIME_MCP_PORT", default="8940")


def _runtime_path(transport: str) -> str:
    default_path = "/mcp" if _normalize_client_type(transport) == "streamable-http" else "/sse"
    path = _env_first("PLAYWRIGHT_RUNTIME_MCP_PATH", "BROWSER_RUNTIME_MCP_PATH", default=default_path)
    if not path.startswith("/"):
        return f"/{path}"
    return path


def _build_server_url(transport: str) -> str:
    return f"http://{_runtime_host()}:{_runtime_port()}{_runtime_path(transport)}"


def _is_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError:
            return False
    return True


def _pick_available_port(host: str, preferred_port: int, max_attempts: int = 25) -> int:
    if preferred_port > 0 and _is_port_available(host, preferred_port):
        return preferred_port
    for port in range(preferred_port + 1, preferred_port + max_attempts + 1):
        if _is_port_available(host, port):
            return port
    raise RuntimeError("No available port for local browser runtime MCP server.")


def _wait_port_open(host: str, port: int, timeout_s: float = 20.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.8)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"Browser runtime MCP server did not start in time: {host}:{port}")


def _start_local_server(transport: str, host: str, port: int, path: str) -> str:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL

    normalized = _normalize_client_type(transport)
    if normalized not in {"sse", "streamable-http"}:
        raise ValueError(f"Unsupported local server transport: {transport}")

    command = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_COMMAND",
        "BROWSER_RUNTIME_MCP_COMMAND",
        default=sys.executable,
    )
    cmd = [
        command,
        "-m",
        _SERVER_MODULE,
        "--transport",
        normalized,
        "--host",
        host,
        "--port",
        str(port),
        "--path",
        path,
        "--no-banner",
    ]
    _LOCAL_SERVER_PROCESS = subprocess.Popen(
        cmd,
        cwd=_module_launch_cwd(),
        env=_build_child_env(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    _wait_port_open(host, port)
    _LOCAL_SERVER_URL = f"http://{host}:{port}{path}"
    return _LOCAL_SERVER_URL


def _parse_local_server_url(server_url: str) -> tuple[str, int, str]:
    parsed = urlparse(server_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.port is None:
        raise ValueError(f"Invalid browser runtime MCP URL: {server_url}")
    return parsed.hostname, int(parsed.port), parsed.path or "/mcp"


def stop_local_browser_runtime_server() -> None:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL

    proc = _LOCAL_SERVER_PROCESS
    _LOCAL_SERVER_PROCESS = None
    _LOCAL_SERVER_URL = None
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:
            pass


def restart_local_browser_runtime_server() -> str | None:
    transport = _normalize_client_type(
        _env_first(
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE",
            "BROWSER_RUNTIME_MCP_CLIENT_TYPE",
            default="streamable-http",
        )
    )
    current_url = _LOCAL_SERVER_URL
    current_proc = _LOCAL_SERVER_PROCESS

    if transport not in {"sse", "streamable-http"}:
        stop_local_browser_runtime_server()
        return None

    host = _runtime_host()
    path = _runtime_path(transport)
    preferred_port = int(_runtime_port())
    if current_url:
        host, preferred_port, path = _parse_local_server_url(current_url)

    had_local_server = current_url is not None or (current_proc is not None and current_proc.poll() is None)
    stop_local_browser_runtime_server()
    if not had_local_server:
        return None

    deadline = time.time() + 10.0
    while time.time() < deadline:
        if _is_port_available(host, preferred_port):
            return _start_local_server(transport, host, preferred_port, path)
        time.sleep(0.3)
    raise RuntimeError(f"Browser runtime port is still occupied after shutdown: {host}:{preferred_port}")


def _ensure_local_server_started(transport: str) -> str:
    global _LOCAL_SERVER_PROCESS
    global _LOCAL_SERVER_URL

    normalized = _normalize_client_type(transport)
    if normalized not in {"sse", "streamable-http"}:
        raise ValueError(f"Unsupported local server transport: {transport}")
    if _LOCAL_SERVER_PROCESS is not None and _LOCAL_SERVER_PROCESS.poll() is None and _LOCAL_SERVER_URL:
        return _LOCAL_SERVER_URL

    host = _runtime_host()
    preferred_port = int(_runtime_port())
    path = _runtime_path(normalized)
    port = _pick_available_port(host, preferred_port)
    return _start_local_server(normalized, host, port, path)


def _build_http_fallback_config(base_cfg: McpServerConfig, server_url: str | None = None) -> McpServerConfig:
    return McpServerConfig(
        server_id=f"{base_cfg.server_id}_http",
        server_name=base_cfg.server_name,
        server_path=server_url or _build_server_url("streamable-http"),
        client_type="streamable-http",
    )


def _build_http_retry_config(base_cfg: McpServerConfig, server_url: str) -> McpServerConfig:
    return McpServerConfig(
        server_id=base_cfg.server_id,
        server_name=base_cfg.server_name,
        server_path=server_url,
        client_type="streamable-http",
    )


def _result_is_ok(result: Any) -> bool:
    if result is None:
        return True
    is_ok = getattr(result, "is_ok", None)
    if callable(is_ok):
        try:
            return bool(is_ok())
        except Exception:
            return False
    return False


def _result_error_text(result: Any) -> str:
    if result is None:
        return ""
    for attr in ("error", "msg"):
        method = getattr(result, attr, None)
        if callable(method):
            try:
                value = method()
                if value is not None:
                    return str(value)
            except Exception:
                pass
    raw_error = getattr(result, "_error", None)
    if raw_error is not None:
        return str(raw_error)
    return str(result)


def build_browser_runtime_mcp_config() -> McpServerConfig | None:
    """Build browser runtime MCP config for OpenJiuWen agent registration."""
    if not _env_bool("PLAYWRIGHT_RUNTIME_MCP_ENABLED", "BROWSER_RUNTIME_MCP_ENABLED", default=False):
        return None

    server_id = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_ID",
        "BROWSER_RUNTIME_MCP_SERVER_ID",
        default=_DEFAULT_SERVER_ID,
    )
    server_name = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_NAME",
        "BROWSER_RUNTIME_MCP_SERVER_NAME",
        default=_DEFAULT_SERVER_NAME,
    )
    client_type = _normalize_client_type(
        _env_first(
            "PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE",
            "BROWSER_RUNTIME_MCP_CLIENT_TYPE",
            default="streamable-http",
        )
    )
    if client_type not in _SUPPORTED_CLIENT_TYPES:
        raise ValueError("PLAYWRIGHT_RUNTIME_MCP_CLIENT_TYPE must be stdio, sse, or streamable-http.")

    if client_type == "sse":
        server_path = _env_first(
            "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH",
            "BROWSER_RUNTIME_MCP_SERVER_PATH",
            default=_build_server_url("sse"),
        )
        return McpServerConfig(
            server_id=server_id,
            server_name=server_name,
            server_path=server_path,
            client_type="sse",
        )

    if client_type == "streamable-http":
        server_path = _env_first(
            "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH",
            "BROWSER_RUNTIME_MCP_SERVER_PATH",
            default=_build_server_url("streamable-http"),
        )
        return McpServerConfig(
            server_id=server_id,
            server_name=server_name,
            server_path=server_path,
            client_type="streamable-http",
        )

    command = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_COMMAND",
        "BROWSER_RUNTIME_MCP_COMMAND",
        default=sys.executable,
    )
    args_raw = _env_first("PLAYWRIGHT_RUNTIME_MCP_ARGS", "BROWSER_RUNTIME_MCP_ARGS")
    args = (
        _parse_args(args_raw)
        if args_raw
        else ["-m", _SERVER_MODULE, "--transport", "stdio", "--no-banner", "--log-level", "ERROR"]
    )

    params: dict[str, Any] = {
        "command": command,
        "args": args,
        "cwd": _module_launch_cwd(),
    }
    timeout_raw = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_TIMEOUT_S",
        "BROWSER_RUNTIME_MCP_TIMEOUT_S",
        default="300",
    )
    try:
        timeout_s = int(timeout_raw)
        if timeout_s > 0:
            params["timeout_s"] = timeout_s
    except ValueError:
        pass

    child_env = _build_child_env()
    if child_env:
        params["env"] = child_env

    server_path = _env_first(
        "PLAYWRIGHT_RUNTIME_MCP_SERVER_PATH",
        "BROWSER_RUNTIME_MCP_SERVER_PATH",
        default="stdio://playwright-runtime-wrapper",
    )
    return McpServerConfig(
        server_id=server_id,
        server_name=server_name,
        server_path=server_path,
        client_type="stdio",
        params=params,
    )


async def register_browser_runtime_mcp_server(agent: Any, *, tag: str = "agent.main") -> bool:
    """Register browser runtime MCP server on an OpenJiuWen agent with fallbacks."""
    _ensure_openjiuwen_client_patch()
    cfg = build_browser_runtime_mcp_config()
    if cfg is None:
        return False

    async def _register_once(target_cfg: McpServerConfig) -> tuple[bool, str]:
        result = await Runner.resource_mgr.add_mcp_server(target_cfg, tag=tag)
        if _result_is_ok(result):
            agent.ability_manager.add(target_cfg)
            return True, ""

        error_text = _result_error_text(result)
        if "already exist" in error_text.lower():
            agent.ability_manager.add(target_cfg)
            return True, error_text
        return False, error_text

    http_err = ""
    if cfg.client_type == "stdio" and _env_bool(_AUTO_HTTP_FALLBACK, default=True):
        http_cfg = _build_http_fallback_config(cfg)
        ok, http_err = await _register_once(http_cfg)
        if ok:
            return True
        try:
            auto_url = _ensure_local_server_started("streamable-http")
            http_cfg = _build_http_fallback_config(cfg, server_url=auto_url)
            ok, auto_http_err = await _register_once(http_cfg)
            if ok:
                return True
            http_err = f"{http_err} | {auto_http_err}".strip(" |")
        except Exception as exc:
            http_err = f"{http_err} | {exc}".strip(" |")

    ok, error_text = await _register_once(cfg)
    if ok:
        return True

    normalized_type = _normalize_client_type(cfg.client_type)
    if normalized_type in {"streamable-http", "sse"}:
        try:
            auto_url = _ensure_local_server_started(normalized_type)
            retry_cfg = (
                _build_http_retry_config(cfg, auto_url)
                if normalized_type == "streamable-http"
                else McpServerConfig(
                    server_id=cfg.server_id,
                    server_name=cfg.server_name,
                    server_path=auto_url,
                    client_type="sse",
                )
            )
            ok, retry_err = await _register_once(retry_cfg)
            if ok:
                return True
            error_text = f"{error_text} | {retry_err}".strip(" |")
        except Exception as exc:
            error_text = f"{error_text} | {exc}".strip(" |")

    if http_err:
        error_text = f"{error_text} | http={http_err}".strip(" |")
    raise RuntimeError(f"Failed to register browser runtime MCP server: {error_text}")
