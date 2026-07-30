# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Web 后端公共能力：日志、SSL、dist 解析、地址规范化、路径越权防护、静态+SPA、/api 反代。

这些原本散落在 ``app_web.py`` 的 ``http.server`` 实现里，现抽出为框架无关 / FastAPI 可复用的工具。
"""

from __future__ import annotations

import logging
import mimetypes
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response, StreamingResponse

from jiuwenclaw.agentserver.tools.ssl_config import get_insecure_ssl_context, get_ssl_verify
from jiuwenclaw.utils import get_root_dir, get_user_workspace_dir, is_package_installation

# 与原 _SpaStaticHandler.extensions_map 一致的自定义 MIME（覆盖部分系统默认）。
_EXTRA_MIME: dict[str, str] = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".wasm": "application/wasm",
}

# 反代时不应透传的 hop-by-hop 头（与原实现一致）。
_HOP_BY_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
_HTTP_PROXY_TIMEOUT = 30.0


@dataclass
class WebRuntime:
    """进程内共享运行时状态，取代原实现里挂在 handler 类上的可变属性。"""

    api_target: str = ""
    ws_target: str = ""
    ws_disable_compress: bool = False  # 可被 POST /file-api/ws-debug-config 改
    dist_dir: Path = field(default_factory=Path)
    workspace_root: Path = field(default_factory=Path)
    logs_root: Path = field(default_factory=Path)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("jiuwenclaw.webserver"))
    # 会话历史存储（ChatHistoryStore | None）；由 app_enterprise_web.main 注入，供 /api/sessions 使用。
    history_store: Any = None


# --------------------------------------------------------------------------- #
# dist 解析 / 地址规范化（行为与原 app_web 等价）
# --------------------------------------------------------------------------- #

def _package_dir() -> Path:
    return Path(__file__).resolve().parent.parent  # jiuwenclaw/


def default_dist_dir() -> Path:
    """默认前端 dist 目录（与原 _default_dist_dir 行为一致）。"""
    root = get_root_dir()
    if (root / "web" / "dist").exists():
        return root / "web" / "dist"
    pkg = _package_dir()
    if (pkg / "web" / "dist").exists():
        return pkg / "web" / "dist"
    return root / "web" / "dist"


def normalize_api_target(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"api target must be http/https: {value}")
    return value.rstrip("/")


def normalize_ws_target(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme in ("http", "https"):
        value = value.replace("http://", "ws://", 1).replace("https://", "wss://", 1)
        parsed = urlparse(value)
    if parsed.scheme not in ("ws", "wss"):
        raise ValueError(f"ws target must be ws/wss/http/https: {value}")
    return value.rstrip("/")


# --------------------------------------------------------------------------- #
# 日志（与原 _setup_logger 等价：写 ws-dev.log）
# --------------------------------------------------------------------------- #

def setup_logger(logs_root: Path, log_level: str) -> logging.Logger:
    logs_root.mkdir(parents=True, exist_ok=True)
    lg = logging.getLogger("jiuwenclaw.webserver")
    lg.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    lg.propagate = True
    for h in lg.handlers[:]:
        h.close()
        lg.removeHandler(h)
    formatter = logging.Formatter(
        fmt="%(asctime)s.%(msecs)03d [%(process)d] %(levelname)s %(name)s %(filename)s:%(lineno)d: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    fh = logging.FileHandler(logs_root / "ws-dev.log", mode="w", encoding="utf-8")
    fh.setFormatter(formatter)
    lg.addHandler(fh)
    return lg


# --------------------------------------------------------------------------- #
# 路径越权防护（与原 _is_path_under_allowed_root 等价）
# --------------------------------------------------------------------------- #

def is_path_under_allowed_root(target: Path, *, workspace_root: Path, logs_root: Path) -> bool:
    """文件操作仅允许落在 workspace_root / logs_root / 用户工作区之内。"""
    target_resolved = target.resolve()
    try:
        in_workspace = os.path.commonpath([str(workspace_root), str(target_resolved)]) == str(workspace_root)
        in_logs = os.path.commonpath([str(logs_root), str(target_resolved)]) == str(logs_root)
        user_ws = str(get_user_workspace_dir())
        in_user_ws = os.path.commonpath([user_ws, str(target_resolved)]) == user_ws
        return in_workspace or in_logs or in_user_ws
    except ValueError:
        return False


def guess_content_type(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix in _EXTRA_MIME:
        return _EXTRA_MIME[suffix]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


# --------------------------------------------------------------------------- #
# /api 反向代理（取代原 _proxy_http；用 httpx 异步转发）
# --------------------------------------------------------------------------- #

def add_api_proxy(app: FastAPI, get_api_target: Callable[[], str]) -> None:
    """注册 ``/api`` 反向代理（所有方法）。``get_api_target`` 返回后端基地址。"""

    async def _proxy(request: Request) -> Response:
        target = get_api_target()
        verify = get_ssl_verify()
        ssl_arg: Any = True if verify else get_insecure_ssl_context()
        body = await request.body()
        fwd_headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS and k.lower() != "host"
        }
        url = target.rstrip("/") + request.url.path
        if request.url.query:
            url += "?" + request.url.query
        async with httpx.AsyncClient(verify=ssl_arg, timeout=_HTTP_PROXY_TIMEOUT, follow_redirects=False) as client:
            try:
                upstream = await client.request(
                    request.method, url, content=body, headers=fwd_headers,
                )
            except Exception:  # noqa: BLE001
                return Response(status_code=502, content=b"proxy http error")
        resp_headers = {
            k: v for k, v in upstream.headers.items()
            if k.lower() not in _HOP_BY_HOP_HEADERS
        }
        return Response(
            content=upstream.content, status_code=upstream.status_code, headers=resp_headers,
        )

    app.add_api_route(
        "/api/{rest:path}", _proxy,
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
    )


# --------------------------------------------------------------------------- #
# 静态文件 + SPA 兜底（取代原 _SpaStaticHandler.send_head；catch-all 必须最后注册）
# --------------------------------------------------------------------------- #

def add_static_spa(app: FastAPI, dist_dir: Path) -> None:
    """注册静态文件 + SPA 兜底：命中文件发文件，否则回 index.html。

    **必须在所有 /api、/ws、/file-api 路由之后注册**（catch-all 会吃掉未匹配路径）。
    """
    base_dir = dist_dir.resolve()
    index_html = base_dir / "index.html"

    @app.get("/{full_path:path}")
    async def _spa(full_path: str) -> Response:  # noqa: ANN202
        rel = full_path.lstrip("/") or "index.html"
        target = (base_dir / rel).resolve()
        # 目录穿越防护：必须落在 dist 根下
        in_base = os.path.commonpath([str(base_dir), str(target)]) == str(base_dir)
        if in_base and target.is_file():
            return FileResponse(target, media_type=guess_content_type(str(target)))
        return FileResponse(index_html, media_type="text/html; charset=utf-8")
