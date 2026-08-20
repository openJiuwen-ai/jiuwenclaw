"""Serve Observability Web via FastAPI (static dist + /observability reverse proxy to Prometheus)."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from jiuwenclaw_observability import db as audit_db

_SKIP_REQ_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection"})
_SKIP_RESP_HEADERS = frozenset({"content-encoding", "content-length", "transfer-encoding", "connection"})


class AuditRuleCreate(BaseModel):
    detector: str
    rule_name: str
    category: str = ""
    pattern: str
    severity: str = "medium"
    action: str = "log"
    enabled: bool = True
    description: str = ""


class AuditRuleUpdate(BaseModel):
    detector: str | None = None
    rule_name: str | None = None
    category: str | None = None
    pattern: str | None = None
    severity: str | None = None
    action: str | None = None
    enabled: bool | None = None
    description: str | None = None


def _observability_dist() -> Path:
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def _coerce_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"url must be http/https: {raw}")
    return url


def create_observability_app(
    dist_root: Path,
    prometheus_url: str,
    tempo_url: str,
    loki_url: str,
) -> FastAPI:
    application = FastAPI(title="jiuwenclaw-observability", docs_url=None, redoc_url=None)

    async def _relay(request: Request, upstream_base: str, tail: str, tag: str) -> Response:
        upstream_url = f"{upstream_base}/{tail}"
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
            logging.getLogger("jiuwenclaw-observability").error(
                "%s relay failed: %s", tag, exc
            )
            return Response(content=f"{tag} relay failed".encode(), status_code=502)

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

    @application.api_route(
        "/observability/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_observability(request: Request, tail: str) -> Response:
        return await _relay(request, prometheus_url, tail, "observability")

    @application.api_route(
        "/tempo/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_tempo(request: Request, tail: str) -> Response:
        return await _relay(request, tempo_url, tail, "tempo")

    @application.api_route(
        "/loki/{tail:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )
    async def relay_loki(request: Request, tail: str) -> Response:
        return await _relay(request, loki_url, tail, "loki")

    # ---- Audit Rules CRUD API ----

    @application.get("/api/audit/rules")
    async def list_audit_rules(detector: str | None = None):
        handler = audit_db.get_db()
        filters = {"detector": detector} if detector else None
        records = await handler.list_records("audit_rules", filters=filters, limit=500)
        return [r.to_dict() if hasattr(r, "to_dict") else dict(r) for r in records]

    @application.post("/api/audit/rules")
    async def create_audit_rule(rule: AuditRuleCreate):
        handler = audit_db.get_db()
        now = datetime.now(tz=ZoneInfo(os.getenv("TZ", "UTC")))
        payload = rule.model_dump()
        payload["enabled"] = int(payload["enabled"])
        payload["created_at"] = now
        payload["updated_at"] = now
        record = await handler.create("audit_rules", payload)
        return record.to_dict() if hasattr(record, "to_dict") else dict(record)

    @application.put("/api/audit/rules/{rule_id}")
    async def update_audit_rule(rule_id: int, rule: AuditRuleUpdate):
        handler = audit_db.get_db()
        data = {k: v for k, v in rule.model_dump().items() if v is not None}
        if "enabled" in data:
            data["enabled"] = int(data["enabled"])
        data["updated_at"] = datetime.now(tz=ZoneInfo(os.getenv("TZ", "UTC")))
        record = await handler.update("audit_rules", filters={"id": rule_id}, data=data)
        if record is None:
            return Response(content='{"detail":"Not Found"}', status_code=404, media_type="application/json")
        return record.to_dict() if hasattr(record, "to_dict") else dict(record)

    @application.delete("/api/audit/rules/{rule_id}")
    async def delete_audit_rule(rule_id: int):
        handler = audit_db.get_db()
        ok = await handler.delete("audit_rules", filters={"id": rule_id})
        if not ok:
            return Response(content='{"detail":"Not Found"}', status_code=404, media_type="application/json")
        return {"ok": True}

    @application.get("/api/audit/rules/export")
    async def export_audit_rules(detector: str | None = None):
        """Export rules as JSON (for agentserver to load on startup)."""
        return await audit_db.get_rules_for_detector(detector) if detector else await list_audit_rules()

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
        name="observability-web-static",
    )
    return application


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serve JiuwenClaw Observability Web static files."
    )
    parser.add_argument("--host", default=os.getenv("OBSERVABILITY_HOST", "0.0.0.0"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.getenv("OBSERVABILITY_PORT", "5274")),
    )
    parser.add_argument("--dist", default=str(_observability_dist()))
    parser.add_argument(
        "--prometheus-url",
        default=os.getenv("OBSERVABILITY_PROMETHEUS_URL", "http://prometheus.default:9090"),
        help="Prometheus base URL for /observability relay.",
    )
    parser.add_argument(
        "--tempo-url",
        default=os.getenv("OBSERVABILITY_TEMPO_URL", "http://tempo.default:3100"),
        help="Tempo base URL for /tempo relay.",
    )
    parser.add_argument(
        "--loki-url",
        default=os.getenv("OBSERVABILITY_LOKI_URL", "http://loki.default:3100/loki"),
        help="Loki base URL for /loki relay.",
    )
    parser.add_argument(
        "--log-level",
        default=os.getenv("OBSERVABILITY_LOG_LEVEL", "info"),
    )
    args = parser.parse_args()

    dist_root = Path(args.dist).expanduser().resolve()
    if not dist_root.is_dir():
        raise SystemExit(f"dist directory not found: {dist_root}")

    try:
        prometheus_url = _coerce_url(args.prometheus_url)
        tempo_url = _coerce_url(args.tempo_url)
        loki_url = _coerce_url(args.loki_url)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    # Init observability DB
    asyncio.run(audit_db.init_db())

    log = logging.getLogger("jiuwenclaw-observability")
    log.info("serving %s", dist_root)
    log.info("http://%s:%s", args.host, args.port)
    log.info("/observability relay -> %s", prometheus_url)
    log.info("/tempo relay -> %s", tempo_url)
    log.info("/loki relay -> %s", loki_url)
    log.info("/api/audit/rules -> observability DB")

    app = create_observability_app(dist_root, prometheus_url, tempo_url, loki_url)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level.lower())


if __name__ == "__main__":
    main()
