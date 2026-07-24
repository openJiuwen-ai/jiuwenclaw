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


def _allocate_jiuwenbox_port(host: str, preferred: int) -> int:
    """为 box-server 分配端口: preferred 空闲用它, 否则让 OS 分配随机空闲端口。

    docs §4.2/§5: runner.ensure_running 把 port 原样传给 uvicorn --port, 端口被占
    uvicorn 启动失败; runner 自身不做端口分配 (develop 那套 _allocate_internal_
    jiuwenbox_port 在 agent_ws_server, 未移植)。本函数在调用点补上: 用 socket
    bind 探测, preferred 占用则 bind(0) 让 OS 选随机端口。存在 TOCTOU race
    (测完到 uvicorn 起之间被占), 但 best-effort —— 真撞上 runner 内部 uvicorn
    会失败, 已有 warning 兜底。
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind((host, preferred))
        return preferred  # preferred 空闲
    except OSError:
        # preferred 被占, 让 OS 选一个: 复用同一 socket bind(0)
        try:
            sock.close()
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind((host, 0))
            allocated = sock.getsockname()[1]
            logger.info(
                "[AgentServer] jiuwenbox port %d busy, allocated random %d",
                preferred, allocated,
            )
            return allocated
        except OSError as exc:
            logger.warning(
                "[AgentServer] allocate random jiuwenbox port failed: %s", exc,
            )
            return preferred  # 兜底用 preferred, 让 uvicorn 自己失败报错
    finally:
        sock.close()


async def _ensure_jiuwenbox_internal() -> None:
    """``startup_mode=internal`` 时拉起本地 jiuwenbox-server 子进程.

    由 :func:`_run` 在 agent-server 启动链调用 (server.start 之后)。读
    :func:`get_sandbox_endpoint` / :func:`get_sandbox_runtime`: 仅当
    ``startup_mode == "internal"`` 且 sandbox ``enabled`` 时才 spawn, 避免未
    启用沙箱时白拉一个 jiuwenbox-server。spawn 后把 runner 实际监听的
    ``base_url`` 经 :func:`set_local_config` 回写 ``JIUWENCLAW_SANDBOX_URL``,
    让后续 :func:`get_sandbox_endpoint` 与 agent-core provider 拿到真实 url
    (端口被占时 runner 会换随机端口)。失败只记 warning, 不阻断 agent-server
    启动 —— 沙箱任务真正发起时 provider 会连不上而报错, 主进程照常跑。

    关停由 :func:`_run` 的 ``finally`` 段调 ``JiuwenBoxRunner.instance().stop()``。
    设计见 ``docs/windows_sandbox_officeace_integration_design.md`` §4.2。
    """
    from urllib.parse import urlparse

    from jiuwenclaw.agentserver.jiuwenbox_runner import JiuwenBoxRunner
    from jiuwenclaw.config import get_sandbox_endpoint, get_sandbox_runtime
    from jiuwenclaw.local_env_config import set_local_config

    try:
        endpoint = get_sandbox_endpoint()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AgentServer] read sandbox endpoint failed, skip jiuwenbox spawn: %s", exc)
        return

    if (endpoint.get("startup_mode") or "internal") != "internal":
        return  # external: jiuwenbox-server 由外部托管, 不 spawn

    try:
        runtime = get_sandbox_runtime()
    except Exception as exc:  # noqa: BLE001
        logger.warning("[AgentServer] read sandbox runtime failed, skip jiuwenbox spawn: %s", exc)
        return
    if not bool(runtime.get("enabled")):
        return  # sandbox 未启用, 不白拉 jiuwenbox-server

    # 解析 host:port (缺省 127.0.0.1:8321); url 为空也用缺省。
    host = "127.0.0.1"
    preferred_port = 8321
    url = (endpoint.get("url") or "").strip()
    if url:
        try:
            parsed = urlparse(url)
            host = parsed.hostname or host
            if parsed.port:
                preferred_port = parsed.port
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AgentServer] parse sandbox url %r failed, use default %s:%d: %s", url, host, preferred_port, exc)

    # 端口分配: preferred (8321 或 url 里的) 被占则换随机空闲 (docs §4.2/§5)。
    preferred_port = _allocate_jiuwenbox_port(host, preferred_port)

    # policy: Windows 用 windows-policy.yaml, Linux 用 default-policy.yaml;
    # 找不到 (None) 让 jiuwenbox-server 自身回落内置默认。
    policy_filename = "windows-policy.yaml" if sys.platform == "win32" else "default-policy.yaml"
    policy_path = JiuwenBoxRunner.resolve_policy_path(policy_filename)

    # 注入动态路径 env 给 box-server 子进程 (runner 用 dict(os.environ) 作子进程 env,
    # 故设 os.environ 即透传). docs §4.3:
    #   JIUWENBOX_BUNDLED_PYTHON = 打包 embeddable python 目录 (tools/python/),
    #                              _create_windows 对其授 allow_read (含 Execute);
    #   JIUWENBOX_VENV_DIR       = 宿主机 isolation_venv 目录,
    #                              _create_windows 对其授 allow_write (pip 写 site-packages).
    # 未设则 _create_windows 跳过对应 ACL (沙箱内 python/pip 任务会失败, 但不阻断启动)。
    try:
        from jiuwenclaw.runtime.pip_env import (
            ensure_runtime_venv, resolve_base_python,
        )

        venv_dir = ensure_runtime_venv()  # 首次创建后跨任务复用 (检 pyvenv.cfg 跳过)
        os.environ["JIUWENBOX_VENV_DIR"] = str(venv_dir)
        bundled_python = resolve_base_python()
        # resolve_base_python 返回 python.exe; 授权其所在目录 (allow_read 整目录)
        os.environ["JIUWENBOX_BUNDLED_PYTHON"] = str(bundled_python.parent)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[AgentServer] inject JIUWENBOX_BUNDLED_PYTHON/VENV_DIR failed: %s", exc,
        )

    runner = JiuwenBoxRunner.instance()
    ok = await runner.ensure_running(
        host=host,
        port=preferred_port,
        startup_mode="internal",
        policy_path=policy_path,
    )
    if not ok:
        tail = runner.get_stderr_tail(20)
        hint = "\n--- jiuwenbox stderr (tail) ---\n" + tail if tail else ""
        logger.warning(
            "[AgentServer] jiuwenbox internal spawn failed (%s:%d)%s",
            host, preferred_port, hint,
        )
        return

    # 回写真实 url (端口可能被占而换过)
    actual_url = runner.base_url
    if actual_url and actual_url != url:
        set_local_config("JIUWENCLAW_SANDBOX_URL", actual_url)
        logger.info("[AgentServer] jiuwenbox internal ready, sandbox url=%s", actual_url)


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

    # startup_mode=internal 且 sandbox enabled 时拉起本地 jiuwenbox-server 子进程
    # (失败不阻断 agent-server 启动)。关停在下方 finally 段。
    await _ensure_jiuwenbox_internal()

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

            await asyncio.to_thread(shutdown_jiuwenbox_sandboxes)
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

            await JiuwenBoxRunner.instance().stop()
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

