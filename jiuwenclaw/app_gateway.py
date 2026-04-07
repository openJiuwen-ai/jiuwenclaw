# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Standalone Gateway entrypoint (split deployment).

This process starts:
- Gateway MessageHandler + ChannelManager
- WebChannel websocket server (browser inbound)
- Heartbeat service
- Cron scheduler service (triggers remote AgentServer via ws)

It connects to a remote/local AgentServer WebSocket endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from typing import Any

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

from jiuwenclaw.jiuwen_core_patch import apply_openai_model_client_patch
from jiuwenclaw.utils import (
    get_user_workspace_dir,
    get_env_file,
    prepare_workspace,
    connect_with_retry,
    restart_process,
)
from jiuwenclaw.local_env_config import decrypt
from jiuwenclaw.extensions.loader import load_all_extensions

apply_openai_model_client_patch()

# Ensure workspace initialized
_config_file = get_user_workspace_dir() / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

# Reduce openjiuwen internal logs (keep Gateway logs)
for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.CRITICAL)

load_dotenv(dotenv_path=get_env_file())

logger = logging.getLogger(__name__)


async def _run(
    agent_server_url: str, web_host: str, web_port: int, web_path: str
) -> None:
    from jiuwenclaw.telemetry import init_telemetry

    # 插件必须提前加载, 否则会影响配置的加解密解析
    extension_registry = await load_all_extensions()

    init_telemetry()
    from jiuwenclaw.channel.web_channel import WebChannel, WebChannelConfig
    from jiuwenclaw.config import get_config
    from jiuwenclaw.gateway import (
        GatewayHeartbeatService,
        HeartbeatConfig,
        WebSocketAgentServerClient,
    )
    from jiuwenclaw.gateway.channel_bootstrap import ChannelBootstrap
    from jiuwenclaw.gateway.channel_manager import ChannelManager
    from jiuwenclaw.gateway.cron import (
        CronController,
        CronJobStore,
        CronSchedulerService,
    )
    from jiuwenclaw.gateway.heartbeat import build_heartbeat_config
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.gateway.web_normalize import create_web_forward_handler
    from jiuwenclaw.gateway.config_hot_reload import handle_config_hot_reload
    from jiuwenclaw.app_web_handlers import (
        WebHandlersBindParams,
        _DummyBus,
        _CONFIG_SET_ENV_MAP,
        _CONFIG_YAML_KEYS,
        _register_web_handlers,
    )
    from jiuwenclaw.schema.message import Message
    from jiuwenclaw.updater import WindowsUpdaterService

    logger.info("[App] Gateway starting, connecting AgentServer: %s", agent_server_url)

    max_retries = int(os.getenv("AGENT_CONNECT_RETRY", "20"))
    retry_interval = float(os.getenv("AGENT_CONNECT_RETRY_INTERVAL", "3"))

    agent_server_ext = extension_registry.get_agent_server_client_extension()
    if agent_server_ext is not None:
        logger.info(
            "[App] 使用扩展提供的 AgentServerClient: %s", agent_server_ext.metadata.name
        )
        client = agent_server_ext.get_client()
    else:
        client = WebSocketAgentServerClient(ping_interval=20.0, ping_timeout=20.0)
    await connect_with_retry(
        client,
        agent_server_url,
        max_retries=max_retries,
        interval=retry_interval,
    )

    message_handler = MessageHandler(client)
    await message_handler.start_forwarding()

    cron_store = CronJobStore(
        path=get_user_workspace_dir() / "gateway" / "cron_jobs.json"
    )
    cron_scheduler = CronSchedulerService(
        store=cron_store,
        agent_client=client,
        message_handler=message_handler,
    )
    cron_controller = CronController.get_instance(
        store=cron_store, scheduler=cron_scheduler
    )
    message_handler.set_cron_controller(cron_controller)

    full_cfg: dict[str, Any] = {}
    heartbeat_cfg: dict | None = None
    channels_cfg: dict | None = None
    try:
        full_cfg = get_config()
        heartbeat_cfg = (
            full_cfg.get("heartbeat") if isinstance(full_cfg, dict) else None
        )
        channels_cfg = full_cfg.get("channels") if isinstance(full_cfg, dict) else None
    except Exception as e:  # noqa: BLE001
        logger.warning("[App] 读取 config.yaml heartbeat 配置失败，将使用默认值: %s", e)
        heartbeat_cfg = None
        channels_cfg = None

    # 配置解密后存储在内存中
    env_dict = {}
    for env_key in _CONFIG_SET_ENV_MAP.values():
        env_dict[env_key] = decrypt(env_key, os.getenv(env_key))
    client.set_or_update_server_config(
        config=dict(full_cfg or {}),
        env=env_dict,
    )

    heartbeat_config = build_heartbeat_config(heartbeat_cfg)
    heartbeat_service = GatewayHeartbeatService(
        client,
        heartbeat_config,
        message_handler=message_handler,
    )
    await heartbeat_service.start()

    initial_channels_conf: dict = channels_cfg if isinstance(channels_cfg, dict) else {}
    channel_manager = ChannelManager(message_handler, config=initial_channels_conf)
    updater_service = WindowsUpdaterService()

    async def on_config_saved(
        updated_env_keys: set[str] | None = None,
        *,
        env_updates: dict[str, str] | None = None,
        config_payload: dict[str, Any] | None = None,
    ) -> bool:
        return await handle_config_hot_reload(
            client, updated_env_keys, env_updates, config_payload
        )

    # 启动时将配置同步给agentserver
    await on_config_saved(
        set(_CONFIG_SET_ENV_MAP.values()) | _CONFIG_YAML_KEYS,
        env_updates=dict(env_dict),
        config_payload=dict(full_cfg or {}),
    )
    web_config = WebChannelConfig(
        enabled=True, host=web_host, port=web_port, path=web_path
    )
    web_channel = WebChannel(web_config, _DummyBus())
    _register_web_handlers(
        WebHandlersBindParams(
            channel=web_channel,
            agent_client=client,
            message_handler=message_handler,
            channel_manager=channel_manager,
            on_config_saved=on_config_saved,
            heartbeat_service=heartbeat_service,
            cron_controller=cron_controller,
            updater_service=updater_service,
        )
    )

    channel_manager.register_channel_with_inbound(
        web_channel, create_web_forward_handler(channel_manager)
    )

    # 使用 ChannelBootstrap 管理所有频道
    channel_bootstrap = ChannelBootstrap(channel_manager, _DummyBus())
    channel_manager.set_config_callback(channel_bootstrap.apply_config)
    await channel_bootstrap.apply_config(initial_channels_conf)

    await channel_manager.start_dispatch()
    await cron_scheduler.start()
    web_task = asyncio.create_task(web_channel.start(), name="web-channel")
    logger.info(
        "[App] 已启动: Web ws://%s:%s%s  AgentServer: %s  Ctrl+C 退出。",
        web_host,
        web_port,
        web_path,
        agent_server_url,
    )

    try:
        await web_task
    except KeyboardInterrupt:
        logger.info("收到 Ctrl+C，正在退出…")
    except asyncio.CancelledError:
        pass
    finally:
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass
        await web_channel.stop()

        # 使用 ChannelBootstrap 清理所有频道
        await channel_bootstrap.cleanup_all()

        await cron_scheduler.stop()
        await channel_manager.stop_dispatch()
        await heartbeat_service.stop()
        await message_handler.stop_forwarding()
        await client.disconnect()
        logger.info("[App] Gateway 已停止")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-gateway",
        description="Start JiuwenClaw Gateway + Channels (split deployment; connects to jiuwenclaw-agentserver).",
    )
    parser.add_argument(
        "--agent-server-url",
        "-u",
        default=None,
        metavar="URL",
        help="AgentServer WebSocket URL (default: AGENT_SERVER_URL or ws://AGENT_SERVER_HOST:AGENT_SERVER_PORT).",
    )
    parser.add_argument(
        "--host",
        "-H",
        default=None,
        metavar="HOST",
        help="WebChannel bind host (default: WEB_HOST or 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        "-p",
        type=int,
        default=None,
        metavar="PORT",
        help="WebChannel bind port (default: WEB_PORT or 19000).",
    )
    parser.add_argument(
        "--web-path",
        default=None,
        metavar="PATH",
        help="WebChannel ws path (default: WEB_PATH or /ws).",
    )
    args = parser.parse_args()

    default_host = os.getenv("AGENT_SERVER_HOST", "127.0.0.1")
    default_port = os.getenv("AGENT_SERVER_PORT") or os.getenv("AGENT_PORT", "18092")
    agent_server_url = (
        args.agent_server_url
        or os.getenv("AGENT_SERVER_URL")
        or f"ws://{default_host}:{default_port}"
    )
    web_host = args.host or os.getenv("WEB_HOST", "127.0.0.1")
    web_port = args.port or int(os.getenv("WEB_PORT", "19000"))
    web_path = args.web_path or os.getenv("WEB_PATH", "/ws")

    asyncio.run(
        _run(
            agent_server_url=agent_server_url,
            web_host=web_host,
            web_port=web_port,
            web_path=web_path,
        )
    )


if __name__ == "__main__":
    main()
