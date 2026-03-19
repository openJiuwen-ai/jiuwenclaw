#!/usr/bin/env python
# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""MCP server wrapper for Playwright browser runtime.

Usage (from the agent-core repo root):
  uv run python -m openjiuwen.deepagents.tools.browser_move.playwright_runtime_mcp_server
  uv run python -m openjiuwen.deepagents.tools.browser_move.playwright_runtime_mcp_server \
      --transport streamable-http --host 127.0.0.1 --port 8940
"""

from __future__ import annotations

import argparse
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional

from fastmcp import Context, FastMCP
from openjiuwen.deepagents.tools.browser_move import REPO_ROOT
from openjiuwen.deepagents.tools.browser_move.clients.stdio_client import BrowserMoveStdioClient
from openjiuwen.deepagents.tools.browser_move.clients.streamable_http_client import (
    BrowserMoveStreamableHttpClient,
)
from openjiuwen.deepagents.tools.browser_move.playwright_runtime.config import (
    DEFAULT_BROWSER_TIMEOUT_S,
    MISSING_API_KEY_MESSAGE,
    build_runtime_settings,
    load_repo_dotenv,
)
from openjiuwen.deepagents.tools.browser_move.controllers.action import (
    bind_runtime,
    clear_runtime_runner,
    list_actions,
    register_example_actions,
    run_action,
)

import openjiuwen.core.runner.resources_manager.tool_manager as _tool_mgr_mod

if TYPE_CHECKING:
    from openjiuwen.deepagents.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

_tool_mgr_mod.StdioClient = BrowserMoveStdioClient
_tool_mgr_mod.StreamableHttpClient = BrowserMoveStreamableHttpClient

_runtime: Optional["BrowserAgentRuntime"] = None
_runtime_lock = asyncio.Lock()

load_repo_dotenv()


def _build_runtime() -> "BrowserAgentRuntime":
    from openjiuwen.deepagents.tools.browser_move.playwright_runtime.runtime import BrowserAgentRuntime

    settings = build_runtime_settings()
    if not settings.api_key:
        raise RuntimeError(MISSING_API_KEY_MESSAGE)
    return BrowserAgentRuntime(
        provider=settings.provider,
        api_key=settings.api_key,
        api_base=settings.api_base,
        model_name=settings.model_name,
        mcp_cfg=settings.mcp_cfg,
        guardrails=settings.guardrails,
    )


async def _get_runtime() -> BrowserAgentRuntime:
    global _runtime
    if _runtime is not None:
        return _runtime

    async with _runtime_lock:
        if _runtime is None:
            _runtime = _build_runtime()
            await _runtime.ensure_started()
            bind_runtime(_runtime)
        return _runtime


async def _shutdown_runtime() -> None:
    global _runtime
    async with _runtime_lock:
        if _runtime is not None:
            await _runtime.shutdown()
            _runtime = None
        clear_runtime_runner()


def _resolve_session_id(explicit_session_id: str, ctx: Context | None = None) -> str:
    """Resolve logical browser session id, preferring explicit value over MCP context."""
    explicit = (explicit_session_id or "").strip()
    if explicit:
        return explicit
    if ctx is None:
        return ""
    try:
        return (ctx.session_id or "").strip()
    except Exception:
        return ""


def _resolve_request_id(explicit_request_id: str, ctx: Context | None = None) -> str:
    """Resolve request id, preferring explicit value over MCP context."""
    explicit = (explicit_request_id or "").strip()
    if explicit:
        return explicit
    if ctx is None:
        return ""
    try:
        return (ctx.request_id or "").strip()
    except Exception:
        return ""


@asynccontextmanager
async def _lifespan(_server: FastMCP):
    try:
        register_example_actions()
        yield {}
    finally:
        await _shutdown_runtime()


mcp = FastMCP(
    "playwright-runtime-mcp",
    instructions=(
        "Browser automation MCP server. "
        "Use browser_run_task for web tasks. "
        "Pass a stable session_id to reuse browser session state."
    ),
    lifespan=_lifespan,
)


@mcp.tool(
    name="browser_run_task",
    description=(
        "Execute a browser task using Playwright runtime. "
        "Prefer one comprehensive task per request instead of many tiny retries. "
        "Use a long timeout and do not pass timeout_s below the configured default; "
        "omit timeout_s to use the default long timeout. "
        "Returns JSON with ok/session_id/request_id/final/page/screenshot/error/attempt."
    ),
)
async def browser_run_task(
    task: str,
    session_id: str = "",
    request_id: str = "",
    timeout_s: int = 0,
    ctx: Context | None = None,
) -> dict[str, Any]:
    runtime = await _get_runtime()
    effective_session_id = _resolve_session_id(session_id, ctx)
    effective_request_id = _resolve_request_id(request_id, ctx)
    return await runtime.run_browser_task(
        task=task,
        session_id=effective_session_id or None,
        request_id=effective_request_id or None,
        timeout_s=timeout_s if timeout_s > 0 else None,
    )


@mcp.tool(
    name="browser_cancel_task",
    description="Cancel an in-flight browser run for a session/request.",
)
async def browser_cancel_task(
    session_id: str = "",
    request_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    runtime = await _get_runtime()
    effective_session_id = _resolve_session_id(session_id, ctx)
    if not effective_session_id:
        raise ValueError("session_id is required for cancellation")
    effective_request_id = _resolve_request_id(request_id, ctx)
    return await runtime.cancel_run(session_id=effective_session_id, request_id=effective_request_id or None)


@mcp.tool(
    name="browser_clear_cancel",
    description="Clear cancellation flag for a session/request.",
)
async def browser_clear_cancel(
    session_id: str = "",
    request_id: str = "",
    ctx: Context | None = None,
) -> dict[str, Any]:
    runtime = await _get_runtime()
    effective_session_id = _resolve_session_id(session_id, ctx)
    if not effective_session_id:
        raise ValueError("session_id is required to clear cancellation")
    effective_request_id = _resolve_request_id(request_id, ctx)
    return await runtime.clear_cancel(session_id=effective_session_id, request_id=effective_request_id or None)


@mcp.tool(
    name="browser_custom_action",
    description=(
        "Run a custom controller action by name. Use for actions the Playwright MCP does not provide. "
        "Optional: session_id, request_id, and action-specific args via params object "
        "(e.g. params={'text': 'hello'} for 'echo'). "
        "Built-in examples: ping, echo, browser_get_element_coordinates, browser_drag_and_drop. "
        "For browser_get_element_coordinates use at least element_source (element_target optional). "
        "For browser_drag_and_drop use element_source+element_target. "
        "Or use coord_source_x+coord_source_y+coord_target_x+coord_target_y. "
        "Aliases source/target and source_x/source_y/target_x/target_y are accepted. "
        "Register your own via controllers.action.register_action."
    ),
)
async def browser_custom_action(
    action: str,
    session_id: str = "",
    request_id: str = "",
    params: dict[str, Any] | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    effective_session_id = _resolve_session_id(session_id, ctx)
    effective_request_id = _resolve_request_id(request_id, ctx)
    result = await run_action(
        action=action,
        session_id=effective_session_id,
        request_id=effective_request_id,
        **(params or {}),
    )
    if isinstance(result, dict) and str(result.get("error", "")).startswith("runtime_not_bound:"):
        runtime = await _get_runtime()
        bind_runtime(runtime)
        result = await run_action(
            action=action,
            session_id=effective_session_id,
            request_id=effective_request_id,
            **(params or {}),
        )
    return result


@mcp.tool(
    name="browser_list_custom_actions",
    description="List registered custom action names (for browser_custom_action).",
)
async def browser_list_custom_actions() -> dict[str, Any]:
    return {"ok": True, "actions": list_actions()}


@mcp.tool(
    name="browser_runtime_health",
    description="Return runtime readiness and selected model/provider config.",
)
async def browser_runtime_health() -> dict[str, Any]:
    settings = build_runtime_settings()
    return {
        "ok": True,
        "provider": settings.provider,
        "api_base": settings.api_base,
        "model_name": settings.model_name,
        "started": _runtime is not None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Playwright runtime MCP server")
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http", "http"],
        default=(os.getenv("PLAYWRIGHT_RUNTIME_MCP_TRANSPORT") or "stdio").strip().lower(),
        help="MCP transport mode. Use stdio for agent-launched server; sse/http for standalone server.",
    )
    parser.add_argument(
        "--host",
        default=(os.getenv("PLAYWRIGHT_RUNTIME_MCP_HOST") or "127.0.0.1").strip(),
        help="Host for sse/http transports.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int((os.getenv("PLAYWRIGHT_RUNTIME_MCP_PORT") or "8940").strip()),
        help="Port for sse/http transports.",
    )
    parser.add_argument(
        "--path",
        default=(os.getenv("PLAYWRIGHT_RUNTIME_MCP_PATH") or "").strip(),
        help="Optional custom endpoint path for sse/http transports.",
    )
    parser.add_argument(
        "--log-level",
        default=(os.getenv("PLAYWRIGHT_RUNTIME_MCP_LOG_LEVEL") or "INFO").strip(),
        help="Server log level.",
    )
    parser.add_argument(
        "--no-banner",
        action="store_true",
        help="Disable FastMCP startup banner.",
    )
    parser.add_argument(
        "--stateless-http",
        action="store_true",
        help=(
            "Enable stateless HTTP mode for http/streamable-http transports. "
            "Useful when clients intermittently lose transport session state."
        ),
    )
    return parser.parse_args()


def _apply_timeout_defaults() -> None:
    """Set runtime timeout defaults based on transport when not explicitly provided."""
    timeout_text = str(DEFAULT_BROWSER_TIMEOUT_S)
    os.environ.setdefault("BROWSER_TIMEOUT_S", timeout_text)
    os.environ.setdefault("PLAYWRIGHT_TOOL_TIMEOUT_S", os.getenv("BROWSER_TIMEOUT_S", timeout_text))


def _resolve_stateless_http(args: argparse.Namespace) -> bool:
    """Resolve whether to run HTTP transports in stateless mode."""
    if args.stateless_http:
        return True

    env_raw = (os.getenv("PLAYWRIGHT_RUNTIME_MCP_STATELESS_HTTP") or "").strip().lower()
    if env_raw in {"1", "true", "yes", "on"}:
        return True
    if env_raw in {"0", "false", "no", "off"}:
        return False

    # Default to stateless for streamable/http to avoid stateful session crashes.
    return args.transport in {"streamable-http", "http"}


def _configure_stdio_logging() -> None:
    """Prevent application logs from polluting stdio JSON-RPC output."""
    try:
        from openjiuwen.core.common.logging.default.log_config import log_config
        from openjiuwen.core.common.logging.manager import LogManager

        log_path = str((Path(REPO_ROOT) / "logs").resolve())
        log_config._log_config["log_path"] = log_path
        log_config._log_config["output"] = ["file"]
        log_config._log_config["interface_output"] = ["file"]
        log_config._log_config["performance_output"] = ["file"]

        if getattr(LogManager, "_initialized", False):
            for current_logger in LogManager.get_all_loggers().values():
                try:
                    current_config = current_logger.get_config()
                    current_config["output"] = ["file"]
                    current_logger.reconfigure(current_config)
                except Exception:
                    continue
    except Exception:
        return


def main() -> None:
    load_repo_dotenv()
    args = parse_args()
    if args.transport == "stdio":
        _configure_stdio_logging()
    _apply_timeout_defaults()
    kwargs: Dict[str, Any] = {
        "show_banner": not args.no_banner,
        "log_level": args.log_level,
    }
    if args.transport in {"sse", "streamable-http", "http"}:
        kwargs["host"] = args.host
        kwargs["port"] = args.port
        if args.path:
            kwargs["path"] = args.path
    if args.transport in {"streamable-http", "http"} and _resolve_stateless_http(args):
        kwargs["stateless_http"] = True
    mcp.run(transport=args.transport, **kwargs)


if __name__ == "__main__":
    main()
