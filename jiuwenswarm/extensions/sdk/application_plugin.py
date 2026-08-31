from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import threading
from typing import Any, Awaitable, Callable, Mapping

from jiuwenswarm.common.utils import get_config_dir
from jiuwenswarm.extensions.sdk.base import BaseExtension


WebSocketEndpoint = Callable[[Any], Awaitable[None]]
_PLUGIN_STATE_LOCK = threading.RLock()
_SAFE_PLUGIN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


@dataclass(frozen=True)
class ApplicationPluginServices:
    """Core services made available while an application plugin binds to Gateway."""

    agent_client: Any = None


@dataclass(frozen=True)
class WebSocketRouteContribution:
    path: str
    endpoint: WebSocketEndpoint
    check_origin: bool = True


@dataclass(frozen=True)
class FrontendContribution:
    """A page contribution exposed to the web frontend.

    ``bundled`` entries are compiled with Jiuwen and resolved by ``component``.
    ``iframe`` entries are prebuilt assets shipped by an installable plugin.
    """

    id: str
    nav_key: str
    title: str
    title_i18n_key: str = ""
    render_mode: str = "iframe"
    component: str = ""
    entrypoint: str = ""
    position: int = 100


class ApplicationPluginExtension(BaseExtension):
    """Extension contract for a full-stack Jiuwen application plugin."""

    plugin_id: str = ""

    def is_enabled(self) -> bool:
        """Return whether the plugin should accept runtime work.

        Plugins remain discoverable while disabled so their settings page can
        be used to enable them again.
        """

        return bool(self._read_host_state().get("enabled", True))

    async def set_enabled(self, enabled: bool) -> None:
        """Persist the plugin's runtime switch.

        Override this when an existing plugin must keep compatibility with a
        legacy configuration source such as environment variables.
        """

        state = self._read_host_state()
        state["enabled"] = bool(enabled)
        self._write_host_state(state)

    def settings_schema(self) -> dict[str, Any]:
        schema = self.metadata.config_schema
        return dict(schema) if isinstance(schema, dict) else {
            "type": "object",
            "properties": {},
        }

    def get_settings(self) -> dict[str, Any]:
        schema = self.settings_schema()
        state = self._read_host_state()
        stored = state.get("settings")
        values = self._settings_defaults(schema)
        if isinstance(stored, dict):
            values.update(stored)
        return values

    async def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        merged = self.get_settings()
        merged.update(dict(values))
        validated = validate_application_plugin_settings(self.settings_schema(), merged)
        state = self._read_host_state()
        state["settings"] = validated
        self._write_host_state(state)
        return validated

    def _settings_defaults(self, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
        target = schema if isinstance(schema, Mapping) else self.settings_schema()
        properties = target.get("properties")
        if not isinstance(properties, Mapping):
            return {}
        return {
            str(key): definition["default"]
            for key, definition in properties.items()
            if isinstance(definition, Mapping) and "default" in definition
        }

    def _state_path(self) -> Path:
        plugin_id = str(self.plugin_id or self.metadata.id or "").strip()
        if not _SAFE_PLUGIN_ID.fullmatch(plugin_id):
            raise ValueError(f"invalid application plugin id: {plugin_id!r}")
        return get_config_dir() / "application_plugins" / f"{plugin_id}.json"

    def _read_host_state(self) -> dict[str, Any]:
        path = self._state_path()
        with _PLUGIN_STATE_LOCK:
            if not path.is_file():
                return {"schema_version": 1, "enabled": True, "settings": {}}
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                return {"schema_version": 1, "enabled": True, "settings": {}}
        return value if isinstance(value, dict) else {
            "schema_version": 1,
            "enabled": True,
            "settings": {},
        }

    def _write_host_state(self, state: Mapping[str, Any]) -> None:
        path = self._state_path()
        payload = {
            "schema_version": 1,
            "enabled": bool(state.get("enabled", True)),
            "settings": dict(state.get("settings") or {}),
        }
        with _PLUGIN_STATE_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f"{path.suffix}.tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)

    def bind_web_channel(
        self,
        channel: Any,
        services: ApplicationPluginServices,
    ) -> None:
        """Register local RPC methods and connection hooks on ``channel``.

        Frontend-only plugins do not need to override this method.
        """

        del channel, services

    def websocket_routes(self) -> tuple[WebSocketRouteContribution, ...]:
        return ()

    def frontend_contributions(self) -> tuple[FrontendContribution, ...]:
        return ()

    def frontend_asset_root(self) -> Path | None:
        root = self._get_extension_dir()
        if root is None:
            return None
        candidate = root / "frontend" / "dist"
        return candidate if candidate.is_dir() else None


class ManifestApplicationPlugin(ApplicationPluginExtension):
    """Application plugin described entirely by ``extension.yaml``.

    This is intended for prebuilt iframe applications that do not need a
    Python backend. The loader creates and registers it automatically.
    """

    def __init__(self, root: Path) -> None:
        self.set_extension_dir(root)
        self.plugin_id = self.metadata.id

    async def initialize(self, config: Any) -> None:
        del config

    async def shutdown(self) -> None:
        return None

    def frontend_contributions(self) -> tuple[FrontendContribution, ...]:
        contributions: list[FrontendContribution] = []
        for index, raw in enumerate(self.metadata.frontend):
            render_mode = str(raw.get("render_mode", "iframe")).strip()
            if render_mode != "iframe":
                raise ValueError(
                    "manifest-only application plugins support iframe frontends only"
                )
            entrypoint = str(raw.get("entrypoint", "index.html")).strip()
            if not entrypoint:
                raise ValueError("application plugin frontend entrypoint must not be empty")
            contribution_id = str(raw.get("id") or f"{self.plugin_id}-page").strip()
            nav_key = str(raw.get("nav_key") or f"app:{self.plugin_id}").strip()
            title = str(raw.get("title") or self.metadata.name or self.plugin_id).strip()
            try:
                position = int(raw.get("position", 100 + index))
            except (TypeError, ValueError) as exc:
                raise ValueError("application plugin frontend position must be an integer") from exc
            contributions.append(
                FrontendContribution(
                    id=contribution_id,
                    nav_key=nav_key,
                    title=title,
                    title_i18n_key=str(raw.get("title_i18n_key", "")).strip(),
                    render_mode=render_mode,
                    entrypoint=entrypoint,
                    position=position,
                )
            )
        return tuple(contributions)


def application_plugin_secret_fields(schema: Mapping[str, Any]) -> set[str]:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        return set()
    return {
        str(key)
        for key, definition in properties.items()
        if isinstance(definition, Mapping)
        and (definition.get("secret") is True or definition.get("format") == "password")
    }


def validate_application_plugin_settings(
    schema: Mapping[str, Any],
    values: Mapping[str, Any],
) -> dict[str, Any]:
    if schema.get("type", "object") != "object":
        raise ValueError("application plugin config_schema must describe an object")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping):
        properties = {}
    unknown = set(values) - {str(key) for key in properties}
    if unknown:
        raise ValueError("unknown application plugin settings: " + ", ".join(sorted(unknown)))

    result: dict[str, Any] = {}
    for key, value in values.items():
        definition = properties.get(key)
        if not isinstance(definition, Mapping):
            continue
        expected = definition.get("type", "string")
        valid_type = (
            (expected == "string" and isinstance(value, str))
            or (expected == "boolean" and isinstance(value, bool))
            or (expected == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (expected == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (expected == "array" and isinstance(value, list))
            or (expected == "object" and isinstance(value, dict))
            or expected is None
        )
        if not valid_type:
            raise ValueError(f"setting {key!r} must be {expected}")
        enum = definition.get("enum")
        if isinstance(enum, list) and value not in enum:
            raise ValueError(f"setting {key!r} must be one of {enum!r}")
        if isinstance(value, str):
            minimum_length = definition.get("minLength")
            maximum_length = definition.get("maxLength")
            if isinstance(minimum_length, int) and len(value) < minimum_length:
                raise ValueError(f"setting {key!r} is shorter than {minimum_length}")
            if isinstance(maximum_length, int) and len(value) > maximum_length:
                raise ValueError(f"setting {key!r} is longer than {maximum_length}")
            pattern = definition.get("pattern")
            if isinstance(pattern, str) and re.search(pattern, value) is None:
                raise ValueError(f"setting {key!r} does not match its required pattern")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            minimum = definition.get("minimum")
            maximum = definition.get("maximum")
            if isinstance(minimum, (int, float)) and value < minimum:
                raise ValueError(f"setting {key!r} must be at least {minimum}")
            if isinstance(maximum, (int, float)) and value > maximum:
                raise ValueError(f"setting {key!r} must be at most {maximum}")
        result[str(key)] = value

    required = schema.get("required")
    if isinstance(required, list):
        missing = [str(key) for key in required if key not in result]
        if missing:
            raise ValueError("missing required application plugin settings: " + ", ".join(missing))
    return result
