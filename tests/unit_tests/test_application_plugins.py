from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jiuwenswarm.extensions.application_host import (
    application_plugin_manifest,
    application_plugin_settings_payload,
    mount_application_plugin_http_routes,
)
from jiuwenswarm.extensions.loader import ExtensionLoader
from jiuwenswarm.extensions.registry import ExtensionRegistry
from jiuwenswarm.extensions.sdk import ApplicationPluginExtension
from jiuwenswarm.extensions.types import ExtensionMetadata


VIDEO_PLUGIN_ROOT = (
    Path(__file__).resolve().parents[2]
    / "jiuwenswarm"
    / "extensions"
    / "video_duplex"
)


class _FakeChannel:
    def __init__(self) -> None:
        self.methods: dict[str, object] = {}
        self.local_only: set[str] = set()

    def register_method(self, method, handler, *, local_only=False) -> None:  # noqa: ANN001
        self.methods[method] = handler
        if local_only:
            self.local_only.add(method)

    async def send_response(self, ws, req_id, **payload) -> None:  # noqa: ANN001
        ws.append({"id": req_id, **payload})


class _ConfigurablePlugin(ApplicationPluginExtension):
    plugin_id = "example-plugin"

    def __init__(self, state_path: Path) -> None:
        self.state_path = state_path

    @property
    def metadata(self) -> ExtensionMetadata:
        return ExtensionMetadata(
            id=self.plugin_id,
            name="Example plugin",
            version="1.0.0",
            description="Test application plugin",
            author="tests",
            min_jiuwenswarm_version="0.2.5",
            dependencies={},
            config_schema=self.settings_schema(),
            package_type="application",
        )

    async def initialize(self, config) -> None:  # noqa: ANN001
        return None

    async def shutdown(self) -> None:
        return None

    def bind_web_channel(self, channel, services) -> None:  # noqa: ANN001
        async def ping(ws, req_id, params, session_id):  # noqa: ANN001
            await channel.send_response(ws, req_id, ok=True, payload={"pong": True})

        channel.register_method("example.ping", ping, local_only=True)

    def settings_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "endpoint": {"type": "string", "default": "http://localhost"},
                "api_key": {"type": "string", "secret": True},
                "retries": {"type": "integer", "default": 2, "minimum": 0},
            },
        }

    def _state_path(self) -> Path:
        return self.state_path


def _registry() -> ExtensionRegistry:
    return ExtensionRegistry(MagicMock(), {}, MagicMock())


@pytest.mark.asyncio
async def test_video_duplex_is_discovered_as_application_plugin(monkeypatch) -> None:
    monkeypatch.delenv("VIDEO_DUPLEX_ENABLED", raising=False)
    registry = _registry()
    loaded = await ExtensionLoader(registry).load_extension(VIDEO_PLUGIN_ROOT)

    assert loaded
    plugins = registry.get_application_plugins()
    assert len(plugins) == 1
    assert plugins[0].metadata.id == "video-duplex"

    manifest = application_plugin_manifest(registry)
    assert manifest["api_version"] == 1
    assert manifest["plugins"][0]["nav_key"] == "app:video-duplex"
    assert manifest["plugins"][0]["render_mode"] == "bundled"
    assert manifest["plugins"][0]["enabled"] is True


@pytest.mark.asyncio
async def test_disabled_plugin_stays_discoverable_for_settings(monkeypatch) -> None:
    monkeypatch.setenv("VIDEO_DUPLEX_ENABLED", "false")
    registry = _registry()
    await ExtensionLoader(registry).load_extension(VIDEO_PLUGIN_ROOT)

    manifest = application_plugin_manifest(registry)

    assert manifest["plugins"][0]["plugin_id"] == "video-duplex"
    assert manifest["plugins"][0]["enabled"] is False


@pytest.mark.asyncio
async def test_video_duplex_bind_registers_local_gateway_methods() -> None:
    registry = _registry()
    await ExtensionLoader(registry).load_extension(VIDEO_PLUGIN_ROOT)
    channel = _FakeChannel()

    registry.bind_application_plugins(channel, agent_client=object())

    assert "video.realtime.config" in channel.methods
    assert "video.joyai.frame" in channel.methods
    assert "tts.stream.start" in channel.methods
    assert set(channel.methods) == channel.local_only
    assert channel.application_plugin_registry is registry


@pytest.mark.asyncio
async def test_generic_plugin_settings_and_enabled_state_are_persisted(tmp_path: Path) -> None:
    plugin = _ConfigurablePlugin(tmp_path / "example.json")

    assert plugin.is_enabled() is True
    assert plugin.get_settings()["retries"] == 2

    await plugin.update_settings({"endpoint": "https://example.test", "api_key": "secret"})
    await plugin.set_enabled(False)

    restored = _ConfigurablePlugin(tmp_path / "example.json")
    assert restored.is_enabled() is False
    assert restored.get_settings()["endpoint"] == "https://example.test"
    assert restored.get_settings()["api_key"] == "secret"

    public = application_plugin_settings_payload(restored)
    assert public["values"]["api_key"] == ""
    assert public["configured_secrets"] == ["api_key"]
    assert public["configured_secret_lengths"] == {"api_key": 6}


@pytest.mark.asyncio
async def test_generic_plugin_settings_reject_unknown_or_invalid_values(tmp_path: Path) -> None:
    plugin = _ConfigurablePlugin(tmp_path / "example.json")

    with pytest.raises(ValueError, match="unknown application plugin settings"):
        await plugin.update_settings({"unknown": True})
    with pytest.raises(ValueError, match="must be integer"):
        await plugin.update_settings({"retries": "many"})


@pytest.mark.asyncio
async def test_registry_automatically_blocks_disabled_plugin_rpc(tmp_path: Path) -> None:
    plugin = _ConfigurablePlugin(tmp_path / "example.json")
    await plugin.set_enabled(False)
    registry = _registry()
    registry.register_application_plugin(plugin)
    channel = _FakeChannel()
    registry.bind_application_plugins(channel)
    responses: list[dict[str, Any]] = []

    await channel.methods["example.ping"](responses, "req-1", {}, "session-1")

    assert responses[0]["ok"] is False
    assert responses[0]["code"] == "APPLICATION_PLUGIN_DISABLED"


def test_application_plugin_host_exposes_generic_management_routes(tmp_path: Path) -> None:
    plugin = _ConfigurablePlugin(tmp_path / "example.json")
    registry = _registry()
    registry.register_application_plugin(plugin)
    app = FastAPI()

    mount_application_plugin_http_routes(app, registry)

    manifest = application_plugin_manifest(registry)
    assert manifest["plugins"][0]["render_mode"] == "none"
    paths = {route.path for route in app.routes}
    assert "/api/application-plugins/{plugin_id}/settings" in paths
    assert "/api/application-plugins/{plugin_id}/enabled" in paths


@pytest.mark.asyncio
async def test_manifest_only_iframe_plugin_requires_no_python_entry(tmp_path: Path) -> None:
    root = tmp_path / "hello-plugin"
    assets = root / "frontend" / "dist"
    assets.mkdir(parents=True)
    (assets / "index.html").write_text("<h1>Hello</h1>", encoding="utf-8")
    (root / "extension.yaml").write_text(
        """
id: hello-plugin
name: Hello plugin
version: 1.0.0
description: Minimal external application
author: Example
min_jiuwenswarm_version: 0.2.5
package_type: application
permissions: [camera]
config_schema:
  type: object
  properties:
    greeting:
      type: string
      default: Hello
frontend:
  - id: hello-page
    title: Hello
    entrypoint: index.html
    position: 120
""".strip(),
        encoding="utf-8",
    )
    registry = _registry()

    loaded = await ExtensionLoader(registry).load_extension(root)

    assert loaded
    plugin = registry.get_application_plugin("hello-plugin")
    assert plugin is not None
    assert plugin.get_settings() == {"greeting": "Hello"}
    manifest = application_plugin_manifest(registry)["plugins"][0]
    assert manifest["render_mode"] == "iframe"
    assert manifest["permissions"] == ["camera"]
    assert manifest["entry_url"].endswith("/hello-plugin/assets/index.html")


def test_application_plugin_management_api_masks_and_preserves_secrets(tmp_path: Path) -> None:
    plugin = _ConfigurablePlugin(tmp_path / "example.json")
    registry = _registry()
    registry.register_application_plugin(plugin)
    app = FastAPI()
    mount_application_plugin_http_routes(app, registry)
    client = TestClient(app)

    saved = client.put(
        "/api/application-plugins/example-plugin/settings",
        json={
            "values": {
                "endpoint": "https://example.test",
                "api_key": "private-value",
                "retries": 4,
            }
        },
    )
    assert saved.status_code == 200
    assert saved.json()["values"]["api_key"] == ""
    assert saved.json()["configured_secrets"] == ["api_key"]
    assert saved.json()["configured_secret_lengths"] == {"api_key": 13}

    preserved = client.put(
        "/api/application-plugins/example-plugin/settings",
        json={
            "values": {
                "endpoint": "https://changed.test",
                "api_key": "",
                "retries": 5,
            }
        },
    )
    assert preserved.status_code == 200
    assert plugin.get_settings()["api_key"] == "private-value"
