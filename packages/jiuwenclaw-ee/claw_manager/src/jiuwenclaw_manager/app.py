"""FastAPI 应用工厂。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from jiuwenclaw_manager import __version__
from jiuwenclaw_manager.api.instances import router as instances_router
from jiuwenclaw_manager.api.runtime_config import router as runtime_config_router
from jiuwenclaw_manager.config import settings
from jiuwenclaw_manager.infrastructure.db import create_all_tables, dispose_engine, init_engine
from jiuwenclaw_manager.infrastructure.logger import configure_logging, get_logger
from jiuwenclaw_manager.schedulers.heartbeat_scanner import run_heartbeat_scan_loop

_log = get_logger(__name__)


@asynccontextmanager
async def lifespan(application: FastAPI):  # 改名 app -> application
    configure_logging()
    init_engine(settings.database_url)
    await create_all_tables()
    stop = asyncio.Event()
    scan_task = asyncio.create_task(run_heartbeat_scan_loop(stop))
    consumer_task: asyncio.Task[None] | None = None
    if settings.rabbitmq_url:
        from jiuwenclaw_manager.consumers.event_consumer import start_consumer

        consumer_task = asyncio.create_task(start_consumer())
    timeout = httpx.Timeout(settings.upstream_http_timeout_seconds)
    application.state.http_client = httpx.AsyncClient(timeout=timeout)  # 使用新的参数名
    _log.info(
        "startup",
        version=__version__,
        db=settings.database_url.split("://")[0],
        rabbitmq=bool(settings.rabbitmq_url),
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
    await application.state.http_client.aclose()  # 使用新的参数名
    await dispose_engine()
    _log.info("shutdown")


def create_app() -> FastAPI:
    application = FastAPI(
        title="jiuwenclaw-manager",
        description="JiuwenClaw EE 管理平面（Claw Manager）",
        version=__version__,
        lifespan=lifespan,
    )
    # ... 其余代码保持不变 ...
    return application


app = create_app()  # 这里的全局变量 app 不再冲突