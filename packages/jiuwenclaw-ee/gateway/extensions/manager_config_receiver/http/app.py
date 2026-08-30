# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from ..routers.application_config_routers import application_config_router
from ..routers.instance_resource_routers import instance_resource_router
from ..routers.instance_routers import instance_router
from ..routers.register_router import register_router
from ..routers.template_routers import templates_router


def create_app() -> FastAPI:
    """Gateway 本机接口：无路径级实例段；``JIUWENCLAW_ID`` 仅作 Manager 注册/探活身份（未设则自动生成）。"""
    app = FastAPI(title="Gateway Manager Config Receiver", docs_url="/docs", redoc_url=None)

    @app.get("/api/health", tags=["System"])
    async def system_health() -> dict[str, str]:
        return {"status": "ok"}

    v1 = APIRouter(prefix="/api/v1")

    @v1.get("/ready", tags=["System"])
    async def ready() -> dict[str, str]:
        """K8s readiness。"""
        return {"status": "ready"}

    v1.include_router(templates_router, tags=["Templates"])
    v1.include_router(register_router, tags=["Instances"])
    v1.include_router(instance_router, tags=["Instances"])
    v1.include_router(instance_resource_router, tags=["Instance Resources"])
    v1.include_router(application_config_router, tags=["Application Config"])

    app.include_router(v1)
    return app
