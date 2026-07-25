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


async def _bootstrap_internal_jiuwenbox() -> None:
    """AgentServer boot 时按 ``get_sandbox_runtime()['startup_mode']`` 拉起 jiuwenbox.

    仅当 runtime ``startup_mode == "internal"`` 时执行 (默认 ``external`` 不 spawn)。
    失败只记 warning, 不阻塞 AgentServer。不做平台早退。
    """
    try:
        from jiuwenclaw.config import (
            DEFAULT_SANDBOX_POLICY_FILE,
            get_sandbox_endpoint,
            get_sandbox_runtime,
            persist_sandbox_endpoint_url,
            resolve_sandbox_policy_path,
        )
        from jiuwenclaw.agentserver.sandbox import (
            JiuwenBoxRunner,
            allocate_internal_jiuwenbox_port,
            parse_sandbox_host_port,
        )

        runtime = get_sandbox_runtime()
        startup_mode = runtime.get("startup_mode") or "external"
        if startup_mode != "internal":
            logger.info(
                "[AgentServer] sandbox startup_mode=%r; skipping jiuwenbox auto-start",
                startup_mode,
            )
            return

        endpoint = get_sandbox_endpoint()
        url = endpoint.get("url") or "http://127.0.0.1:8321"
        raw_policy = endpoint.get("policy_file") or ""
        effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
        policy_path = resolve_sandbox_policy_path(effective_policy_file)
        if policy_path is None or not policy_path.is_file():
            logger.warning(
                "[AgentServer] jiuwenbox auto-start skipped: "
                "policy_file=%r unresolved or missing (resolved=%s)",
                effective_policy_file,
                policy_path,
            )
            return

        host, preferred_port = parse_sandbox_host_port(url)
        port = allocate_internal_jiuwenbox_port(host, preferred_port)
        if port != preferred_port:
            url = f"http://{host}:{port}"
            logger.info(
                "[AgentServer] jiuwenbox auto-start: preferred port %d busy, using %d",
                preferred_port,
                port,
            )

        runner = JiuwenBoxRunner.instance()
        ok = await runner.ensure_running(
            host=host,
            port=port,
            startup_mode="internal",
            policy_path=policy_path,
        )
        if not ok:
            logger.warning(
                "[AgentServer] jiuwenbox auto-start failed at %s:%d (policy=%s). "
                "stderr tail:\n%s",
                host,
                port,
                policy_path,
                runner.get_stderr_tail(10) or "(empty)",
            )
            return

        try:
            persist_sandbox_endpoint_url(url)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentServer] persist sandbox URL after auto-start failed: %s",
                exc,
            )

        logger.info(
            "[AgentServer] jiuwenbox auto-started at %s (policy=%s)",
            url,
            policy_path,
        )
    except Exception:  # noqa: BLE001
        logger.exception(
            "[AgentServer] jiuwenbox auto-start raised; skipping"
        )


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


    # Best-effort: 显式 STARTUP_MODE=internal 时拉起本地 jiuwenbox 子进程。
    await _bootstrap_internal_jiuwenbox()

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
        try:
            from jiuwenclaw.agentserver.sandbox import JiuwenBoxRunner

            await JiuwenBoxRunner.instance().stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentServer] jiuwenbox runner stop failed: %s", exc,
            )

        try:
            from jiuwenclaw.agentserver.sandbox_lifecycle import (
                shutdown_jiuwenbox_sandboxes,
            )

            await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[AgentServer] jiuwenbox sandbox cleanup failed: %s", exc,
            )
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

