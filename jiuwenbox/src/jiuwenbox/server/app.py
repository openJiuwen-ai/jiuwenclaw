# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""FastAPI application for box-server."""

from __future__ import annotations

import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from jiuwenbox.logging_config import configure_logging
from jiuwenbox import __version__
from jiuwenbox.server.sandbox_manager import (
    SandboxManager,
    SandboxNotFoundError,
    SandboxStateError,
)
from jiuwenbox.server.proxy_manager import ProxyManager
from jiuwenbox.server.policy_engine import PolicyValidationError

configure_logging()
logger = logging.getLogger(__name__)

_sandbox_manager: SandboxManager | None = None
_proxy_manager: ProxyManager | None = None

# Every sandbox API call that talks to the in-sandbox daemon (exec, write_file,
# read_file, list_dir) is dispatched via ``loop.run_in_executor(None, ...)``
# and therefore consumes one slot in the asyncio default ThreadPoolExecutor.
# Python's default size is ``min(32, os.cpu_count() + 4)`` which is *eight*
# threads on a 4-CPU box; running 100 concurrent sandboxes blows past that
# cap immediately, leaving 90+ requests sitting in the executor queue while
# the event loop sees the same coroutines as "still awaiting". That extra
# queueing time eventually trips upstream HTTP read timeouts and can be
# misread by clients as the server hanging up. Raise the pool to something
# proportional to the sandbox fan-out, with an env override for operators.
_IO_THREADS_ENV = "JIUWENBOX_IO_THREADS"
_DEFAULT_IO_THREADS_FLOOR = 64

# 100 sandboxes × (1 listener + 3 stdio + several transient client/daemon
# sockets) easily exceeds the typical Docker default of ``RLIMIT_NOFILE=1024``.
# Hitting it surfaces as a mix of ``EMFILE`` errors during ``accept``,
# ``connect``, or ``open`` - none of which fail loudly but all of which
# manifest to the test client as random "Server disconnected" responses.
# Raise the soft limit to the hard limit at startup.


def _resolve_io_thread_count() -> int:
    raw = os.environ.get(_IO_THREADS_ENV)
    if raw:
        try:
            value = int(raw)
        except ValueError:
            logger.warning(
                "Ignoring non-integer %s=%r; falling back to default",
                _IO_THREADS_ENV,
                raw,
            )
        else:
            if value >= 1:
                return value
            logger.warning(
                "Ignoring %s=%r (must be >= 1); falling back to default",
                _IO_THREADS_ENV,
                raw,
            )
    cpu_count = os.cpu_count() or 1
    return max(_DEFAULT_IO_THREADS_FLOOR, cpu_count * 16)


def _raise_open_file_limit() -> None:
    try:
        import resource
    except ImportError:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except (OSError, ValueError) as exc:
        logger.warning("Could not query RLIMIT_NOFILE: %s", exc)
        return
    if soft >= hard:
        logger.info("RLIMIT_NOFILE already at %s (hard=%s)", soft, hard)
        return
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (hard, hard))
    except (OSError, ValueError) as exc:
        logger.warning("Could not raise RLIMIT_NOFILE from %s to %s: %s", soft, hard, exc)
        return
    logger.info("Raised RLIMIT_NOFILE soft limit from %s to %s", soft, hard)


def _configure_loop_default_executor() -> None:
    workers = _resolve_io_thread_count()
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="jiuwenbox-io",
    )
    loop.set_default_executor(executor)
    logger.info(
        "asyncio default executor configured with %d threads (override via %s)",
        workers,
        _IO_THREADS_ENV,
    )


def get_sandbox_manager() -> SandboxManager:
    global _sandbox_manager
    if _sandbox_manager is None:
        _sandbox_manager = SandboxManager()
    return _sandbox_manager


def get_proxy_manager() -> ProxyManager:
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager()
    return _proxy_manager


@asynccontextmanager
async def lifespan(_application: FastAPI):
    global _sandbox_manager, _proxy_manager
    # Both of these have to run after uvicorn has spun up its event loop -
    # ``set_default_executor`` requires a running loop, and raising NOFILE is
    # only effective within the live process. They are also independent of
    # any other sandbox state, so doing them first means later startup work
    # already benefits from the larger executor.
    _raise_open_file_limit()
    _configure_loop_default_executor()
    _sandbox_manager = SandboxManager()
    _proxy_manager = ProxyManager()
    logger.info("box-server started (version %s)", __version__)
    await _proxy_manager.start()
    yield
    await _proxy_manager.stop()
    logger.info("box-server shutting down")


def create_app() -> FastAPI:
    application = FastAPI(
        title="jiuwenbox",
        description="Agent sandbox management API",
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

    @application.exception_handler(SandboxNotFoundError)
    async def not_found_handler(request: Request, exc: SandboxNotFoundError):
        return JSONResponse(status_code=404, content={"error": str(exc)})

    @application.exception_handler(SandboxStateError)
    async def state_error_handler(request: Request, exc: SandboxStateError):
        return JSONResponse(status_code=409, content={"error": str(exc)})

    @application.exception_handler(PolicyValidationError)
    async def policy_validation_error_handler(request: Request, exc: PolicyValidationError):
        return JSONResponse(status_code=400, content={"error": str(exc)})

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        # Without this catch-all, an unexpected error inside a route handler
        # (e.g. ``OSError`` with EMFILE/ENOMEM under heavy fan-out, or any
        # other unanticipated exception) escapes uvicorn's ASGI cycle and
        # the connection is dropped without a response. Clients then see
        # ``RemoteProtocolError: Server disconnected without sending a
        # response`` and have no way to distinguish a real crash from a
        # transient overload. Returning a structured 500 lets the test
        # harness's retry logic kick in and surfaces a debuggable trace
        # in the server log.
        logger.exception(
            "Unhandled exception in %s %s: %s",
            request.method,
            request.url.path,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_server_error",
                "detail": str(exc) or exc.__class__.__name__,
            },
        )

    from jiuwenbox.server.routes.sandbox import router as sandbox_router
    from jiuwenbox.server.routes.policy import router as policy_router
    from jiuwenbox.server.routes.proxy import router as proxy_router

    application.include_router(sandbox_router, prefix="/api/v1")
    application.include_router(policy_router, prefix="/api/v1")
    application.include_router(proxy_router, prefix="/api/v1")

    @application.get("/health")
    async def health():
        from jiuwenbox.models.common import HealthResponse
        from jiuwenbox.supervisor.landlock import detect_landlock_abi

        mgr = get_sandbox_manager()
        sandboxes = await mgr.list_sandboxes()
        active = sum(1 for s in sandboxes if s.phase.value == "ready")

        return HealthResponse(
            version=__version__,
            landlock_supported=detect_landlock_abi() > 0,
            sandboxes_active=active,
        )

    return application


app = create_app()
get_manager = get_sandbox_manager
