# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""简单版 Web 后端入口（FastAPI + uvicorn）。

实现已迁至 ``jiuwenclaw.webserver``；本文件只保留 CLI 与启动，对外契约
（命令行参数 / 端口 / 模块入口 ``-m jiuwenclaw.app_web`` / ``jiuwenclaw-web`` 脚本）不变。
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from jiuwenclaw.utils import (
    get_logs_dir,
    get_multi_tenant_user_workspace_dir,
    get_user_workspace_dir,
)
from jiuwenclaw.webserver.app import create_simple_web_app, create_upload_api_app
from jiuwenclaw.webserver.common import (
    WebRuntime,
    default_dist_dir,
    normalize_api_target,
    normalize_ws_target,
    setup_logger,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve JiuwenClaw frontend static files.")
    parser.add_argument("--host", default=os.getenv("JIUWENCLAW_WEB_HOST", "localhost"), help="Host to bind.")
    parser.add_argument("--port", type=int, default=int(os.getenv("JIUWENCLAW_WEB_PORT", "5173")), help="Port to bind.")
    parser.add_argument("--dist", default=str(default_dist_dir()), help="Path to frontend dist directory.")
    parser.add_argument(
        "--proxy-target",
        default=os.getenv("JIUWENCLAW_WEB_PROXY_TARGET", "http://127.0.0.1:19000"),
        help="Backend base URL for proxy (used as default for api/ws).",
    )
    parser.add_argument("--api-target", default="", help="Override backend target for /api (http/https).")
    parser.add_argument("--ws-target", default="", help="Override backend target for /ws (ws/wss/http/https).")
    parser.add_argument("--log-level", default="INFO", help="Log level. e.g. DEBUG/INFO/WARNING/ERROR")
    parser.add_argument(
        "--ws-disable-compress", action="store_true",
        help="Disable websocket compression for easier ws req/res/event debug logging.",
    )
    parser.add_argument(
        "--upload-api-only", action="store_true",
        help="Run a minimal server that only serves POST /file-api/upload-obs (for Vite dev).",
    )
    args = parser.parse_args()

    logs_root = get_logs_dir().resolve()
    logger = setup_logger(logs_root, args.log_level)
    uvicorn_log = args.log_level.lower()

    if args.upload_api_only:
        rt = WebRuntime(logs_root=logs_root, logger=logger)
        logger.info("[jiuwenclaw-upload-api] POST http://%s:%s/file-api/upload-obs", args.host, args.port)
        uvicorn.run(create_upload_api_app(rt), host=args.host, port=args.port, log_level=uvicorn_log)
        return

    dist_dir = Path(args.dist).expanduser().resolve()
    if not dist_dir.exists():
        raise SystemExit(f"dist directory not found: {dist_dir}")
    if not dist_dir.is_dir():
        raise SystemExit(f"dist path is not a directory: {dist_dir}")

    try:
        proxy_target = args.proxy_target.strip()
        api_target = normalize_api_target(args.api_target.strip() or proxy_target)
        ws_target = normalize_ws_target(args.ws_target.strip() or proxy_target)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    workspace_root = get_multi_tenant_user_workspace_dir("default", "default") or get_user_workspace_dir()
    rt = WebRuntime(
        api_target=api_target,
        ws_target=ws_target,
        ws_disable_compress=args.ws_disable_compress,
        dist_dir=dist_dir,
        workspace_root=workspace_root,
        logs_root=logs_root,
        logger=logger,
    )

    logger.info("[jiuwenclaw-web] serving %s", dist_dir)
    logger.info("[jiuwenclaw-web] http://%s:%s", args.host, args.port)
    logger.info("[jiuwenclaw-web] /api -> %s", api_target)
    logger.info("[jiuwenclaw-web] /ws  -> %s", ws_target)
    uvicorn.run(create_simple_web_app(rt), host=args.host, port=args.port, log_level=uvicorn_log)


if __name__ == "__main__":
    main()
