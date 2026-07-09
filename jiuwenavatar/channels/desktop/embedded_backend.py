# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.
"""Desktop embedded backend — AgentServer + Gateway in a single process.

Avoids spawning two full PyInstaller child processes (each ~1GB private memory)
while keeping the same split architecture over in-process WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import time

from jiuwenavatar.common.service_ports import DEFAULT_AGENT_SERVER_PORT, DEFAULT_WEB_PORT

from dotenv import load_dotenv

from jiuwenavatar.dotenv_early import parse_dotenv_early

parse_dotenv_early("jiuwenavatar-desktop-backend")

from jiuwenavatar.common.utils import (
    cleanup_team_files,
    get_env_file,
    get_user_workspace_dir,
    prepare_workspace,
    reset_free_search_runtime_flags,
    wait_for_tcp_port,
)

logger = logging.getLogger("jiuwenavatar.channels.desktop.backend")

_workspace_dir = get_user_workspace_dir()
_config_file = _workspace_dir / "config" / "config.yaml"
_new_workspace = _workspace_dir / "agent" / "workspace"
_old_workspace = _workspace_dir / "agent" / "jiuwenclaw_workspace"

cleanup_team_files(_workspace_dir)
if not _config_file.exists() or (_old_workspace.exists() and not _new_workspace.exists()):
    prepare_workspace(overwrite=False)

load_dotenv(dotenv_path=get_env_file(), override=True)
reset_free_search_runtime_flags()


def _configure_desktop_backend_env() -> None:
    """Tune retry/interval defaults for in-process agent+gateway startup."""
    os.environ.setdefault("AGENT_CONNECT_RETRY", "8")
    os.environ.setdefault("AGENT_CONNECT_RETRY_INTERVAL", "0.5")
    os.environ.setdefault("TRIGGER_STORE_WATCH_INTERVAL", "15")


async def _wait_for_tcp_port_async(
    host: str,
    port: int,
    *,
    timeout: float = 60.0,
    target_state: str = "connected",
) -> bool:
    """Wait for a TCP port without blocking the asyncio event loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = await asyncio.to_thread(
            wait_for_tcp_port,
            host,
            port,
            timeout=1.0,
            max_attempts=1,
            target_state=target_state,
        )
        if ready:
            return True
        await asyncio.sleep(0.25)
    return False


async def run_combined_backend(
    *,
    agent_host: str,
    agent_port: int,
    web_host: str,
    web_port: int,
    web_path: str = "/ws",
) -> None:
    from jiuwenavatar.gateway.app_gateway import _run as run_gateway
    from jiuwenavatar.server.app_agentserver import _run as run_agent

    agent_url = f"ws://{agent_host}:{agent_port}"
    logger.info(
        "[desktop-backend] starting combined backend agent=%s web=%s:%s",
        agent_url,
        web_host,
        web_port,
    )

    agent_task = asyncio.create_task(
        run_agent(agent_host, agent_port),
        name="embedded-agent",
    )

    if not await _wait_for_tcp_port_async(
        agent_host,
        agent_port,
        timeout=60.0,
        target_state="connected",
    ):
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        raise RuntimeError(f"AgentServer did not become ready on {agent_host}:{agent_port}")

    gateway_task = asyncio.create_task(
        run_gateway(agent_url, web_host, web_port, web_path),
        name="embedded-gateway",
    )

    if not await _wait_for_tcp_port_async(
        web_host,
        web_port,
        timeout=60.0,
        target_state="connected",
    ):
        agent_task.cancel()
        gateway_task.cancel()
        for task in (agent_task, gateway_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        raise RuntimeError(f"Gateway did not become ready on {web_host}:{web_port}")

    logger.info("[desktop-backend] agent+gateway ready on %s:%s", web_host, web_port)

    done, pending = await asyncio.wait(
        {agent_task, gateway_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    for task in pending:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    for task in done:
        exc = task.exception()
        if exc is not None:
            raise exc


def main() -> None:
    _configure_desktop_backend_env()

    agent_host = os.getenv("AGENT_SERVER_HOST", "127.0.0.1")
    agent_port = int(
        os.getenv("AGENT_SERVER_PORT") or os.getenv("AGENT_PORT", str(DEFAULT_AGENT_SERVER_PORT))
    )
    web_host = os.getenv("WEB_HOST", "127.0.0.1")
    web_port = int(os.getenv("WEB_PORT", str(DEFAULT_WEB_PORT)))
    web_path = os.getenv("WEB_PATH", "/ws")

    try:
        asyncio.run(
            run_combined_backend(
                agent_host=agent_host,
                agent_port=agent_port,
                web_host=web_host,
                web_port=web_port,
                web_path=web_path,
            )
        )
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
