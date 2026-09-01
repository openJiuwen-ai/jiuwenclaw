from __future__ import annotations

from typing import Any

from jiuwenswarm.extensions.sdk import (
    ApplicationPluginExtension,
    ApplicationPluginServices,
    FrontendContribution,
    WebSocketRouteContribution,
)
from jiuwenswarm.extensions.video_duplex.backend.qwen_omni_gateway import (
    QWEN_OMNI_PROXY_PATH,
    serve_qwen_omni_websocket,
)
from jiuwenswarm.extensions.video_duplex.backend.video_live import (
    register_video_live_handler,
    video_duplex_enabled,
)


class VideoDuplexApplicationPlugin(ApplicationPluginExtension):
    plugin_id = "video-duplex"

    def is_enabled(self) -> bool:
        return video_duplex_enabled()

    async def initialize(self, config: Any) -> None:
        del config

    async def shutdown(self) -> None:
        return None

    def bind_web_channel(
        self,
        channel: Any,
        services: ApplicationPluginServices,
    ) -> None:
        register_video_live_handler(
            channel,
            agent_client=services.require_agent_client(),
            normalize_media_attachments=services.normalize_media_attachments,
        )

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
                render_mode="bundled",
                component="video-duplex",
                position=75,
            ),
        )


async def register_extensions(registry: Any) -> list[VideoDuplexApplicationPlugin]:
    extension = VideoDuplexApplicationPlugin()
    registry.register_application_plugin(extension)
    return [extension]
