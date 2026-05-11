# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone AgentServer entrypoint.

This process only starts:
- JiuWenClaw (agent runtime)
- AgentWebSocketServer (ws server for Gateway)

Gateway should be started separately and connect to this ws server.
Both processes share the same user workspace directory (~/.jiuwenclaw).

Supports ``--dotenv <path>`` for multi-instance isolation.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

# --- Early --dotenv parsing (before jiuwenclaw imports) ---
from jiuwenclaw.dotenv_early import parse_dotenv_early
parse_dotenv_early("jiuwenclaw-agentserver")

# --- Now safe to import jiuwenclaw modules ---
from jiuwenclaw.utils import (
    get_env_file,
    get_user_workspace_dir,
    logger,
    prepare_workspace,
    reset_free_search_runtime_flags,
)

# Ensure workspace initialized
_workspace_dir = get_user_workspace_dir()
_config_file = _workspace_dir / "config" / "config.yaml"
_new_workspace = _workspace_dir / "agent" / "jiuwenclaw_workspace"
_old_workspace = _workspace_dir / "agent" / "workspace"

# Initialize if config doesn't exist, or if legacy workspace exists but new doesn't (migration)
if not _config_file.exists() or (_old_workspace.exists() and not _new_workspace.exists()):
    prepare_workspace(overwrite=False)

for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.INFO)

# Load env from user workspace config/.env
load_dotenv(dotenv_path=get_env_file())
reset_free_search_runtime_flags()


async def _run(host: str, port: int) -> None:
    from openjiuwen.core.runner import Runner
    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
    from jiuwenclaw.agentserver.team import cleanup_team_runtime_state_once
    from jiuwenclaw.agentserver.team.remote_member_bootstrap import run_teammate_bootstrap_daemon
    from jiuwenclaw.extensions.manager import ExtensionManager
    from jiuwenclaw.extensions.registry import ExtensionRegistry

    logger.info("[AgentServer] starting: ws://%s:%s", host, port)

    from jiuwenclaw.agentserver.session_metadata import remove_team_mode_session_dirs_at_startup

    remove_team_mode_session_dirs_at_startup()
    deleted_tables, cleared_tables = await cleanup_team_runtime_state_once()
    if deleted_tables or cleared_tables:
        logger.info(
            "[AgentServer] startup team runtime cleanup deleted dynamic tables=%s cleared static tables=%s",
            deleted_tables,
            cleared_tables,
        )

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

    server = AgentWebSocketServer.get_instance(
        host=host,
        port=port
    )
    await server.start()

    logger.info("[AgentServer] ready: ws://%s:%s  Ctrl+C to stop", host, port)

    stop_event = asyncio.Event()
    teammate_bootstrap_task: asyncio.Task | None = None

    # Distributed teammate can receive bootstrap before any team-mode request arrives.
    # Keep a lightweight daemon alive so remote member bootstrap is consumed proactively.
    teammate_bootstrap_task = asyncio.create_task(
        run_teammate_bootstrap_daemon(stop_event=stop_event)
    )

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
        if teammate_bootstrap_task is not None:
            teammate_bootstrap_task.cancel()
            try:
                await teammate_bootstrap_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.warning("[AgentServer] teammate bootstrap daemon stop failed: %s", exc)
        await server.stop()
        logger.info("[AgentServer] stopped")


def main() -> None:
    from jiuwenclaw.dotenv_early import get_parsed_dotenv

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
    parser.add_argument(
        "--name",
        metavar="<name>",
        help="Start a named instance from instances.yaml.",
    )
    parser.add_argument(
        "--dotenv",
        metavar="<path>",
        help="Load environment from .env file (processed at startup, not used here).",
    )
    args = parser.parse_args()

    # Handle --name: check if bootstrap .env was loaded successfully
    # (parse_dotenv_early() already processed it at module import time)
    if args.name and get_parsed_dotenv() is None:
        # Early parsing failed - error was already printed
        raise SystemExit(1)

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
