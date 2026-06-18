"""Serve Manager Web via FastAPI (static dist + /api reverse proxy)."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding", "connection"})


def _manager_web_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def _coerce_backend_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"backend url must be http/https: {raw}")
    return url


def create_manager_web_app(dist_root: Path, backend_url: str) -> FastAPI:
    application = FastAPI(title="jiuwenclaw-manager-web", docs_url=None, redoc_url=None)

    @application.api_route(
        "/api/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_manager_api(request: Request, tail: str) -> Response:
        upstream_url = f"{backend_url}/api/{tail}"
        if request.url.query:
            upstream_url = f"{upstream_url}?{request.url.query}"

        outbound_headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() not in _SKIP_REQ_HEADERS
        }
        payload = await request.body()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                upstream = await client.request(
                    request.method,
                    upstream_url,
                    content=payload,
                    headers=outbound_headers,
                )
        except httpx.HTTPError as exc:
            logging.getLogger("jiuwenclaw-manager-web").error("api relay failed: %s", exc)
            return Response(content=b"api relay failed", status_code=502)

        response_headers = {
            name: value
            for name, value in upstream.headers.items()
            if name.lower() not in _SKIP_RESP_HEADERS
        }
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=response_headers,
        )

    @application.middleware("http")
    async def static_cache_control(request: Request, call_next) -> Response:
        """HTML 可缓存但须校验（未发版刷新走 304）；/assets/ 带 hash 长期缓存。"""
        response = await call_next(request)
        content_type = (response.headers.get("content-type") or "").lower()
        if "text/html" in content_type:
            response.headers["Cache-Control"] = "no-cache, must-revalidate"
        elif request.url.path.startswith("/assets/"):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=31536000, immutable",
            )
        return response

    application.mount(
        "/",
        StaticFiles(directory=str(dist_root), html=True),
        name="manager-web-static",
    )
    return application


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve JiuwenClaw Manager Web static files.")
    parser.add_argument("--host", default=os.getenv("MANAGER_WEB_HOST", "localhost"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("MANAGER_WEB_PORT", "5273")),
    )
    parser.add_argument("--dist", default=str(_manager_web_dist()))
    parser.add_argument(
        "--proxy-target",
        default=os.getenv("MANAGER_WEB_PROXY_TARGET", "http://127.0.0.1:8765"),
        help="Claw Manager REST base URL for /api relay.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("MANAGER_WEB_LOG_LEVEL", "info"),
    )
    args = parser.parse_args()

    dist_root = Path(args.dist).expanduser().resolve()
    if not dist_root.is_dir():
        raise SystemExit(f"dist directory not found: {dist_root}")

    try:
        backend_url = _coerce_backend_url(args.proxy_target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    log = logging.getLogger("jiuwenclaw-manager-web")
    log.info("serving %s", dist_root)
    log.info("http://%s:%s", args.host, args.port)
    log.info("/api relay -> %s", backend_url)

    app = create_manager_web_app(dist_root, backend_url)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
