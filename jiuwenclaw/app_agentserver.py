 # Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""jiuwenclaw-agentserver: 独立启动 AgentServer 进程。

用法：
    jiuwenclaw-agentserver [--host HOST] [--port PORT]

环境变量（优先级低于命令行参数）：
    AGENT_SERVER_HOST   AgentWebSocketServer 绑定地址，默认 127.0.0.1
    AGENT_SERVER_PORT   AgentWebSocketServer 绑定端口，默认 18092

部署拓扑：
    此进程只启动 AgentServer（JiuWenClaw + AgentWebSocketServer）。
    Gateway 进程（jiuwenclaw-gateway）通过 WebSocket 连接此服务。
    两个进程共享同一 workspace 目录（SQLite、记忆文件、技能等）。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from jiuwenclaw.utils import USER_WORKSPACE_DIR, prepare_workspace, logger

# 确保 workspace 初始化
_config_file = USER_WORKSPACE_DIR / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(dotenv_path=_PROJECT_ROOT / ".env")


class _NopCronScheduler:
    """无调度功能的占位 Scheduler，仅满足 CronController 构造要求。

    AgentServer 进程只需要 Cron 工具（CRUD 操作写 SQLite），实际的定时触发
    由 Gateway 进程的 CronSchedulerService 负责。
    """

    async def reload(self) -> None:
        pass

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def is_running(self) -> bool:
        return False


async def _run(host: str, port: int) -> None:
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.gateway import AgentWebSocketServer
    from jiuwenclaw.gateway.cron import CronController, CronJobStore

    logger.info("[AgentServer] 正在启动: ws://%s:%s", host, port)

    # ---------- CronController（工具侧，无调度） ----------
    cron_store = CronJobStore()
    CronController.get_instance(store=cron_store, scheduler=_NopCronScheduler())

    # ---------- JiuWenClaw Agent ----------
    agent = JiuWenClaw()

    # ---------- AgentWebSocketServer ----------
    server = AgentWebSocketServer(
        agent,
        host=host,
        port=port,
        ping_interval=20.0,
        ping_timeout=20.0,
    )
    await server.start()

    # create_instance 依赖 CronController 单例已初始化
    await agent.create_instance()

    logger.info(
        "[AgentServer] 已就绪，监听 ws://%s:%s  Ctrl+C 退出。",
        host, port,
    )

    # 保持运行，直到收到退出信号
    stop_event = asyncio.Event()

    def _on_signal() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        import signal
        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except (NotImplementedError, OSError):
        # Windows 不支持 add_signal_handler
        pass

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("[AgentServer] 正在停止…")
        await server.stop()
        logger.info("[AgentServer] 已停止")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-agentserver",
        description="启动 JiuwenClaw AgentServer（独立进程，供 jiuwenclaw-gateway 通过 WebSocket 连接）",
    )
    parser.add_argument(
        "--host", "-H",
        default=None,
        metavar="HOST",
        help="AgentWebSocketServer 绑定地址（默认：AGENT_SERVER_HOST 环境变量，或 127.0.0.1）",
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        metavar="PORT",
        help="AgentWebSocketServer 绑定端口（默认：AGENT_SERVER_PORT 环境变量，或 18092）",
    )
    args = parser.parse_args()

    host = args.host or os.getenv("AGENT_SERVER_HOST", "127.0.0.1")
    port = args.port or int(os.getenv("AGENT_SERVER_PORT", "18092"))

    asyncio.run(_run(host=host, port=port))


if __name__ == "__main__":
    main()