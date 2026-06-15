# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone AgentServer entrypoint.

This process only starts:
- JiuWenClaw (agent runtime)
- AgentWebSocketServer (ws server for Gateway)

Gateway should be started separately and connect to this ws server.
Both processes share the same user workspace directory (~/.jiuwenclaw).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

from jiuwenclaw.jiuwen_core_patch import (
    apply_openai_model_client_patch,
    configure_openjiuwen_logging_under_jiuwenclaw,
)
from jiuwenclaw.runtime.shell_pip_patch import apply_shell_pip_isolation_patch
from jiuwenclaw.utils import (
    get_env_file,
    ensure_workspace_initialized,
    migrate_legacy_user_config_if_needed,
    logger,
)

apply_openai_model_client_patch()
apply_shell_pip_isolation_patch()
migrate_legacy_user_config_if_needed()

try:
    import jiuwenclaw.agentserver.deep_agent.interface_deep  # 重要：优化冷启动性能，不要删除
except ImportError:
    # Fallback for environments where interface_deep dependencies are not available.
    # The module will be imported lazily when needed in _run().
    pass

# 确保工作区已初始化（使用跨进程锁保护并发访问）
ensure_workspace_initialized(component_name="AgentServer")

configure_openjiuwen_logging_under_jiuwenclaw()
for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.INFO)

# Load env from user workspace config/.env
load_dotenv(dotenv_path=get_env_file())


class _NopCronScheduler:
    """A no-op scheduler placeholder for CronController.

    In split deployment, AgentServer only provides cron CRUD storage.
    Actual scheduling/triggering is handled by the Gateway process.
    """

    async def reload(self) -> None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    @staticmethod
    def is_running() -> bool:
        return False


async def _run(host: str, port: int) -> None:
    from openjiuwen.core.runner import Runner
    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
    from jiuwenclaw.extensions.manager import ExtensionManager
    from jiuwenclaw.extensions.registry import ExtensionRegistry
    from jiuwenclaw.telemetry import init_telemetry

    logger.info("[AgentServer] starting: ws://%s:%s", host, port)

    from jiuwenclaw.agentserver.session_metadata import remove_team_mode_session_dirs_at_startup

    remove_team_mode_session_dirs_at_startup()

    # ---------- 扩展系统初始化 ----------
    callback_framework = Runner.callback_framework
    extension_registry = ExtensionRegistry.create_instance(
        callback_framework=callback_framework,
        config={},
        logger=logger,
    )
    extension_manager = ExtensionManager(
        registry=extension_registry,
    )
    await extension_manager.load_all_extensions()
    logger.info("[AgentServer] 扩展加载完成，共 %d 个", len(extension_manager.list_extensions()))

    try:
        from jiuwenclaw.infrastructure.log_masking.engine import LogMaskingEngine

        await LogMaskingEngine.reload_log_masking_from_gateway_db()
        logger.info("[AgentServer] log masking rules loaded from Gateway DB (if any)")
    except Exception:  # noqa: BLE001
        logger.warning("[AgentServer] log_masking_rule cold load skipped", exc_info=True)

    if os.getenv("AGENT_RUNTIME", "").strip():
        try:
            from jiuwenclaw.agentserver.memory.config import reload_embed_config_from_gateway_db

            await reload_embed_config_from_gateway_db()
            logger.info("[AgentServer] embed_config loaded from Gateway DB (if any)")
        except Exception:  # noqa: BLE001
            logger.warning("[AgentServer] embed_config cold load skipped", exc_info=True)

    if os.getenv("AGENT_RUNTIME", "").strip():
        try:
            from jiuwenclaw.agentserver.memory.config import reload_task_memory_config_from_gateway_db

            await reload_task_memory_config_from_gateway_db()
            logger.info("[AgentServer] task_memory_config loaded from Gateway DB (if any)")
        except Exception:  # noqa: BLE001
            logger.warning("[AgentServer] task_memory_config cold load skipped", exc_info=True)

    if os.getenv("AGENT_RUNTIME", "").strip():
        try:
            from jiuwenclaw.utils import reload_logging_levels_from_gateway_db

            await reload_logging_levels_from_gateway_db()
            logger.info("[AgentServer] logging levels loaded from Gateway DB (if any)")
        except Exception:  # noqa: BLE001
            logger.warning("[AgentServer] logging_config cold load skipped", exc_info=True)

    # ---------- Telemetry 初始化 ----------
    init_telemetry()

    server = AgentWebSocketServer.get_instance(
        host=host,
        port=port,
        ping_interval=20.0,
        ping_timeout=300.0,
    )
    await server.start()

    logger.info("[AgentServer] ready: ws://%s:%s  Ctrl+C to stop", host, port)

    stop_event = asyncio.Event()

    def _on_signal() -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    try:
        import signal

        loop.add_signal_handler(signal.SIGINT, _on_signal)
        loop.add_signal_handler(signal.SIGTERM, _on_signal)
    except (NotImplementedError, OSError):
        pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("[AgentServer] stopping…")
        await extension_manager.shutdown_all_extensions()
        await server.stop()
        # jiuwenbox 服务端没有 idle TTL, 本进程退出后已创建的 sandbox 会在 jiuwenbox
        # 一侧持续占用直到 jiuwenbox 自己重启。在此处主动 DELETE 掉本进程缓存里的
        # sandbox 列表; 走线程是因为底层 httpx 是同步 API, 不能直接堵 event loop。
        # cleanup 自身已经吞了所有异常并永不抛, 外层 try/except 只是再加一道防线,
        # 兜住 import 阶段 (例如 venv 损坏) 这种极端情况。
        try:
            from jiuwenclaw.agentserver.sandbox_lifecycle import (
                shutdown_jiuwenbox_sandboxes,
            )

            await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentServer] jiuwenbox sandbox cleanup failed: %s", exc,
            )
        logger.info("[AgentServer] stopped")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-agentserver",
        description="Start JiuwenClaw AgentServer (standalone process for Gateway to connect).",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        metavar="PORT",
        help="Bind port (default: AGENT_SERVER_PORT env or 18092).",
    )
    args = parser.parse_args()

    host = os.getenv("AGENT_SERVER_HOST", "127.0.0.1")
    port = args.port
    if port is None:
        for key in ("AGENT_SERVER_PORT", "AGENT_PORT"):
            raw = os.getenv(key)
            if raw:
                port = int(raw)
                break
        else:
            port = 18092

    asyncio.run(_run(host=host, port=port))


if __name__ == "__main__":
    main()

