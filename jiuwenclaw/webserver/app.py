# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""按形态组装 FastAPI app。

注意路由注册顺序：``/file-api`` / ``/api`` / ``/ws`` 必须在静态 catch-all 之前注册，
否则 catch-all 会吃掉这些路径。
"""

from __future__ import annotations

from fastapi import FastAPI

from jiuwenclaw.webserver.common import WebRuntime, add_api_proxy, add_static_spa
from jiuwenclaw.webserver.enterprise_broker import EnterpriseWebWsServer, add_enterprise_ws_routes
from jiuwenclaw.webserver.file_api import build_file_api_router
from jiuwenclaw.webserver.ws_proxy import add_ws_proxy


def create_simple_web_app(rt: WebRuntime) -> FastAPI:
    """简单版 web 后端：静态+SPA + /api 反代 + /ws 反代 + /file-api 全部。"""
    app = FastAPI(title="jiuwenclaw-web", docs_url=None, redoc_url=None, openapi_url=None)
    # 1) 业务路由（先注册，优先匹配）
    app.include_router(build_file_api_router(rt))
    add_api_proxy(app, lambda: rt.api_target)
    add_ws_proxy(app, rt)
    # 2) 静态 + SPA 兜底（最后注册，catch-all）
    add_static_spa(app, rt.dist_dir)
    return app


def create_upload_api_app(rt: WebRuntime) -> FastAPI:
    """仅上传形态（等价 app_web --upload-api-only）：只挂 POST /file-api/upload-obs。"""
    app = FastAPI(title="jiuwenclaw-upload-api", docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(build_file_api_router(rt, upload_only=True))
    return app


def create_enterprise_broker_app(
    broker: EnterpriseWebWsServer, rt: WebRuntime | None = None
) -> FastAPI:
    """企业版 WS broker app：浏览器 /ws + 网关 uplink /gateway（默认监听 relay 端口）。

    传入 ``rt`` 时同时挂载 ``/file-api``（文件上传→MinIO / 工作区列读下载），使该端口成为
    统一前端(nginx)反代的 "web 后端"：``/ws`` 与 ``/file-api`` 同源(``--relay-only`` 下静态由
    nginx 承担,本进程只跑 broker + 文件)。``/file-api`` 为具体路径、无 catch-all,顺序无碍。
    """
    app = FastAPI(title="jiuwenclaw-enterprise-web-broker", docs_url=None, redoc_url=None, openapi_url=None)
    if rt is not None:
        app.include_router(build_file_api_router(rt))
    add_enterprise_ws_routes(app, broker)
    return app


def create_enterprise_static_app(rt: WebRuntime) -> FastAPI:
    """企业版静态 app：发 dist + 把 /ws 反代到 broker（不含 /api，与原 enterprise 静态服务一致）。"""
    app = FastAPI(title="jiuwenclaw-enterprise-web", docs_url=None, redoc_url=None, openapi_url=None)
    add_ws_proxy(app, rt)              # /ws → rt.ws_target（broker）
    add_static_spa(app, rt.dist_dir)   # 静态 + SPA 兜底（catch-all 最后）
    return app
