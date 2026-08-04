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
import atexit
import asyncio
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

# Apply lazy-memory patch before any code triggers `openjiuwen.core.memory`
# import (that chain eagerly pulls in alembic/sqlalchemy migration machinery,
# ~560ms saved at startup). LogManager itself does not transitively touch
# openjiuwen.core.memory, so it can be imported above safely.
from jiuwenclaw.runtime.lazy_memory_patch import apply_lazy_memory_patch

apply_lazy_memory_patch()

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

# interface_deep 改为懒加载：第一条聊天消息时由 create_adapter() 触发 import，
# 避免 AgentServer 冷启时加载整个 openjiuwen 框架（~170s 冷/ ~7s 热），
# 使权限配置 RPC 等非聊天请求无需等待即可秒级响应。

# 确保工作区已初始化（使用跨进程锁保护并发访问）
ensure_workspace_initialized(component_name="AgentServer")

configure_openjiuwen_logging_under_jiuwenclaw()
for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.INFO)

# Load env from user workspace config/.env
load_dotenv(dotenv_path=get_env_file())
from jiuwenclaw.local_env_config import mirror_bare_business_env_to_default_ns

mirror_bare_business_env_to_default_ns()

# 进程退出诊断：仅记录原因，不改变退出码/清理顺序。默认为 unknown，
# 若 atexit 仍为 unknown 且看不到 stopping…，多半是强杀/原生崩/os._exit。
_EXIT_REASON = "unknown"


def _set_exit_reason(reason: str) -> None:
    global _EXIT_REASON
    _EXIT_REASON = reason


def _atexit_log_exit_reason() -> None:
    logger.critical("[AgentServer] atexit reason=%s", _EXIT_REASON)


atexit.register(_atexit_log_exit_reason)


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

    from jiuwenclaw.agentserver.code_source_unicode import register_code_source_unicode_hook

    register_code_source_unicode_hook()

    # ---------- Telemetry 初始化 ----------
    init_telemetry()

    from jiuwenclaw.perf.config import init_perf_summary_config

    init_perf_summary_config()

    server = AgentWebSocketServer.get_instance(
        host=host,
        port=port,
        ping_interval=20.0,
        ping_timeout=300.0,
    )
    await server.start()
    # jiuwenbox-server 子进程的自动拉起已迁至 AgentWebSocketServer.start 的
    # _bootstrap_internal_jiuwenbox (按 config.yaml::sandbox.startup_mode 判断)。
    # 关停 box-server 子进程仍在下方 finally 段。

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
        await server.stop()
        from jiuwenclaw.perf.writer import flush_request_summary_writer
        from jiuwenclaw.perf.guard import run_perf_safe

        run_perf_safe(
            "AgentServer",
            "request summary flush",
            lambda: flush_request_summary_writer(timeout=5.0),
        )
        # jiuwenbox 关停顺序: 先 DELETE 远端沙箱, 再停 box-server 子进程。
        # shutdown_jiuwenbox_sandboxes 是 HTTP DELETE 给 box-server (清本进程 provider
        # 缓存里的 sandbox_id), 必须 box-server 还活着才能响应; 故它在 runner.stop()
        # 之前。runner.stop() 再停 box-server 子进程 (external 模式下 no-op)。若反过来
        # 先停子进程, DELETE 会全失败 (被 warning 吞不崩, 但沙箱没正常清理)。
        # 走线程是因为底层 httpx 是同步 API, 不能直接堵 event loop。
        # cleanup 自身已经吞了所有异常并永不抛, 外层 try/except 只是再加一道防线,
        # 兜住 import 阶段 (例如 venv 损坏) 这种极端情况。
        try:
            from jiuwenclaw.agentserver.sandbox_lifecycle import (
                shutdown_jiuwenbox_sandboxes,
            )

            logger.info("[AgentServer][sandbox] step 1: DELETE 远端沙箱 (box-server 活着)")
            released = await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
            logger.info("[AgentServer][sandbox] step 1 done: released=%s", released)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentServer] jiuwenbox sandbox cleanup failed: %s", exc,
            )
        # 停 internal 模式下由本 agent-server 拉起的 box-server 子进程。box-server
        # 进程退出时其 FastAPI lifespan shutdown 会兜底调 shutdown_all_sandboxes
        # (清上面 DELETE 漏网的沙箱)。失败不阻断后续 session_history flush。
        # Windows 上 proc.terminate()=TerminateProcess 是即时强杀, 不给 uvicorn 跑
        # lifespan shutdown 的机会 (Linux terminate()=SIGTERM 才 graceful) —— 有活
        # sandbox 时可能成孤儿, 留 Windows 实测时定 (docs §8.1 Q4 / 实测收窄)。
        try:
            from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner

            runner = JiuwenBoxRunner.instance()
            owned = runner.get_owned_endpoint()
            logger.info(
                "[AgentServer][sandbox] step 2: stop box-server 子进程 (owned=%s)",
                owned,
            )
            await runner.stop()
            logger.info("[AgentServer][sandbox] step 2 done")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AgentServer] jiuwenbox runner stop failed: %s", exc)
        # 落盘 session_history 缓冲层剩余数据（atexit 兜底的显式调用，确保 SIGTERM 退出前 flush）
        try:
            from jiuwenclaw.agentserver import session_history

            await asyncio.to_thread(session_history.shutdown)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AgentServer] history flush failed: %s", exc)
        logger.info("[AgentServer] stopped")
        _set_exit_reason("clean_shutdown")


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

    try:
        asyncio.run(_run(host=host, port=port))
        # 若 finally 已标 clean_shutdown，保留之；否则记录“正常返回但未走完收尾”
        if _EXIT_REASON == "unknown":
            _set_exit_reason("asyncio_run_returned")
    except SystemExit as exc:
        _set_exit_reason(f"SystemExit({exc.code})")
        raise
    except BaseException as exc:
        _set_exit_reason(f"{type(exc).__name__}: {exc}")
        raise


if __name__ == "__main__":
    main()

