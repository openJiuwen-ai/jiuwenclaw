# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from fastapi import APIRouter, FastAPI, HTTPException, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from ..infrastructure.ha import gateway_deployment_mode, is_gateway_primary
from ..routers.application_config_routers import application_config_router
from ..routers.config_effective_policy_routers import config_effective_policy_routers
from ..routers.instance_resource_routers import instance_resource_router
from ..routers.instance_routers import instance_router
from ..routers.register_router import register_router
from ..routers.template_routers import templates_router

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# 注册/探活始终放行（STANDBY 也需可被 Manager 探测本地状态）
_WRITE_ALLOW_PATH_SUFFIXES = (
    "/api/v1/register",
    "/api/v1/register-payload",
    "/api/v1/health",
    "/api/v1/ready",
    "/api/health",
    "/docs",
    "/openapi.json",
)


class PrimaryWriteGateMiddleware(BaseHTTPMiddleware):
    """active-standby 下 STANDBY 拒绝写请求（503），读与注册相关路径放行。"""

    async def dispatch(self, request: Request, call_next):
        if request.method.upper() in _WRITE_METHODS:
            path = request.url.path
            if not any(path.endswith(s) or path == s for s in _WRITE_ALLOW_PATH_SUFFIXES):
                if gateway_deployment_mode() == "active-standby" and not is_gateway_primary():
                    return Response(
                        content='{"detail":"gateway standby; write only on PRIMARY"}',
                        status_code=503,
                        media_type="application/json",
                    )
        return await call_next(request)


def create_app() -> FastAPI:
    """Gateway 本机接口：无 ``/instances/{jiuwenclaw_id}``；实例 id 取自 ``JIUWENCLAW_ID``。"""
    app = FastAPI(title="Gateway Manager Config Receiver", docs_url="/docs", redoc_url=None)
    app.add_middleware(PrimaryWriteGateMiddleware)

    @app.get("/api/health", tags=["System"])
    async def system_health() -> dict[str, str]:
        return {"status": "ok"}

    v1 = APIRouter(prefix="/api/v1")

    @v1.get("/ready", tags=["System"])
    async def ready() -> dict[str, str]:
        """K8s readiness：active-standby 仅 PRIMARY 返回 200。"""
        mode = gateway_deployment_mode()
        if mode == "active-standby" and not is_gateway_primary():
            raise HTTPException(status_code=503, detail="standby")
        return {"status": "ready", "role": "PRIMARY" if is_gateway_primary() else "ACTIVE"}

    v1.include_router(templates_router, tags=["Templates"])
    v1.include_router(register_router, tags=["Instances"])
    v1.include_router(instance_router, tags=["Instances"])
    v1.include_router(instance_resource_router, tags=["Instance Resources"])
    v1.include_router(application_config_router, tags=["Application Config"])
    for policy_router in config_effective_policy_routers:
        v1.include_router(policy_router, tags=["Config Effective Policy"])

    app.include_router(v1)
    return app
