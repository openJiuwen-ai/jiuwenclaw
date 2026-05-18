from __future__ import annotations

from fastapi import APIRouter, FastAPI

from .config_effective_policy import router as config_effective_policy_router
from .instances import router as instances_router
from .template import router as model_templates_router

api_router = APIRouter()


def router_register(app: FastAPI) -> None:
    v1_router = APIRouter(prefix="/v1")
    v1_router.include_router(instances_router, tags=["Instances"])
    v1_router.include_router(model_templates_router, tags=["Model Templates"])
    v1_router.include_router(
        config_effective_policy_router,
        tags=["Config Effective Policy"],
    )

    @api_router.get("/health", tags=["System"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    api_router.include_router(v1_router)
    app.include_router(api_router, prefix="/api")

    @app.get("/", tags=["System"])
    async def root() -> dict[str, str]:
        return {
            "message": "JiuwenClaw Manager API",
            "docs": "/docs",
            "health": "/api/health",
        }
