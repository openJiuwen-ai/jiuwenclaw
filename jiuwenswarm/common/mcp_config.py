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


def extract_enabled_mcp_server_entries(config_base: dict[str, Any]) -> list[dict[str, Any]]:
    """Return enabled ``mcp.servers`` entries from a resolved config mapping."""
    if not isinstance(config_base, dict):
        return []
    mcp_cfg = config_base.get("mcp", {})
    if not isinstance(mcp_cfg, dict):
        return []
    servers = mcp_cfg.get("servers", [])
    if not isinstance(servers, list):
        return []

    result: list[dict[str, Any]] = []
    for item in servers:
        if not isinstance(item, dict):
            continue
        if not bool(item.get("enabled", True)):
            continue
        result.append(item)
    return result


def build_mcp_server_config(
    entry: dict[str, Any],
    *,
    server_id_scope: str | None = None,
) -> McpServerConfig | None:
    """Build a ``McpServerConfig`` from one ``mcp.servers`` entry.

    Args:
        entry: One config entry under ``mcp.servers``.
        server_id_scope: Optional scope used to derive a stable ``server_id``.
            When omitted, openjiuwen's default random id behavior is preserved.
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
        cwd = entry.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            params["cwd"] = cwd.strip()
        env = entry.get("env")
        if isinstance(env, dict):
            params["env"] = {str(k): str(v) for k, v in env.items()}
        timeout_s = entry.get("timeout_s")
        if isinstance(timeout_s, (int, float)) and int(timeout_s) > 0:
            params["timeout_s"] = int(timeout_s)
        payload["server_path"] = f"stdio://{name}"
        payload["params"] = params
    else:
        url = str(entry.get("url", "")).strip()
        if not url:
            return None
        payload["server_path"] = url
        params: dict[str, Any] = {}
        headers = entry.get("headers")
        if isinstance(headers, dict):
            normalized_headers = {str(k): str(v) for k, v in headers.items()}
            params["headers"] = normalized_headers
            # StreamableHttpClient sends auth via ``auth_headers`` (routed
            # through ``_auth_provider``), not ``params.headers``. Mirror so
            # both the probe and real connect carry Authorization (otherwise
            # 401 → anyio task-group corruption).
            payload["auth_headers"] = dict(normalized_headers)
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

    Uses a plain ``httpx.AsyncClient`` POST (never ``client.connect()``, which
    enters the mcp SDK's anyio task group and leaks ghost tasks on 401/timeout
    — the "restart then can't chat" symptom). Catches: 401/403 (auth rejected),
    timeout (server not responding), connect error (unreachable). Other status
    codes defer to the real connect path; this probe only guards reachability
    and auth, not protocol correctness.

    Non-HTTP transports report reachable (no cheap probe). Shared by the
    config-time ``_pre_check_mcp_http_auth`` and cold-start
    ``_register_mcp_server`` so both gates stay identical.
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


__all__ = [
    "build_enabled_mcp_server_configs",
    "build_mcp_server_config",
    "extract_enabled_mcp_server_entries",
    "preflight_mcp_server_reachable",
]
