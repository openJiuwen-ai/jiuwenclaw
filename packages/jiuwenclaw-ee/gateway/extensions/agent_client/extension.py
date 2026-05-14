from __future__ import annotations

import threading
from typing import Any

from jiuwenclaw.config import get_config
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.extensions.sdk.base import BaseExtension
from jiuwenclaw.extensions.types import ExtensionConfig

from .agent_client_rest.app import create_app
from .claw_manager_reporting import register_claw_manager_dmq_hooks, shutdown_reporting_task


class AgentClientRestExtension(BaseExtension):
    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._server: Any | None = None
        self._server_thread: threading.Thread | None = None

    async def initialize(self, config: ExtensionConfig) -> None:
        if self._server is not None:
            return

        try:
            import uvicorn
        except ImportError as exc:
            raise RuntimeError(
                "FastAPI extension requires uvicorn. Please install extension dependencies first."
            ) from exc

        app = create_app()
        uvicorn_config = uvicorn.Config(
            app=app,
            host=self._host,
            port=self._port,
            log_level="info",
        )
        self._server = uvicorn.Server(uvicorn_config)
        self._server_thread = threading.Thread(target=self._server.run, daemon=True)
        self._server_thread.start()

    async def shutdown(self) -> None:
        if self._server is not None:
            self._server.should_exit = True

        if self._server_thread is not None and self._server_thread.is_alive():
            self._server_thread.join(timeout=3.0)

        self._server = None
        self._server_thread = None


class ClawManagerDmqLifecycleExtension:
    """仅用于进程退出时 cancel DMQ 心跳任务并发布 offline（无独立 extension.yaml）。"""

    async def shutdown(self) -> None:
        await shutdown_reporting_task()


def _resolve_rest_config() -> tuple[bool, str, int]:
    cfg = get_config()
    ext_cfg = cfg.get("extensions") if isinstance(cfg, dict) else {}
    rest_cfg = ext_cfg.get("agent_client_rest") if isinstance(ext_cfg, dict) else {}
    if not isinstance(rest_cfg, dict):
        rest_cfg = {}

    raw_en = rest_cfg.get("enabled", True)
    if isinstance(raw_en, bool):
        enabled = raw_en
    elif isinstance(raw_en, str):
        enabled = raw_en.strip().lower() in ("true", "1", "yes", "on")
    else:
        enabled = bool(raw_en)
    host = str(rest_cfg.get("host") or "127.0.0.1").strip()
    port = int(rest_cfg.get("port") or 18080)
    return enabled, host, port


async def register_extensions(registry: ExtensionRegistry) -> list[Any]:
    register_claw_manager_dmq_hooks(registry)
    loaded: list[Any] = [ClawManagerDmqLifecycleExtension()]
    enabled, host, port = _resolve_rest_config()
    if enabled:
        rest = AgentClientRestExtension(host=host, port=port)
        await rest.initialize(registry.config)
        loaded.insert(0, rest)
    return loaded
