from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .infrastructure.db import ensure_db_handler_ready, get_db_handler
from .routers.register import router_register


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_handler = await ensure_db_handler_ready()
        app.state.db_handler = db_handler
        yield
        await get_db_handler().disconnect()

    app = FastAPI(
        title="Agent Client REST API",
        version="0.1.0",
        description="REST API scaffold for agent client extension.",
        lifespan=lifespan,
    )

    router_register(app)
    return app
