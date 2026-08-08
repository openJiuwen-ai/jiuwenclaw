# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests: ${VAR} placeholder resolution at McpServerConfig build time.

Gap-1 fix (plan B): config.yaml stores placeholders (``${EMAIL_USER}``) —
never real token values. Resolution happens when building the runtime
McpServerConfig: the adapter layer's ``_build_mcp_server_config`` consults
the CredentialStore (keyed by MCP name) + os.environ and substitutes
real values into ``params.env`` / ``headers``, so the spawned stdio process
gets real credentials while config.yaml stays secret-free.

These tests cover the resolution helper + the adapter-layer integration.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from jiuwenswarm.common.mcp_config import build_mcp_server_config


# --- build_mcp_server_config with a credential_resolver callback ---

def _make_resolver(store_map: dict[str, str]):
    """A resolver that looks up ${VAR} by name in store_map, then os.environ."""
    def resolver(key: str) -> str | None:
        if key in store_map:
            return store_map[key]
        return os.environ.get(key)
    return resolver


def test_stdio_env_placeholders_resolved_via_resolver() -> None:
    """${VAR} in env is replaced with real values from the resolver."""
    entry = {
        "name": "gmail",
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "mcp-email"],
        "env": {
            "EMAIL_USER": "${EMAIL_USER}",
            "EMAIL_PASSWORD": "${EMAIL_PASSWORD}",
            "EMAIL_TYPE": "gmail",  # no placeholder, kept as-is
        },
        "enabled": True,
        "server_id_scope": "mcp:gmail",
    }
    resolver = _make_resolver({"EMAIL_USER": "real@gmail.com", "EMAIL_PASSWORD": "secret123"})
    cfg = build_mcp_server_config(entry, credential_resolver=resolver)
    assert cfg is not None
    assert cfg.params["env"]["EMAIL_USER"] == "real@gmail.com"
    assert cfg.params["env"]["EMAIL_PASSWORD"] == "secret123"
    assert cfg.params["env"]["EMAIL_TYPE"] == "gmail"


def test_stdio_env_placeholder_missing_kept_literal(monkeypatch) -> None:
    """A placeholder with no resolver hit stays as ${VAR} (caller can detect)."""
    monkeypatch.delenv("NONEXISTENT_VAR", raising=False)
    entry = {
        "name": "x", "transport": "stdio", "command": "cmd",
        "env": {"K": "${NONEXISTENT_VAR}"}, "enabled": True,
    }
    cfg = build_mcp_server_config(entry, credential_resolver=_make_resolver({}))
    assert cfg.params["env"]["K"] == "${NONEXISTENT_VAR}"


def test_stdio_env_resolves_from_os_environ(monkeypatch) -> None:
    """When resolver has no store entry, falls back to os.environ."""
    monkeypatch.setenv("MY_MCP_TOKEN", "env-token-xyz")
    entry = {
        "name": "x", "transport": "stdio", "command": "cmd",
        "env": {"TOKEN": "${MY_MCP_TOKEN}"}, "enabled": True,
    }
    cfg = build_mcp_server_config(entry, credential_resolver=_make_resolver({}))
    assert cfg.params["env"]["TOKEN"] == "env-token-xyz"


def test_no_resolver_keeps_placeholders(monkeypatch) -> None:
    """Without a resolver, placeholders stay literal (backward compat)."""
    monkeypatch.delenv("EMAIL_USER", raising=False)
    entry = {
        "name": "x", "transport": "stdio", "command": "cmd",
        "env": {"EMAIL_USER": "${EMAIL_USER}"}, "enabled": True,
    }
    cfg = build_mcp_server_config(entry)  # no resolver
    assert cfg.params["env"]["EMAIL_USER"] == "${EMAIL_USER}"


def test_http_headers_placeholders_resolved() -> None:
    """${VAR} in headers is resolved into auth_headers (github Authorization).

    openjiuwen's SseClient / StreamableHttpClient read ``config.auth_headers``
    (not ``params.headers``) — the resolved headers MUST land in auth_headers
    or the remote MCP gets no Authorization and returns 0 tools.
    """
    entry = {
        "name": "github",
        "transport": "streamable-http",
        "url": "https://api.githubcopilot.com/mcp/",
        "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"},
        "enabled": True,
        "server_id_scope": "mcp:github",
    }
    resolver = _make_resolver({"GITHUB_TOKEN": "ghp_abc123"})
    cfg = build_mcp_server_config(entry, credential_resolver=resolver)
    assert cfg.auth_headers["Authorization"] == "Bearer ghp_abc123"


def test_sse_headers_go_to_auth_headers() -> None:
    """sse transport: resolved headers must populate auth_headers too."""
    entry = {
        "name": "baidu-netdisk",
        "transport": "sse",
        "url": "https://mcp-pan.baidu.com/sse",
        "headers": {"Authorization": "Bearer ${BAIDU_ACCESS_TOKEN}"},
        "enabled": True,
    }
    resolver = _make_resolver({"BAIDU_ACCESS_TOKEN": "tok-xyz"})
    cfg = build_mcp_server_config(entry, credential_resolver=resolver)
    assert cfg.auth_headers["Authorization"] == "Bearer tok-xyz"
    assert cfg.server_path == "https://mcp-pan.baidu.com/sse"


def test_url_placeholder_resolved() -> None:
    """${VAR} in url query is resolved (gildata ?token=${GILDATA_TOKEN})."""
    entry = {
        "name": "gildata", "transport": "http",
        "url": "https://api.gildata.com/mcp?token=${GILDATA_TOKEN}",
        "enabled": True,
    }
    resolver = _make_resolver({"GILDATA_TOKEN": "tok-xyz"})
    cfg = build_mcp_server_config(entry, credential_resolver=resolver)
    assert cfg.server_path == "https://api.gildata.com/mcp?token=tok-xyz"


def test_stdio_missing_args_defaults_to_empty_list() -> None:
    """A stdio entry with no args field must still produce params.args=[] —
    openjiuwen's StdioServerParameters rejects args=None ("Input should be a
    valid list"). This is the bare-command custom-MCP regression."""
    entry = {
        "name": "bare", "transport": "stdio",
        "command": "my-mcp-server",  # no args key
        "enabled": True, "server_id_scope": "mcp:bare",
    }
    from jiuwenswarm.common.mcp_config import build_mcp_server_config
    cfg = build_mcp_server_config(entry)
    assert cfg is not None
    assert cfg.client_type == "stdio"
    assert cfg.params["command"] == "my-mcp-server"
    assert cfg.params["args"] == []
