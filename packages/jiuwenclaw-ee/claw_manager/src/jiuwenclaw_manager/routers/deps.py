from __future__ import annotations

import httpx
from fastapi import Request
from openjiuwen_runtime.foundation.db.handler import DBHandler


def get_db_handler(request: Request) -> DBHandler:
    handler = getattr(request.app.state, "db_handler", None)
    if handler is None:
        raise RuntimeError("db_handler not initialized on app.state")
    return handler


def get_http_client(request: Request) -> httpx.AsyncClient:
    client = getattr(request.app.state, "http_client", None)
    if client is None:
        raise RuntimeError("http_client not initialized on app.state")
    return client
