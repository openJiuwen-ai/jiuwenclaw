from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_database
from .models.table_init import init_all_tables
from .routers.register import router_register


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db_handler = init_database()
        app.state.db_handler = db_handler
        await db_handler.connect()
        await init_all_tables(db_handler)
        yield
        await db_handler.disconnect()

    app = FastAPI(
        title="Agent Client REST API",
        version="0.1.0",
        description="REST API scaffold for agent client extension.",
        lifespan=lifespan,
    )

    router_register(app)
    return app
