from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from jiuwenswarm.common.utils import get_env_file
from jiuwenswarm.extensions.sdk import (
    ApplicationPluginExtension,
    ApplicationPluginServices,
    FrontendContribution,
    WebSocketRouteContribution,
    validate_application_plugin_settings,
)
from jiuwenswarm.extensions.video_duplex.backend.qwen_omni_gateway import (
    QWEN_OMNI_PROXY_PATH,
    serve_qwen_omni_websocket,
)
from jiuwenswarm.extensions.video_duplex.backend.video_live import (
    register_video_live_handler,
    video_duplex_enabled,
)


_SETTINGS_ENV_MAP = {
    "joyai_api_base": "JOYAI_API_BASE",
    "joyai_api_key": "JOYAI_API_KEY",
    "joyai_model": "JOYAI_MODEL_NAME",
    "qwen_omni_realtime_url": "QWEN_OMNI_REALTIME_URL",
    "qwen_omni_api_key": "QWEN_OMNI_API_KEY",
    "qwen_omni_model": "QWEN_OMNI_MODEL_NAME",
    "qwen_omni_voice": "QWEN_OMNI_VOICE",
    "voice_protocol": "VOICE_PROTOCOL",
    "voice_asr_endpoint": "VOICE_ASR_ENDPOINT",
    "voice_tts_endpoint": "VOICE_TTS_ENDPOINT",
    "voice_api_key": "VOICE_API_KEY",
    "voice_asr_model": "VOICE_ASR_MODEL",
    "voice_tts_model": "VOICE_TTS_MODEL",
    "voice_tts_voice": "VOICE_TTS_VOICE",
}


def _persist_env_updates(updates: Mapping[str, str]) -> None:
    if not updates:
        return
    path = get_env_file()
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True) if path.is_file() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = next((candidate for candidate in remaining if stripped.startswith(candidate + "=")), None)
        if key is None:
            output.append(line)
            continue
        value = remaining.pop(key)
        output.append(f"{key}={json.dumps(value, ensure_ascii=False)}\n" if value else f"{key}=\n")
    for key, value in remaining.items():
        output.append(f"{key}={json.dumps(value, ensure_ascii=False)}\n" if value else f"{key}=\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    temporary.write_text("".join(output), encoding="utf-8")
    os.replace(temporary, path)


class VideoDuplexApplicationPlugin(ApplicationPluginExtension):
    plugin_id = "video-duplex"

    def is_enabled(self) -> bool:
        return video_duplex_enabled()

    async def set_enabled(self, enabled: bool) -> None:
        value = "true" if enabled else "false"
        os.environ["VIDEO_DUPLEX_ENABLED"] = value
        _persist_env_updates({"VIDEO_DUPLEX_ENABLED": value})

    def get_settings(self) -> dict[str, Any]:
        settings = self._settings_defaults()
        mode = (os.getenv("VIDEO_LIVE_MODE") or "joyai").strip().casefold()
        realtime_provider = (os.getenv("VIDEO_REALTIME_PROVIDER") or "").strip().casefold()
        settings["video_live_provider"] = (
            "qwen_omni" if mode == "realtime" and realtime_provider == "qwen_omni" else "joyai"
        )
        for key, env_key in _SETTINGS_ENV_MAP.items():
            raw = os.getenv(env_key)
            if raw is not None and raw.strip():
                settings[key] = raw.strip()
        return settings

    async def update_settings(self, values: Mapping[str, Any]) -> dict[str, Any]:
        merged = self.get_settings()
        merged.update(dict(values))
        validated = validate_application_plugin_settings(self.settings_schema(), merged)
        provider = str(validated.get("video_live_provider") or "joyai")
        updates: dict[str, str] = {}
        if provider == "qwen_omni":
            updates.update({"VIDEO_LIVE_MODE": "realtime", "VIDEO_REALTIME_PROVIDER": "qwen_omni"})
        else:
            updates.update({"VIDEO_LIVE_MODE": "joyai", "VIDEO_REALTIME_PROVIDER": ""})
        for key, env_key in _SETTINGS_ENV_MAP.items():
            if key in validated:
                updates[env_key] = str(validated[key]).strip()
        os.environ.update(updates)
        _persist_env_updates(updates)
        return validated

    async def initialize(self, config: Any) -> None:
        del config

    async def shutdown(self) -> None:
        return None

    def bind_web_channel(
        self,
        channel: Any,
        services: ApplicationPluginServices,
    ) -> None:
        register_video_live_handler(channel, agent_client=services.agent_client)

    def websocket_routes(self) -> tuple[WebSocketRouteContribution, ...]:
        return (
            WebSocketRouteContribution(
                path=QWEN_OMNI_PROXY_PATH,
                endpoint=serve_qwen_omni_websocket,
            ),
        )

    def frontend_contributions(self) -> tuple[FrontendContribution, ...]:
        return (
            FrontendContribution(
                id="video-live",
                nav_key="app:video-duplex",
                title="Full-duplex",
                title_i18n_key="nav.videoLive",
                render_mode="bundled",
                component="video-duplex",
                position=75,
            ),
        )


async def register_extensions(registry: Any) -> list[VideoDuplexApplicationPlugin]:
    extension = VideoDuplexApplicationPlugin()
    registry.register_application_plugin(extension)
    return [extension]
