"""路由注册：全部挂在 /v1 下。"""

from __future__ import annotations

from fastapi import APIRouter, FastAPI

from jiuwenclaw_identity.routers.auth_routers import auth_router
from jiuwenclaw_identity.routers.iam_routers import org_router, user_router


def router_register(app: FastAPI) -> None:
    v1 = APIRouter(prefix="/v1")
    v1.include_router(auth_router, prefix="/auth", tags=["Auth"])
    v1.include_router(org_router, prefix="/orgs", tags=["Directory · Orgs"])
    v1.include_router(user_router, prefix="/users", tags=["Directory · Users"])
    app.include_router(v1)
