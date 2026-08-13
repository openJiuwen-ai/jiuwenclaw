# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Helpers for converting ``config.yaml`` MCP entries to runtime configs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from typing import Any

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
    cfg: McpServerConfig, *, timeout: float | None = None
) -> tuple[bool, str]:
    """Reachability + auth probe for HTTP-based MCP servers.

    Plain httpx POST (not client.connect — that leaks anyio ghost tasks on
    401/timeout). Returns (reachable, reason). Non-HTTP transports skip.
    """
    transport = (getattr(cfg, "client_type", "") or "").strip().lower()
    if transport not in _HTTP_MCP_TRANSPORTS:
        return True, ""

    import httpx

    url = (getattr(cfg, "server_path", "") or "").strip()
    if not url:
        return False, "invalid url: empty"

    # Per-server timeout_s wins; else 10s default.
    raw_timeout = (getattr(cfg, "params", None) or {}).get("timeout_s")
    if isinstance(raw_timeout, (int, float)) and int(raw_timeout) > 0:
        read_t = float(int(raw_timeout))
    elif timeout is not None and timeout > 0:
        read_t = float(timeout)
    else:
        read_t = 10.0
    # Short connect (unreachable host fails fast); read catches no-response.
    http_timeout = httpx.Timeout(connect=min(read_t, 5.0), read=read_t, write=5.0, pool=5.0)

    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    # Caller headers live in params.headers and/or auth_headers.
    params = getattr(cfg, "params", None) or {}
    cfg_headers = params.get("headers") if isinstance(params, dict) else None
    if isinstance(cfg_headers, dict):
        headers.update({str(k): str(v) for k, v in cfg_headers.items()})
    auth_headers = getattr(cfg, "auth_headers", None)
    if isinstance(auth_headers, dict):
        headers.update({str(k): str(v) for k, v in auth_headers.items()})
    # auth_query_params goes to the URL query string, matching the real connect.
    query_params = getattr(cfg, "auth_query_params", None)
    if isinstance(query_params, dict):
        query_params = {str(k): str(v) for k, v in query_params.items()}
    else:
        query_params = None

    # Body must mirror the mcp SDK's initialize exactly: some gateways (e.g.
    # GitHub Copilot) return a body-format 400 before auth, masking a real 401.
    protocol_version = ""
    try:
        from mcp.types import LATEST_PROTOCOL_VERSION
        protocol_version = LATEST_PROTOCOL_VERSION
    except Exception:  # noqa: BLE001
        protocol_version = "2025-06-18"
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "method": "initialize",
            "id": 0,
            "params": {
                "protocolVersion": protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "mcp", "version": "0.1.0"},
            },
        }
    )

    try:
        async with httpx.AsyncClient(timeout=http_timeout, follow_redirects=False) as http:
            resp = await http.post(url, headers=headers, params=query_params, content=body)
    except httpx.TimeoutException as exc:
        return False, f"http probe timed out after {read_t}s (server not responding): {type(exc).__name__}"
    except (httpx.ConnectError, httpx.NetworkError, httpx.UnsupportedProtocol) as exc:
        return False, f"unreachable: {type(exc).__name__}: {exc}"
    except httpx.InvalidURL as exc:
        return False, f"invalid url: {exc}"
    except Exception as exc:  # noqa: BLE001 — probe must never raise
        return False, f"probe failed: {type(exc).__name__}: {exc}"

    if resp.status_code in (401, 403):
        return False, f"auth rejected (HTTP {resp.status_code})"
    # Other 4xx/5xx (e.g. malformed-auth 400) is just as fatal at cold-start
    # as 401: raise_for_status() corrupts the anyio task group either way.
    # Gate strictly — a healthy server answers 2xx/3xx to a well-formed probe.
    if resp.status_code >= 400:
        snippet = ""
        try:
            snippet = (resp.text or "")[:120].replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
        return False, f"http {resp.status_code} from server{(': ' + snippet) if snippet else ''}"
    return True, f"ok (http {resp.status_code})"


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
