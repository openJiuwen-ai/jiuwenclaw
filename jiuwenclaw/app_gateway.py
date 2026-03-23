# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""jiuwenclaw-gateway: 独立启动 Gateway + Channel 进程。

用法：
    jiuwenclaw-gateway [--agent-server-url URL] [--host HOST] [--port PORT]

环境变量（优先级低于命令行参数）：
    AGENT_SERVER_URL          AgentServer 的 WebSocket 地址，默认 ws://127.0.0.1:18092
    AGENT_SERVER_HOST         AgentServer 主机（仅在未设置 AGENT_SERVER_URL 时作为回退）
    AGENT_SERVER_PORT         AgentServer 端口（仅在未设置 AGENT_SERVER_URL 时作为回退，默认 18092）
    WEB_HOST                  WebChannel 绑定地址，默认 127.0.0.1
    WEB_PORT                  WebChannel 绑定端口，默认 19000
    WEB_PATH                  WebChannel WebSocket 路径，默认 /ws
    AGENT_CONNECT_RETRY       连接 AgentServer 的最大重试次数，默认 20
    AGENT_CONNECT_RETRY_INTERVAL  每次重试间隔秒数，默认 3

WEB_HOST / WEB_PORT / WEB_PATH 说明：
    这三个变量配置的是 WebChannel（Gateway 的浏览器入站 WebSocket 服务）的监听地址。
    jiuwenclaw-web（前端静态文件服务器）会将浏览器发来的 /ws 请求反向代理到这个地址。

    连接链路：
        浏览器 ---> jiuwenclaw-web（默认 localhost:5173）
                        | 反向代理 /ws
                        v
                    jiuwenclaw-gateway（WEB_HOST:WEB_PORT/WEB_PATH = 127.0.0.1:19000/ws）

    若需跨机部署前端代理和 Gateway，需同时调整 WEB_HOST（Gateway 绑定地址）
    和 jiuwenclaw-web 的 --proxy-target 参数（使其指向 Gateway 机器的地址）。

部署拓扑：
    此进程包含 Gateway（MessageHandler/ChannelManager）和所有 Channel。
    通过 WebSocket 连接到 jiuwenclaw-agentserver 进程。
    若要将 AgentServer 部署到另一台机器，只需修改 AGENT_SERVER_URL。

重连机制：
    启动时若 AgentServer 尚未就绪，将每隔 AGENT_CONNECT_RETRY_INTERVAL 秒重试，
    最多 AGENT_CONNECT_RETRY 次。
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import logging
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from jiuwenclaw.utils import USER_WORKSPACE_DIR, prepare_workspace, logger

# 确保 workspace 初始化
_config_file = USER_WORKSPACE_DIR / "config" / "config.yaml"
if not _config_file.exists():
    prepare_workspace(overwrite=False)

# 减少 openjiuwen 内部日志
from openjiuwen.core.common.logging import LogManager
for _lg in LogManager.get_all_loggers().values():
    _lg.set_level(logging.CRITICAL)
from openjiuwen.core.foundation.llm import ProviderType

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=_ENV_FILE)

# ---- 复用 app.py 中的常量与帮助函数 ----
from jiuwenclaw.app import (  # noqa: E402
    _CONFIG_SET_ENV_MAP,
    _DummyBus,
    _FORWARD_NO_LOCAL_HANDLER_METHODS,
    _FORWARD_REQ_METHODS,
    _register_web_handlers,
)
from jiuwenclaw.channel import DingTalkChannel, DingTalkConfig, WhatsAppChannel, WhatsAppChannelConfig
from jiuwenclaw.config import (
    get_config,
    update_heartbeat_in_config,
    update_channel_in_config,
    update_browser_in_config,
    update_preferred_language_in_config,
)


def _make_session_id() -> str:
    ts = format(int(time.time() * 1000), "x")
    suffix = secrets.token_hex(3)
    return f"sess_{ts}_{suffix}"


async def _connect_with_retry(
    client,
    uri: str,
    *,
    max_retries: int = 20,
    interval: float = 3.0,
) -> None:
    """尝试连接 AgentServer，失败时自动重试。"""
    for attempt in range(1, max_retries + 1):
        try:
            await client.connect(uri)
            logger.info("[Gateway] 已连接到 AgentServer: %s", uri)
            return
        except Exception as exc:
            if attempt >= max_retries:
                logger.error(
                    "[Gateway] 连接 AgentServer 失败（已重试 %d 次）: %s  最后错误: %s",
                    attempt, uri, exc,
                )
                raise
            logger.warning(
                "[Gateway] 连接 AgentServer 失败（第 %d/%d 次）: %s  %s 秒后重试…",
                attempt, max_retries, exc, interval,
            )
            await asyncio.sleep(interval)


async def _run(agent_server_url: str, web_host: str, web_port: int, web_path: str) -> None:
    from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig
    from jiuwenclaw.channel.web_channel import WebChannel, WebChannelConfig
    from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
    from jiuwenclaw.channel.telegram_channel import TelegramChannel, TelegramChannelConfig
    from jiuwenclaw.channel.discord_channel import DiscordChannel, DiscordChannelConfig
    from jiuwenclaw.gateway import (
        GatewayHeartbeatService,
        HeartbeatConfig,
        WebSocketAgentServerClient,
    )
    from jiuwenclaw.gateway.channel_manager import ChannelManager
    from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.schema.message import Message, EventType, ReqMethod
    from jiuwenclaw.agentserver.memory.config import _load_config as _load_agent_config
    from jiuwenclaw.agentserver.tools.browser_tools import restart_local_browser_runtime_server

    logger.info("[Gateway] 正在启动，连接 AgentServer: %s", agent_server_url)

    # ---------- 连接 AgentServer（带重试） ----------
    max_retries = int(os.getenv("AGENT_CONNECT_RETRY", "20"))
    retry_interval = float(os.getenv("AGENT_CONNECT_RETRY_INTERVAL", "3"))

    client = WebSocketAgentServerClient(ping_interval=20.0, ping_timeout=20.0)
    await _connect_with_retry(
        client, agent_server_url,
        max_retries=max_retries,
        interval=retry_interval,
    )

    message_handler = MessageHandler(client)
    await message_handler.start_forwarding()

    # ---------- Cron ----------
    cron_store = CronJobStore()
    cron_scheduler = CronSchedulerService(
        store=cron_store, agent_client=client, message_handler=message_handler,
    )
    cron_controller = CronController.get_instance(store=cron_store, scheduler=cron_scheduler)

    # ---------- Heartbeat ----------
    heartbeat_cfg: dict | None = None
    channels_cfg: dict | None = None
    try:
        full_cfg = _load_agent_config()
        heartbeat_cfg = full_cfg.get("heartbeat") if isinstance(full_cfg, dict) else None
        channels_cfg = full_cfg.get("channels") if isinstance(full_cfg, dict) else None
    except Exception as exc:
        logger.warning("[Gateway] 读取 config.yaml heartbeat 配置失败，将使用默认值: %s", exc)

    if isinstance(heartbeat_cfg, dict):
        cfg_every = heartbeat_cfg.get("every")
        cfg_target = heartbeat_cfg.get("target")
        cfg_active_hours = heartbeat_cfg.get("active_hours")
    else:
        cfg_every = None
        cfg_target = None
        cfg_active_hours = None

    heartbeat_interval = float(
        os.getenv("HEARTBEAT_INTERVAL")
        or (str(cfg_every) if cfg_every is not None else "60")
    )
    heartbeat_timeout = float(os.getenv("HEARTBEAT_TIMEOUT", "30")) if os.getenv("HEARTBEAT_TIMEOUT") else None
    heartbeat_relay_channel = os.getenv("HEARTBEAT_RELAY_CHANNEL_ID") or (
        str(cfg_target) if cfg_target is not None else "web"
    )

    heartbeat_config = HeartbeatConfig(
        interval_seconds=heartbeat_interval,
        timeout_seconds=heartbeat_timeout,
        relay_channel_id=heartbeat_relay_channel,
        active_hours=cfg_active_hours if isinstance(cfg_active_hours, dict) else None,
    )
    heartbeat_service = GatewayHeartbeatService(
        client, heartbeat_config, message_handler=message_handler,
    )
    await heartbeat_service.start()

    # ---------- ChannelManager ----------
    initial_channels_conf: dict = channels_cfg if isinstance(channels_cfg, dict) else {}
    channel_manager = ChannelManager(message_handler, config=initial_channels_conf)

    def _on_config_saved(updated_env_keys: set[str] | None = None) -> bool:
        """配置写回 .env 后尝试热更新；需要通知远端 AgentServer 热更新。

        在独立部署模式下，热更新需要通过 WebSocket 协议通知 AgentServer。
        当前版本采用简单降级策略：直接安排 Gateway 进程重启（agentserver 需手动重启
        或由运维系统负责），日后可扩展热更新协议。
        """
        try:
            # 重启浏览器运行时（仅 Gateway 进程侧需要）
            browser_runtime_keys = {"MODEL_PROVIDER", "MODEL_NAME", "API_BASE", "API_KEY"}
            if updated_env_keys and (browser_runtime_keys & set(updated_env_keys)):
                restart_local_browser_runtime_server()
            return True
        except Exception as exc:
            logger.warning("[Gateway] 配置热更新失败，将延迟重启: %s", exc)
            _schedule_restart()
            return False

    def _do_restart() -> None:
        logger.info("[Gateway] 配置已写回 .env，正在重启 Gateway 进程…")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def _schedule_restart() -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(2.0, _do_restart)
        except RuntimeError:
            _do_restart()

    # ---------- WebChannel ----------
    web_config = WebChannelConfig(
        enabled=True, host=web_host, port=web_port, path=web_path,
    )
    web_channel = WebChannel(web_config, _DummyBus())
    _register_web_handlers(
        web_channel,
        agent_client=client,
        message_handler=message_handler,
        channel_manager=channel_manager,
        on_config_saved=_on_config_saved,
        heartbeat_service=heartbeat_service,
        cron_controller=cron_controller,
    )

    def _norm_and_forward(msg: Message) -> bool:
        method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
        if method_val not in _FORWARD_REQ_METHODS:
            return False
        is_stream = bool(msg.is_stream or method_val == ReqMethod.CHAT_SEND.value)
        params = dict(msg.params or {})
        if "query" not in params and "content" in params:
            params["query"] = params["content"]
        normalized = Message(
            id=msg.id,
            type=msg.type,
            channel_id=msg.channel_id,
            session_id=msg.session_id,
            params=params,
            timestamp=msg.timestamp,
            ok=msg.ok,
            req_method=getattr(msg, "req_method", None) or ReqMethod.CHAT_SEND,
            mode=msg.mode,
            is_stream=is_stream,
            stream_seq=msg.stream_seq,
            stream_id=msg.stream_id,
            metadata=msg.metadata,
        )
        channel_manager._message_handler.handle_message(normalized)
        logger.info("[Gateway] Web 入站 -> MessageHandler: id=%s channel_id=%s", msg.id, msg.channel_id)
        if method_val in _FORWARD_NO_LOCAL_HANDLER_METHODS:
            return True
        return False

    web_channel.on_message(_norm_and_forward)
    channel_manager._channels[web_channel.channel_id] = web_channel

    # ---------- 其他 Channel（按 config.yaml 动态管理） ----------
    feishu_channel = None
    feishu_task = None
    xiaoyi_channel = None
    xiaoyi_task = None
    dingtalk_channel = None
    dingtalk_task = None
    telegram_channel = None
    telegram_task = None
    discord_channel = None
    discord_task = None
    whatsapp_channel = None
    whatsapp_task = None

    _last_channels_conf: dict = {}

    def _should_restart_channel(name: str, old: dict, new: dict) -> bool:
        old_c = old.get(name) if isinstance(old, dict) else None
        new_c = new.get(name) if isinstance(new, dict) else None
        if (old_c is None) != (new_c is None):
            return True
        if old_c is None:
            return False
        return old_c != new_c

    async def _stop_channel(channel, task, channel_name: str, background_wait: bool = False) -> None:
        if task is not None:
            task.cancel()
            if background_wait:
                async def _wait():
                    try:
                        await task
                    except (TypeError, asyncio.CancelledError):
                        pass
                    except Exception as exc:
                        logger.warning("[Gateway] 等待 %sChannel 取消时忽略: %s", channel_name, exc)
                asyncio.create_task(_wait(), name=f"wait_{channel_name}_cancel")
            else:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                except Exception as exc:
                    logger.warning("[Gateway] 等待 %sChannel 任务时忽略: %s", channel_name, exc)
        if channel is not None:
            try:
                await asyncio.wait_for(channel.stop(), timeout=10.0)
            except (asyncio.TimeoutError, Exception) as exc:
                logger.warning("[Gateway] 停止 %sChannel 时忽略: %s", channel_name, exc)
            channel_manager.unregister_channel(channel.channel_id)

    def _is_channel_enabled(conf: dict | None, required_fields: list[str]) -> tuple[bool, str]:
        if conf is None:
            return False, "未配置"
        enabled_raw = conf.get("enabled", None)
        if enabled_raw is None:
            ok = all(conf.get(f) for f in required_fields)
            return ok, "" if ok else f"缺少 {','.join(required_fields)}"
        return bool(enabled_raw), "enabled = false" if not enabled_raw else ""

    async def _apply_channel_config(conf: dict) -> None:
        nonlocal feishu_channel, feishu_task, xiaoyi_channel, xiaoyi_task
        nonlocal dingtalk_channel, dingtalk_task, telegram_channel, telegram_task
        nonlocal discord_channel, discord_task, whatsapp_channel, whatsapp_task, _last_channels_conf

        changed = [
            c for c in ["feishu", "xiaoyi", "dingtalk", "telegram", "whatsapp", "discord"]
            if _should_restart_channel(c, _last_channels_conf, conf)
        ]
        _last_channels_conf = dict(conf or {})

        # ----- Feishu -----
        if "feishu" in changed:
            feishu_conf = conf.get("feishu") if isinstance(conf, dict) else None
            await _stop_channel(feishu_channel, feishu_task, "feishu")
            feishu_channel, feishu_task = None, None
            if isinstance(feishu_conf, dict):
                enabled, reason = _is_channel_enabled(feishu_conf, ["app_id", "app_secret"])
                if not enabled:
                    logger.info("[Gateway] channels.feishu.%s，FeishuChannel 未启用", reason)
                else:
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=str(feishu_conf.get("app_id") or "").strip(),
                        app_secret=str(feishu_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(feishu_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(feishu_conf.get("verification_token") or "").strip(),
                        allow_from=feishu_conf.get("allow_from") or [],
                        chat_id=str(feishu_conf.get("chat_id") or "").strip(),
                    )
                    feishu_channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(feishu_channel)
                    feishu_task = asyncio.create_task(feishu_channel.start(), name="feishu")
                    logger.info("[Gateway] FeishuChannel 已启动")

        # ----- Xiaoyi -----
        if "xiaoyi" in changed:
            xiaoyi_conf = conf.get("xiaoyi") if isinstance(conf, dict) else None
            await _stop_channel(xiaoyi_channel, xiaoyi_task, "xiaoyi")
            xiaoyi_channel, xiaoyi_task = None, None
            if isinstance(xiaoyi_conf, dict):
                enabled, reason = _is_channel_enabled(xiaoyi_conf, ["ak", "sk", "agent_id"])
                if not enabled:
                    logger.info("[Gateway] channels.xiaoyi.%s，XiaoyiChannel 未启用", reason)
                else:
                    xiaoyi_config = XiaoyiChannelConfig(
                        enabled=True,
                        ak=str(xiaoyi_conf.get("ak") or "").strip(),
                        sk=str(xiaoyi_conf.get("sk") or "").strip(),
                        agent_id=str(xiaoyi_conf.get("agent_id") or "").strip(),
                        ws_url1=str(xiaoyi_conf.get("ws_url1") or "wss://116.63.174.231/openclaw/v1/ws/link").strip(),
                        ws_url2=str(xiaoyi_conf.get("ws_url2") or "wss://hag.cloud.huawei.com/openclaw/v1/ws/link").strip(),
                        enable_streaming=bool(xiaoyi_conf.get("enable_streaming", True)),
                    )
                    xiaoyi_channel = XiaoyiChannel(xiaoyi_config, _DummyBus())
                    channel_manager.register_channel(xiaoyi_channel)
                    xiaoyi_task = asyncio.create_task(xiaoyi_channel.start(), name="xiaoyi")
                    logger.info("[Gateway] XiaoyiChannel 已启动")

        # ----- DingTalk -----
        if "dingtalk" in changed:
            dingtalk_conf = conf.get("dingtalk") if isinstance(conf, dict) else None
            await _stop_channel(dingtalk_channel, dingtalk_task, "dingtalk", background_wait=True)
            dingtalk_channel, dingtalk_task = None, None
            if isinstance(dingtalk_conf, dict):
                enabled, reason = _is_channel_enabled(dingtalk_conf, ["client_id", "client_secret"])
                if not enabled:
                    logger.info("[Gateway] channels.dingtalk.%s，DingtalkChannel 未启用", reason)
                else:
                    dingtalk_config = DingTalkConfig(
                        enabled=True,
                        client_id=str(dingtalk_conf.get("client_id") or "").strip(),
                        client_secret=str(dingtalk_conf.get("client_secret") or "").strip(),
                        allow_from=dingtalk_conf.get("allow_from") or [],
                    )
                    dingtalk_channel = DingTalkChannel(dingtalk_config, _DummyBus())
                    channel_manager.register_channel(dingtalk_channel)
                    dingtalk_task = asyncio.create_task(dingtalk_channel.start(), name="dingtalk")
                    logger.info("[Gateway] DingtalkChannel 已启动")

        # ----- Telegram -----
        if "telegram" in changed:
            telegram_conf = conf.get("telegram") if isinstance(conf, dict) else None
            await _stop_channel(telegram_channel, telegram_task, "telegram")
            telegram_channel, telegram_task = None, None
            if isinstance(telegram_conf, dict):
                enabled, reason = _is_channel_enabled(telegram_conf, ["bot_token"])
                if not enabled:
                    logger.info("[Gateway] channels.telegram.%s，TelegramChannel 未启用", reason)
                else:
                    from jiuwenclaw.channel.telegram_channel import TelegramChannelConfig
                    telegram_config = TelegramChannelConfig(
                        enabled=True,
                        bot_token=str(telegram_conf.get("bot_token") or "").strip(),
                        allow_from=telegram_conf.get("allow_from") or [],
                        parse_mode=str(telegram_conf.get("parse_mode") or "Markdown").strip(),
                        group_chat_mode=str(telegram_conf.get("group_chat_mode") or "mention").strip(),
                    )
                    telegram_channel = TelegramChannel(telegram_config, _DummyBus())
                    channel_manager.register_channel(telegram_channel)
                    telegram_task = asyncio.create_task(telegram_channel.start(), name="telegram")
                    logger.info("[Gateway] TelegramChannel 已启动")

        # ----- Discord -----
        if "discord" in changed:
            discord_conf = conf.get("discord") if isinstance(conf, dict) else None
            await _stop_channel(discord_channel, discord_task, "discord")
            discord_channel, discord_task = None, None
            if isinstance(discord_conf, dict):
                enabled, reason = _is_channel_enabled(discord_conf, ["bot_token"])
                if not enabled:
                    logger.info("[Gateway] channels.discord.%s，DiscordChannel 未启用", reason)
                else:
                    from jiuwenclaw.channel.discord_channel import DiscordChannelConfig
                    discord_config = DiscordChannelConfig(
                        enabled=True,
                        bot_token=str(discord_conf.get("bot_token") or "").strip(),
                        application_id=str(discord_conf.get("application_id") or "").strip(),
                        guild_id=str(discord_conf.get("guild_id") or "").strip(),
                        channel_id=str(discord_conf.get("channel_id") or "").strip(),
                        allow_from=discord_conf.get("allow_from") or [],
                    )
                    discord_channel = DiscordChannel(discord_config, _DummyBus())
                    channel_manager.register_channel(discord_channel)
                    discord_task = asyncio.create_task(discord_channel.start(), name="discord")
                    logger.info("[Gateway] DiscordChannel 已启动")

        # ----- WhatsApp -----
        if "whatsapp" in changed:
            whatsapp_conf = conf.get("whatsapp") if isinstance(conf, dict) else None
            await _stop_channel(whatsapp_channel, whatsapp_task, "whatsapp")
            whatsapp_channel, whatsapp_task = None, None
            if isinstance(whatsapp_conf, dict):
                bridge_ws_url = str(whatsapp_conf.get("bridge_ws_url") or "ws://127.0.0.1:19600/ws").strip()
                auto_start_bridge = bool(whatsapp_conf.get("auto_start_bridge", False))
                enabled_raw = whatsapp_conf.get("enabled", None)
                enabled = bool(bridge_ws_url) if enabled_raw is None else bool(enabled_raw)
                if not enabled or not bridge_ws_url:
                    logger.info("[Gateway] channels.whatsapp 未启用")
                else:
                    whatsapp_config = WhatsAppChannelConfig(
                        enabled=True,
                        enable_streaming=bool(whatsapp_conf.get("enable_streaming", True)),
                        bridge_ws_url=bridge_ws_url,
                        allow_from=whatsapp_conf.get("allow_from") or [],
                        default_jid=str(whatsapp_conf.get("default_jid") or "").strip(),
                        auto_start_bridge=auto_start_bridge,
                        bridge_command=str(whatsapp_conf.get("bridge_command") or "node scripts/whatsapp-bridge.js").strip(),
                        bridge_workdir=str(whatsapp_conf.get("bridge_workdir") or "").strip(),
                        bridge_env={str(k): str(v) for k, v in (whatsapp_conf.get("bridge_env") or {}).items()},
                    )
                    whatsapp_channel = WhatsAppChannel(whatsapp_config, _DummyBus())
                    channel_manager.register_channel(whatsapp_channel)
                    whatsapp_task = asyncio.create_task(whatsapp_channel.start(), name="whatsapp")
                    logger.info("[Gateway] WhatsAppChannel 已启动")

    channel_manager.set_config_callback(_apply_channel_config)
    await channel_manager.set_config(initial_channels_conf)

    await channel_manager.start_dispatch()
    await cron_scheduler.start()
    web_task = asyncio.create_task(web_channel.start(), name="web-channel")

    logger.info(
        "[Gateway] 已就绪: Web ws://%s:%s%s  AgentServer: %s  Ctrl+C 退出。",
        web_host, web_port, web_path, agent_server_url,
    )

    # 主循环
    try:
        await web_task
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        web_task.cancel()
        try:
            await web_task
        except asyncio.CancelledError:
            pass
        await web_channel.stop()

        for ch, task, name in [
            (feishu_channel, feishu_task, "feishu"),
            (xiaoyi_channel, xiaoyi_task, "xiaoyi"),
            (dingtalk_channel, dingtalk_task, "dingtalk"),
            (telegram_channel, telegram_task, "telegram"),
            (discord_channel, discord_task, "discord"),
            (whatsapp_channel, whatsapp_task, "whatsapp"),
        ]:
            if ch is not None and task is not None:
                task.cancel()
                try:
                    await task
                except (TypeError, asyncio.CancelledError):
                    pass
                await ch.stop()

        await cron_scheduler.stop()
        await channel_manager.stop_dispatch()
        await heartbeat_service.stop()
        await message_handler.stop_forwarding()
        await client.disconnect()
        logger.info("[Gateway] 已停止")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="jiuwenclaw-gateway",
        description=(
            "启动 JiuwenClaw Gateway + Channel 进程"
            "（独立部署模式，需搭配 jiuwenclaw-agentserver）"
        ),
    )
    parser.add_argument(
        "--agent-server-url", "-u",
        default=None,
        metavar="URL",
        help="AgentServer WebSocket 地址（默认：AGENT_SERVER_URL 环境变量，或 ws://127.0.0.1:18092）",
    )
    parser.add_argument(
        "--host", "-H",
        default=None,
        metavar="HOST",
        help=(
            "WebChannel 绑定地址（默认：WEB_HOST 环境变量，或 127.0.0.1）。"
            "jiuwenclaw-web 会将浏览器的 /ws 请求反向代理到此地址。"
        ),
    )
    parser.add_argument(
        "--port", "-p",
        type=int,
        default=None,
        metavar="PORT",
        help=(
            "WebChannel 绑定端口（默认：WEB_PORT 环境变量，或 19000）。"
            "jiuwenclaw-web 的 --proxy-target 需指向 host:port。"
        ),
    )
    parser.add_argument(
        "--web-path",
        default=None,
        metavar="PATH",
        help=(
            "WebChannel WebSocket 路径（默认：WEB_PATH 环境变量，或 /ws）。"
            "jiuwenclaw-web 的 /ws 反向代理会转发到此路径。"
        ),
    )
    args = parser.parse_args()

    agent_server_url = (
        args.agent_server_url
        or os.getenv("AGENT_SERVER_URL")
        or f"ws://{os.getenv('AGENT_SERVER_HOST', '127.0.0.1')}:{os.getenv('AGENT_SERVER_PORT', '18092')}"
    )
    web_host = args.host or os.getenv("WEB_HOST", "127.0.0.1")
    web_port = args.port or int(os.getenv("WEB_PORT", "19000"))
    web_path = args.web_path or os.getenv("WEB_PATH", "/ws")

    asyncio.run(_run(
        agent_server_url=agent_server_url,
        web_host=web_host,
        web_port=web_port,
        web_path=web_path,
    ))


if __name__ == "__main__":
    main()