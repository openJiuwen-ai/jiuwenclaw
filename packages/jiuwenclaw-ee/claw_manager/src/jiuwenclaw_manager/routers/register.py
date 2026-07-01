from __future__ import annotations

import os

from fastapi import APIRouter, FastAPI

from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.manager_ws_server import ManagerWsServer

from .application_config_routers import application_config_router
from .config_effective_policy_routers import config_effective_policy_router
from .iam_routers import bot_router, me_router
from .instance_routers import instance_router
from .template_routers import templates_router

api_router = APIRouter()

INSTANCES_PREFIX = "/instances"


def router_register(app: FastAPI) -> None:
    v1_router = APIRouter(prefix="/v1")
    # 登录/用户/组织 已迁至独立认证服务(jiuwenclaw_identity);此处只留 bot 与"我可见的 bot"。
    v1_router.include_router(bot_router, prefix="/bots", tags=["IAM Bots"])
    v1_router.include_router(me_router, prefix="/me", tags=["Me"])
    v1_router.include_router(
        instance_router,
        prefix=INSTANCES_PREFIX,
        tags=["Instances"],
    )
    v1_router.include_router(templates_router, tags=["Templates"])
    v1_router.include_router(
        application_config_router,
        prefix=INSTANCES_PREFIX,
        tags=["Application Config"],
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
    async def manager_ws_status() -> dict:
        server = ManagerWsServer.get_instance()
        if server is None:
            return {
                "enabled": settings.manager_ws_enabled,
                "running": False,
                "registered_jiuwenclaw_ids": [],
                "pid": os.getpid(),
            }
        registered = await server.list_registered_jiuwenclaw_ids()
        return {
            "enabled": True,
            "running": True,
            "host": server.host,
            "port": server.port,
            "registered_jiuwenclaw_ids": registered,
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
