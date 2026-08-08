# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for converting ``config.yaml`` MCP entries to runtime configs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any
from urllib.parse import urlparse

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig

_HTTP_MCP_TRANSPORTS = frozenset({"sse", "http", "streamable-http", "streamable_http"})


def extract_enabled_mcp_server_entries(config_base: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return enabled MCP server entries — merges config.yaml + state.json.

    MCP connection/enabled state lives in ``mcp/state.json``; ``get_mcp_servers``
    merges that with config.yaml's hand-written ``mcp.servers``. This wrapper
    filters to enabled entries so the adapter's init/sync paths register only
    what's on. ``config_base`` is accepted for callers that pass a resolved
    snapshot, but the authoritative source is the merged ``get_mcp_servers()``
    list — config_base's mcp.servers would miss state.json MCPs.
    """
    from jiuwenswarm.common.config import get_mcp_servers
    try:
        servers = get_mcp_servers()
    except Exception:  # noqa: BLE001
        # Fallback to the passed config_base if the store read fails (e.g.
        # during early bootstrap before workspace is set up).
        servers = []
        if isinstance(config_base, dict):
            mcp_cfg = config_base.get("mcp", {})
            if isinstance(mcp_cfg, dict):
                servers = mcp_cfg.get("servers", []) or []

    result: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        result.append(item)
    return result


_PLACEHOLDER_RE = re.compile(r"\$\{(\w+)\}")


def _resolve_string(value: str, resolver) -> str:
    """Substitute ${VAR} in a string via the resolver; missing stays literal."""
    if not resolver or not isinstance(value, str):
        return value
    return _PLACEHOLDER_RE.sub(
        lambda m: resolver(m.group(1)) or m.group(0), value
    )


def build_mcp_server_config(
    entry: dict[str, Any],
    *,
    server_id_scope: str | None = None,
    credential_resolver=None,
) -> McpServerConfig | None:
    """Build a ``McpServerConfig`` from one ``mcp.servers`` entry.

    Args:
        entry: One config entry under ``mcp.servers``.
        server_id_scope: Optional scope used to derive a stable ``server_id``.
            When omitted, openjiuwen's default random id behavior is preserved.
        credential_resolver: Optional ``Callable[[str], str | None]`` for
            ``${VAR}`` placeholder resolution. When provided, placeholders in
            env/headers/url are replaced with real values at runtime — so
            config.yaml can store placeholders (no plaintext tokens) while the
            spawned stdio process / HTTP request gets real credentials.
            When None (default), placeholders stay literal (backward compat).
    """
    name = str(entry.get("name", "")).strip()
    if not name:
        return None
    transport = str(entry.get("transport", "")).strip().lower()
    if transport not in {"stdio", "sse", "http", "streamable-http", "streamable_http"}:
        return None

    payload: dict[str, Any] = {
        "server_name": name,
        "client_type": transport,
    }
    explicit_server_id = str(entry.get("server_id", "") or "").strip()
    if explicit_server_id:
        payload["server_id"] = explicit_server_id

    if transport == "stdio":
        command = str(entry.get("command", "")).strip()
        if not command:
            return None
        params: dict[str, Any] = {"command": command}
        args = entry.get("args")
        if isinstance(args, list):
            params["args"] = [str(item) for item in args]
        else:
            # Default to [] so a bare command (no args) still spawns cleanly.
            params["args"] = []
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            params["cwd"] = cwd.strip()
        env = entry.get("env")
        if isinstance(env, dict):
            # resolve ${VAR} placeholders via credential_resolver
            params["env"] = {str(k): _resolve_string(str(v), credential_resolver) for k, v in env.items()}
        timeout_s = entry.get("timeout_s")
        if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
            params["timeout_s"] = int(timeout_s)
        payload["server_path"] = f"stdio://{name}"
        payload["params"] = params
    else:
        url = str(entry.get("url", "")).strip()
        if not url:
            return None
        # resolve ${VAR} in url (e.g. gildata ?token=${GILDATA_TOKEN})
        payload["server_path"] = _resolve_string(url, credential_resolver)
        params: dict[str, Any] = {}
        headers = entry.get("headers")
        if isinstance(headers, dict):
            # openjiuwen's SseClient / StreamableHttpClient read auth_headers
            # (NOT params.headers) — resolved headers MUST populate
            # auth_headers or the remote MCP gets no Authorization and
            # silently returns 0 tools. auth_query_params stays empty;
            # query tokens in the url are resolved via _resolve_string above.
            payload["auth_headers"] = {
                str(k): _resolve_string(str(v), credential_resolver)
                for k, v in headers.items()
            }
        timeout_s = entry.get("timeout_s")
        if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
            params["timeout_s"] = int(timeout_s)
        if params:
            payload["params"] = params

    if server_id_scope and "server_id" not in payload:
        payload["server_id"] = _stable_mcp_server_id(server_id_scope, name, payload)

    return McpServerConfig(**payload)


def build_enabled_mcp_server_configs(
    config_base: dict[str, Any],
    *,
    server_id_scope: str | None = None,
) -> list[McpServerConfig]:
    """Build all enabled MCP server configs, skipping invalid entries."""
    configs: list[McpServerConfig] = []
    for entry in extract_enabled_mcp_server_entries(config_base):
        cfg = build_mcp_server_config(entry, server_id_scope=server_id_scope)
        if cfg is not None:
            configs.append(cfg)
    return configs


async def preflight_mcp_server_reachable(
    cfg: McpServerConfig, *, timeout: float = 3.0
) -> tuple[bool, str]:
    """Cheap reachability probe for HTTP-based MCP servers.

    Probing host:port first lets us skip registration cleanly when the server
    is unreachable: openjiuwen's ``streamablehttp_client`` async context spins
    up an anyio task group with background request tasks before
    ``session.initialize()`` fails, and tearing that down leaks orphaned
    tasks with noisy ``aclose`` / cancel-scope errors.

    Returns ``(reachable, reason)``. Non-HTTP transports (stdio/playwright/…)
    report reachable — they are spawned locally and have no cheap probe.
    """
    transport = (getattr(cfg, "client_type", "") or "").strip().lower()
    if transport not in _HTTP_MCP_TRANSPORTS:
        return True, ""

    url = (getattr(cfg, "server_path", "") or "").strip()
    parsed = urlparse(url)
    host = parsed.hostname
    if not host:
        return False, f"invalid url: {url!r}"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except asyncio.TimeoutError:
        return False, f"tcp connect to {host}:{port} timed out after {timeout}s"
    except Exception as exc:
        # Connection refused / DNS failure / etc. — also defensive: the probe
        # itself must never break startup with an unexpected exception type.
        return False, f"tcp connect to {host}:{port} failed: {type(exc).__name__}: {exc}"

    writer.close()
    try:
        await writer.wait_closed()
    except Exception as exc:
        logger.debug(
            "[mcp-preflight] reachability probe socket close failed for %s:%s: %r",
            host, port, exc,
        )
    return True, ""


def _stable_mcp_server_id(scope: str, name: str, payload: dict[str, Any]) -> str:
    stable_payload = {
        key: value
        for key, value in payload.items()
        if key != "server_id"
    }
    raw = json.dumps(
        {"scope": scope, "payload": stable_payload},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    safe_scope = _safe_id_part(scope, default="scope")
    safe_name = _safe_id_part(name, default="server")
    return f"mcp_{safe_scope}_{safe_name}_{digest}"


def _safe_id_part(value: str, *, default: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_").lower()
    return (normalized or default)[:48]


async def fetch_mcp_tools_via_temp_connection(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Create a temporary MCP connection from a config entry and list its tools.

    Shared by the gateway-layer ``mcp.show`` (to fill ``detail.tools`` for a
    connected MCP) and the agent server. Resolves ``${VAR}`` placeholders
    so form-B (token) connectors connect with real credentials. The temp
    client is disconnected in ``finally`` — no lingering process.

    Returns ``[{id, name, description, parameters, server_name}]`` (the full
    tool card shape). Empty when the entry is invalid, the server is
    unreachable, or the MCP exposes no tools. Each failure mode is swallowed
    and logged — a failed tools fetch must not break mcp.show.
    """
    from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr

    name = str(entry.get("name", "")).strip()
    transport = str(entry.get("transport", "")).strip().lower()
    if not name or transport not in {"stdio", "sse", "http", "streamable-http", "streamable_http"}:
        logger.debug("[mcp-config] fetch skipped: name=%r transport=%r", name, transport)
        return []

    # Resolve ${VAR} placeholders from the CredentialStore so form-B
    # (token) connectors send real credentials, not the literal
    # ``Bearer ${BAIDU_ACCESS_TOKEN}`` string. No-op for form-A entries
    # (no placeholders) and for non-MCP entries (empty store).
    try:
        from jiuwenswarm.server.runtime.mcp.credential import (
            CredentialStore,
            resolve_placeholders,
        )
        entry = resolve_placeholders(entry, CredentialStore(), name)
    except Exception as resolve_exc:  # noqa: BLE001
        logger.debug("[mcp-config] placeholder resolve failed: %s", resolve_exc)

    cfg = build_mcp_server_config(entry)
    if cfg is None:
        logger.debug("[mcp-config] fetch skipped: invalid entry name=%r", name)
        return []
    client = ToolMgr._create_client(cfg)
    try:
        connected = await client.connect()
        if not connected:
            return []
        cards = await client.list_tools()
        tools_info: list[dict[str, Any]] = []
        for card in (cards or []):
            params_schema = card.input_params if hasattr(card, "input_params") else {}
            if hasattr(params_schema, "model_dump"):
                params_schema = params_schema.model_dump()
            tools_info.append({
                "id": card.id,
                "name": card.name,
                "description": card.description or "",
                "parameters": params_schema,
                "server_name": name,
            })
        return tools_info
    except BaseException as exc:  # noqa: BLE001
        # The mcp SDK's streamablehttp_client raises a BaseExceptionGroup
        # (containing the real HTTPStatusError + a GeneratorExit from
        # task-group teardown) on server errors like 403. That group is NOT
        # an Exception subclass, so it would escape `except Exception` and
        # hang the caller. Coerce it back into a catchable Exception.
        from jiuwenswarm.server.runtime.mcp.exc_group import reraise_as_exception
        reraise_as_exception(exc)
    finally:
        try:
            await client.disconnect()
        except BaseException as exc:  # noqa: BLE001
            # disconnect can raise the same BaseExceptionGroup (SSE/HTTP
            # task-group teardown) as the call path; without this it would
            # escape the finally and replace the original exception. Coerce
            # it back to an Exception so logging + the caller's
            # ``except Exception`` still work.
            if isinstance(exc, (Exception, asyncio.CancelledError)):
                logger.warning("[mcp-config] fetch disconnect failed: %s", exc)
            else:
                from jiuwenswarm.server.runtime.mcp.exc_group import (
                    reraise_as_exception,
                )
                try:
                    reraise_as_exception(exc)
                except Exception as disc_exc:  # noqa: BLE001
                    logger.warning("[mcp-config] fetch disconnect failed: %s", disc_exc)


async def fill_mcp_tools_fallback(item: dict[str, Any], name: str) -> None:
    """Populate ``item["tools"]`` from a temp connection when the connected
    MCP surfaced no tools via ToolMgr.

    Shared by the gateway-layer ``mcp.show`` and the agent ``mcp.show`` so the
    fallback logic (connected + empty tools -> temp connect -> list -> map to
    ``{name, description}``) lives in one place. No-op when ``item`` is not a
    connected MCP or already has tools. All failure modes are swallowed and
    logged — a failed tools fetch must not break mcp.show.
    """
    if not isinstance(item, dict):
        return
    if str(item.get("connection_state", "") or "") != "connected":
        return
    if item.get("tools"):
        return
    try:
        from jiuwenswarm.common.config import get_mcp_server_config
        config_entry = get_mcp_server_config(name)
        if not config_entry or not bool(config_entry.get("enabled", True)):
            return
        fetched = await fetch_mcp_tools_via_temp_connection(config_entry)
        if fetched:
            item["tools"] = [
                {"name": str(t.get("name", "") or ""),
                 "description": str(t.get("description", "") or "")}
                for t in fetched
            ]
    except Exception as fetch_exc:  # noqa: BLE001
        logger.debug("[mcp-config] show tools fallback for '%s' failed: %s", name, fetch_exc)


__all__ = [
    "build_enabled_mcp_server_configs",
    "build_mcp_server_config",
    "extract_enabled_mcp_server_entries",
    "preflight_mcp_server_reachable",
    "fetch_mcp_tools_via_temp_connection",
    "fill_mcp_tools_fallback",
]
