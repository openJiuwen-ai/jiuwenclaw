# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests: register_custom_connector stdio command/args handling + transport normalize.

Two bugs fixed:
  1. A custom stdio MCP typed as "npx -y bing-cn-mcp" in the command field
     (args empty) failed: openjiuwen's StdioServerParameters rejects args=None.
     Now the full invocation is split into command + args; bare commands get [].
  2. A custom remote MCP typed as transport="http" failed because openjiuwen's
     client registry doesn't know "http" (only sse/stdio/streamable-http).
     Now register_custom normalizes http -> streamable-http.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jiuwenswarm.server.runtime.mcp import registry


def _patch_state_store(monkeypatch):
    """register_custom writes to state.json; stub it to a no-op."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(state_store, "upsert_mcp_record", lambda *a, **kw: None)


def test_stdio_full_invocation_split_into_command_and_args(monkeypatch) -> None:
    """User types "npx -y bing-cn-mcp" in command, args empty → split into
    command=npx, args=["-y", "bing-cn-mcp"]."""
    _patch_state_store(monkeypatch)
    captured: dict = {}

    def _capture(n, entry, **kw):
        captured.update(entry)
        return entry

    with patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=_capture):
        result = registry.register_custom_mcp("test-bing", {
            "transport": "stdio",
            "command": "npx -y bing-cn-mcp",
        })
    assert result["command"] == "npx"
    assert result["args"] == ["-y", "bing-cn-mcp"]


def test_stdio_bare_command_gets_empty_args_list(monkeypatch) -> None:
    """A single-token command (no args) gets args=[] so StdioServerParameters
    doesn't reject args=None."""
    _patch_state_store(monkeypatch)
    captured: dict = {}

    def _capture(n, entry, **kw):
        captured.update(entry)
        return entry

    with patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=_capture):
        result = registry.register_custom_mcp("bare", {
            "transport": "stdio",
            "command": "my-mcp-server",
        })
    assert result["command"] == "my-mcp-server"
    assert result["args"] == []


def test_stdio_explicit_args_not_overwritten(monkeypatch) -> None:
    """If the user supplied args explicitly, the command is NOT split — args
    is used as-is and command stays verbatim."""
    _patch_state_store(monkeypatch)
    captured: dict = {}

    def _capture(n, entry, **kw):
        captured.update(entry)
        return entry

    with patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=_capture):
        result = registry.register_custom_mcp("explicit", {
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "bing-cn-mcp"],
        })
    assert result["command"] == "npx"
    assert result["args"] == ["-y", "bing-cn-mcp"]


def test_http_transport_normalized_to_streamable_http(monkeypatch) -> None:
    """transport="http" normalizes to "streamable-http" (openjiuwen only knows
    sse/stdio/streamable-http)."""
    _patch_state_store(monkeypatch)
    captured: dict = {}

    def _capture(n, entry, **kw):
        captured.update(entry)
        return entry

    with patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=_capture):
        result = registry.register_custom_mcp("remote", {
            "transport": "http",
            "url": "https://example.com/mcp",
        })
    assert result["transport"] == "streamable-http"
    assert result["url"] == "https://example.com/mcp"


def test_sse_transport_kept_verbatim(monkeypatch) -> None:
    """sse is a real openjiuwen client; normalize leaves it as sse."""
    _patch_state_store(monkeypatch)
    captured: dict = {}

    def _capture(n, entry, **kw):
        captured.update(entry)
        return entry

    with patch("jiuwenswarm.server.runtime.mcp.state_store.upsert_mcp_record",
               side_effect=_capture):
        result = registry.register_custom_mcp("sse-conn", {
            "transport": "sse",
            "url": "https://example.com/sse",
        })
    assert result["transport"] == "sse"


def test_custom_mcp_appears_in_list_after_register(tmp_path: Path, monkeypatch) -> None:
    """A registered custom MCP (no marketplace package) surfaces in
    list_marketplace_mcps as disconnected (registered, not connected) —
    the user clicks connect to activate it. A connected custom MCP shows as
    connected."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    # registered (not connected) — what register_custom produces
    state_store.upsert_mcp_record(
        "my-custom", {
            "name": "my-custom", "transport": "stdio",
            "command": "npx", "args": ["-y", "x"],
            "server_id_scope": "mcp:my-custom",
        },
        state="registered", enabled=True,
        integration_type="stdio-mcp",
    )
    # connected — what connect produces after the user clicks connect
    state_store.upsert_mcp_record(
        "live-custom", {
            "name": "live-custom", "transport": "streamable-http",
            "url": "https://x/mcp", "server_id_scope": "mcp:live-custom",
        },
        state="connected", enabled=True,
        integration_type="remote-mcp",
    )
    summaries = registry.list_marketplace_mcps()
    by_name = {s["name"]: s for s in summaries}
    assert "my-custom" in by_name
    assert by_name["my-custom"]["connection_state"] == "disconnected"
    assert by_name["my-custom"]["integration_type"] == "stdio-mcp"
    assert "live-custom" in by_name
    assert by_name["live-custom"]["connection_state"] == "connected"
    # enabled must be false when not connected (my-custom is registered,
    # not connected). enabled is only meaningful after connect.
    assert by_name["my-custom"]["enabled"] is False
    assert by_name["my-custom"]["connected"] is False
    # connected custom MCP reflects its state.json enabled flag (default True).
    assert by_name["live-custom"]["enabled"] is True
    assert by_name["live-custom"]["connected"] is True


def test_get_connector_returns_detail_for_custom_mcp(tmp_path: Path, monkeypatch) -> None:
    """get_mcp (mcp.show) returns a detail for a custom MCP with
    no marketplace package, so the frontend's tools panel (which requires
    detail != null) can render."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    state_store.upsert_mcp_record(
        "remote-custom", {"name": "remote-custom", "transport": "streamable-http",
                          "url": "https://x/mcp", "server_id_scope": "mcp:remote-custom"},
        state="connected", enabled=True,
        integration_type="remote-mcp",
    )
    detail = registry.get_mcp("remote-custom")
    assert detail is not None
    assert detail["name"] == "remote-custom"
    assert detail["connection_state"] == "connected"
    # connected custom MCP reflects its enabled flag in show too.
    assert detail["enabled"] is True
    assert detail["connected"] is True
    # show surfaces skills/tools (empty for a custom MCP with no package).
    assert detail["skills"] == []
    assert detail["tools"] == []


def test_list_enabled_false_when_disconnected_even_if_state_enabled_true(tmp_path: Path, monkeypatch) -> None:
    """An MCP with state.json enabled=true but state=registered (not connected)
    must still report enabled=false — enabled is only meaningful when
    connected. Pins the rule that disconnected MCPs are never 'enabled'."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    # registered but enabled=true in state.json (a stale enabled from a prior
    # connected session that was later disconnected) — list must still show
    # enabled=false because it's not connected.
    state_store.upsert_mcp_record(
        "stale-custom", {
            "name": "stale-custom", "transport": "stdio",
            "command": "npx", "args": ["-y", "x"],
            "server_id_scope": "mcp:stale-custom",
        },
        state="registered", enabled=True,
        integration_type="stdio-mcp",
    )
    summaries = registry.list_marketplace_mcps()
    by_name = {s["name"]: s for s in summaries}
    assert by_name["stale-custom"]["connected"] is False
    assert by_name["stale-custom"]["enabled"] is False


def test_connect_custom_mcp_flips_registered_to_connected(tmp_path: Path, monkeypatch) -> None:
    """connect on a registered custom MCP (no marketplace package) flips its
    state to connected and returns the definition — no longer raises KeyError.
    Regression: clicking connect on a registered custom MCP used to fail with
    "mcp not found" because connect_mcp only looked for a package
    dir."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    state_store.upsert_mcp_record(
        "my-custom", {
            "name": "my-custom", "transport": "stdio",
            "command": "npx", "args": ["-y", "x"],
            "server_id_scope": "mcp:my-custom",
        },
        state="registered", enabled=True,
        integration_type="stdio-mcp",
    )
    result = registry.connect_mcp("my-custom")
    assert result["name"] == "my-custom"
    assert result["integration_type"] == "stdio-mcp"
    rec = state_store.get_mcp_record("my-custom")
    assert rec["state"] == "connected"


def test_connect_custom_mcp_not_in_state_raises_keyerror(tmp_path: Path, monkeypatch) -> None:
    """connect on a name that is neither a marketplace package nor in state.json
    raises KeyError (genuine not found)."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    with pytest.raises(KeyError):
        registry.connect_mcp("ghost-custom")


def test_disconnect_custom_mcp_back_to_registered(tmp_path: Path, monkeypatch) -> None:
    """disconnect on a connected custom MCP flips state back to registered
    (keeps the definition so the user can re-connect without re-registering)."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    state_store.upsert_mcp_record(
        "my-custom", {"name": "my-custom", "transport": "streamable-http",
                      "url": "https://x", "server_id_scope": "mcp:my-custom"},
        state="connected", enabled=True,
        integration_type="remote-mcp",
    )
    result = registry.disconnect_mcp("my-custom")
    assert result["removed"] is True
    rec = state_store.get_mcp_record("my-custom")
    assert rec["state"] == "registered"


def test_register_custom_preserves_connected_state_on_edit(tmp_path: Path, monkeypatch) -> None:
    """Editing a connected custom MCP keeps state=connected and returns
    was_connected=True so the handler knows to remove+re-add the live
    instance. The new config fields (url here) replace the old ones."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    # Seed a connected custom MCP with an old URL.
    state_store.upsert_mcp_record(
        "live-custom", {"name": "live-custom", "transport": "streamable-http",
                        "url": "https://old.example/mcp",
                        "server_id_scope": "mcp:live-custom"},
        state="connected", enabled=True,
        integration_type="remote-mcp",
    )
    # Re-register with a new URL (the edit path).
    result = registry.register_custom_mcp("live-custom", {
        "transport": "streamable-http",
        "url": "https://new.example/mcp",
    })
    # was_connected flag is returned to the handler (not persisted).
    assert result["was_connected"] is True
    assert result["url"] == "https://new.example/mcp"
    # state.json keeps connected + the new URL.
    rec = state_store.get_mcp_record("live-custom")
    assert rec["state"] == "connected"
    assert rec["url"] == "https://new.example/mcp"


def test_register_custom_preserves_enabled_on_edit(tmp_path: Path, monkeypatch) -> None:
    """Editing a disabled connected custom MCP keeps enabled=False — the
    edit dialog changing fields is not an implicit enable."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    state_store.upsert_mcp_record(
        "disabled-custom", {"name": "disabled-custom", "transport": "stdio",
                            "command": "npx", "args": ["-y", "x"],
                            "server_id_scope": "mcp:disabled-custom"},
        state="connected", enabled=False,
        integration_type="stdio-mcp",
    )
    result = registry.register_custom_mcp("disabled-custom", {
        "transport": "stdio",
        "command": "npx -y y",
    })
    assert result["was_connected"] is True
    rec = state_store.get_mcp_record("disabled-custom")
    assert rec["enabled"] is False


def test_register_custom_new_mcp_defaults_registered_and_enabled(tmp_path: Path, monkeypatch) -> None:
    """A brand-new custom MCP (no prior record) is written as state=registered,
    enabled=True, was_connected=False — the handler then flips to connected."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    result = registry.register_custom_mcp("brand-new", {
        "transport": "stdio",
        "command": "my-server",
    })
    assert result["was_connected"] is False
    rec = state_store.get_mcp_record("brand-new")
    assert rec["state"] == "registered"
    assert rec["enabled"] is True


def test_get_mcp_custom_returns_config_fields_for_edit_prefill(tmp_path: Path, monkeypatch) -> None:
    """get_mcp (mcp.show) echoes transport/command/args/env/url/headers for a
    custom MCP so the edit dialog can pre-fill the form."""
    from jiuwenswarm.server.runtime.mcp import state_store
    monkeypatch.setattr(registry, "get_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(state_store, "get_workspace_dir", lambda: tmp_path)
    state_store.upsert_mcp_record(
        "remote-custom", {
            "name": "remote-custom", "transport": "streamable-http",
            "url": "https://x/mcp", "server_id_scope": "mcp:remote-custom",
            "headers": {"Authorization": "Bearer abc"},
            "env": {"FOO": "bar"},
            "timeout_s": 30,
        },
        state="connected", enabled=True,
        integration_type="remote-mcp",
    )
    detail = registry.get_mcp("remote-custom")
    assert detail is not None
    assert detail["transport"] == "streamable-http"
    assert detail["url"] == "https://x/mcp"
    assert detail["headers"] == {"Authorization": "Bearer abc"}
    assert detail["env"] == {"FOO": "bar"}
    assert detail["timeout_s"] == 30
