from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI, Request

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.manager_ws_server import ManagerWsServer

from .config_effective_policy_routers import config_effective_policy_router
from .instance_routers import instance_router
from .template_routers import templates_router

api_router = APIRouter()

INSTANCES_PREFIX = "/instances"


def router_register(app: FastAPI) -> None:
    v1_router = APIRouter(prefix="/v1")
    v1_router.include_router(
        instance_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instances"],
    )
    v1_router.include_router(
        templates_router,
        prefix=INSTANCES_PREFIX,
        tags=["Model Templates"],
    )
    v1_router.include_router(
        config_effective_policy_router,
        prefix=INSTANCES_PREFIX,
        tags=["Config Effective Policy"],
    )

    @api_router.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    @api_router.get("/manager-ws/status", tags=["System"])
    async def manager_ws_status(request: Request) -> dict:
        server: ManagerWsServer | None = getattr(
            request.app.state, "manager_ws_server", None
        )
        if server is None:
            return {
                "enabled": settings.manager_ws_enabled,
                "running": False,
                "registered_instances": [],
                "pid": os.getpid(),
            }
        registered = await server.list_registered_instance_ids()
        return {
            "enabled": True,
            "running": True,
            "host": server.host,
            "port": server.port,
            "registered_instances": registered,
            "pid": os.getpid(),
        }

    api_router.include_router(v1_router)
    app.include_router(api_router, prefix="/api")

    @app.get("/", tags=["System"])
    async def root() -> dict[str, str]:
        return {
            "message": "JiuwenClaw Manager API",
            "docs": "/docs",
            "health": "/api/health",
        }
