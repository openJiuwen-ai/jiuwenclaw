# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""企业版 Web Pod 入口（FastAPI + uvicorn）。

实现已迁至 ``jiuwenclaw.webserver``；本文件只保留 CLI 与启动，对外契约
（命令行参数 / 端口 / 模块入口 ``-m jiuwenclaw.app_enterprise_web`` / ``--relay-only``）不变。
``EnterpriseWebWsServer`` / ``CHAT_ACCEPT_METHODS`` 从本模块 re-export（保持单测导入路径）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
from pathlib import Path
from urllib.parse import urlparse

import uvicorn

from jiuwenclaw.channel.enterprise_web_uplink_config import get_enterprise_web_uplink_ws_settings
from jiuwenclaw.utils import get_logs_dir, get_multi_tenant_user_workspace_dir, get_user_workspace_dir
from jiuwenclaw.webserver.app import create_enterprise_broker_app, create_enterprise_static_app
from jiuwenclaw.webserver.common import WebRuntime, default_dist_dir, normalize_ws_target, setup_logger
# re-export：保持 `from jiuwenclaw.app_enterprise_web import EnterpriseWebWsServer, CHAT_ACCEPT_METHODS`
from jiuwenclaw.webserver.enterprise_broker import CHAT_ACCEPT_METHODS, EnterpriseWebWsServer

__all__ = ["CHAT_ACCEPT_METHODS", "EnterpriseWebWsServer", "main"]


def _no_signal_handlers() -> None:
    """空实现：多 server 同进程时禁用各自的信号处理，统一在 _serve 的 _main 里挂。"""


def _serve(servers: list[uvicorn.Server]) -> None:
    """同进程并发跑多个 uvicorn server（保持双端口拓扑），统一处理 SIGINT/SIGTERM。"""
    for s in servers:
        s.install_signal_handlers = _no_signal_handlers

    async def _main() -> None:
        loop = asyncio.get_running_loop()

        def _stop() -> None:
            for s in servers:
                s.should_exit = True

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _stop)
            except NotImplementedError:  # pragma: no cover (Windows)
                pass
        await asyncio.gather(*(s.serve() for s in servers))

    asyncio.run(_main())


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve enterprise web static files and Web Pod WebSocket server.")
    parser.add_argument("--host", default=os.getenv("JIUWENCLAW_WEB_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("JIUWENCLAW_WEB_PORT", "5173")),
                        help="HTTP port for static files.")
    parser.add_argument("--dist", default=str(default_dist_dir()))
    parser.add_argument("--ws-target", default="",
                        help="Override /ws tunnel target on HTTP port (default: local WS server).")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--ws-disable-compress", action="store_true")
    parser.add_argument("--relay-host", default=os.getenv("ENTERPRISE_WEB_WS_HOST", "0.0.0.0"),
                        help="WebSocket bind host (browser /ws and gateway /gateway).")
    parser.add_argument("--relay-port", type=int,
                        default=int(os.getenv("ENTERPRISE_WEB_WS_PORT", os.getenv("WEB_PORT", "19000"))),
                        help="WebSocket bind port (browser /ws and gateway /gateway).")
    parser.add_argument("--relay-browser-path",
                        default=os.getenv("ENTERPRISE_WEB_BROWSER_PATH", os.getenv("WEB_PATH", "/ws")))
    parser.add_argument("--relay-gateway-path", default=os.getenv("ENTERPRISE_WEB_GATEWAY_PATH", "/gateway"))
    parser.add_argument("--relay-only", action="store_true", help="Only run WebSocket server (no HTTP static server).")
    args = parser.parse_args()

    logs_root = get_logs_dir().resolve()
    log = setup_logger(logs_root, args.log_level)
    uvicorn_log = args.log_level.lower()
    relay_port = args.relay_port

    dist_dir = Path(args.dist).expanduser().resolve()
    if not args.relay_only and (not dist_dir.exists() or not dist_dir.is_dir()):
        raise SystemExit(f"dist directory not found or invalid: {dist_dir}")

    # broker（浏览器 /ws + 网关 /gateway）
    uplink = get_enterprise_web_uplink_ws_settings()
    broker = EnterpriseWebWsServer(
        host=args.relay_host, port=relay_port,
        browser_path=args.relay_browser_path, gateway_path=args.relay_gateway_path,
        logger=log,
    )

    # WebRuntime：供 broker 端口的 /file-api 与（非 relay 时）静态 app 共用。
    # ws_target 只取 host:port（path 由浏览器请求决定，避免重复 /ws）。
    if args.ws_target.strip():
        p = urlparse(normalize_ws_target(args.ws_target.strip()))
        ws_target_base = f"{p.scheme}://{p.netloc}"
    else:
        ws_target_base = f"ws://127.0.0.1:{relay_port}"
    workspace_root = get_multi_tenant_user_workspace_dir("default", "default") or get_user_workspace_dir()
    rt = WebRuntime(
        ws_target=ws_target_base, ws_disable_compress=args.ws_disable_compress,
        dist_dir=dist_dir, workspace_root=workspace_root, logs_root=logs_root, logger=log,
    )

    broker_cfg = uvicorn.Config(
        create_enterprise_broker_app(broker, rt),
        host=args.relay_host, port=relay_port, log_level=uvicorn_log,
        ws_ping_interval=uplink.ping_interval, ws_ping_timeout=uplink.ping_timeout,
    )
    servers = [uvicorn.Server(broker_cfg)]
    log.info("[jiuwenclaw-enterprise-web] WS ws://%s:%s%s(browser) %s(gateway) + /file-api",
             args.relay_host, relay_port, args.relay_browser_path, args.relay_gateway_path)

    if not args.relay_only:
        static_cfg = uvicorn.Config(
            create_enterprise_static_app(rt), host=args.host, port=args.port, log_level=uvicorn_log,
        )
        servers.append(uvicorn.Server(static_cfg))
        log.info("[jiuwenclaw-enterprise-web] serving %s | http://%s:%s | /ws -> %s",
                 dist_dir, args.host, args.port, ws_target_base)

    _serve(servers)


if __name__ == "__main__":
    main()
