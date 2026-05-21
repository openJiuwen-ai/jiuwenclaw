"""FastAPI 应用工厂。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jiuwenclaw_manager import __version__
from jiuwenclaw_manager.infrastructure.config import settings
from jiuwenclaw_manager.infrastructure.gateway_forward import GatewayHttpClient
from jiuwenclaw_manager.infrastructure.db import create_db_handler, database_config_summary
from jiuwenclaw_manager.infrastructure.logger import configure_logging, get_logger
from jiuwenclaw_manager.models.table_init import init_all_tables
from jiuwenclaw_manager.routers.register import router_register
from jiuwenclaw_manager.schedulers.heartbeat_scanner import run_heartbeat_scan_loop

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):
    configure_logging()
    db_handler = create_db_handler()
    application.state.db_handler = db_handler
    await db_handler.init_database()
    await db_handler.connect()
    await init_all_tables(db_handler)
    stop = asyncio.Event()
    scan_task = asyncio.create_task(run_heartbeat_scan_loop(stop, db_handler))
    consumer_task: asyncio.Task[None] | None = None
    if settings.rabbitmq_url:
        from jiuwenclaw_manager.consumers.event_consumer import start_consumer

        consumer_task = asyncio.create_task(start_consumer(db_handler))
    GatewayHttpClient.init(timeout=httpx.Timeout(settings.upstream_http_timeout_seconds))
    manager_ws_server = None
    if settings.manager_ws_enabled:
        from jiuwenclaw_manager.manager_ws_server import ManagerWsServer

        manager_ws_server = ManagerWsServer(
            host=settings.manager_ws_host,
            port=settings.manager_ws_port,
            manager_id=settings.manager_id,
        )
        await manager_ws_server.start()
        application.state.manager_ws_server = manager_ws_server
        from jiuwenclaw_manager.manager_ws_server.server import set_manager_ws_server

        set_manager_ws_server(manager_ws_server)
    _log.info(
        "startup",
        version=__version__,
        db=database_config_summary(),
        rabbitmq=bool(settings.rabbitmq_url),
        rabbitmq_url=settings.rabbitmq_url,
        manager_ws=(
            f"ws://{settings.manager_ws_host}:{settings.manager_ws_port}"
            if settings.manager_ws_enabled
            else None
        ),
    )
    yield
    stop.set()
    scan_task.cancel()
    try:
        await scan_task
    except asyncio.CancelledError:
        pass
    if consumer_task is not None:
        consumer_task.cancel()
        try:
            await consumer_task
        except asyncio.CancelledError:
            pass
    if manager_ws_server is not None:
        from jiuwenclaw_manager.manager_ws_server.server import set_manager_ws_server

        set_manager_ws_server(None)
        await manager_ws_server.stop()
    await GatewayHttpClient.close()
    await db_handler.disconnect()
    _log.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="jiuwenclaw-manager",
        description="JiuwenClaw EE 管理平面（Claw Manager）",
        version=__version__,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    router_register(application)
    return application


app = create_app()
