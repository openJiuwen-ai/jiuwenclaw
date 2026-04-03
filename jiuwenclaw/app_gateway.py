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
import sys
import inspect
from typing import Any

from dotenv import load_dotenv
from openjiuwen.core.common.logging import LogManager

from jiuwenclaw.jiuwen_core_patch import apply_openai_model_client_patch
from jiuwenclaw.utils import get_user_workspace_dir, get_env_file, prepare_workspace
from jiuwenclaw.local_env_config import decrypt

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


async def _connect_with_retry(
    client,
    uri: str,
    *,
    max_retries: int = 20,
    interval: float = 3.0,
) -> None:
    for attempt in range(1, max_retries + 1):
        try:
            await client.connect(uri)
            logger.info("[App] connected to AgentServer: %s", uri)
            return
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_retries:
                logger.error(
                    "[App] connect AgentServer failed after %d tries: %s  last=%s",
                    attempt,
                    uri,
                    exc,
                )
                raise
            logger.warning(
                "[App] connect AgentServer failed (%d/%d): %s  retry in %s s…",
                attempt,
                max_retries,
                exc,
                interval,
            )
            await asyncio.sleep(interval)


async def load_all_extensions():
    from openjiuwen.core.runner import Runner
    from jiuwenclaw.extensions import ExtensionManager, ExtensionRegistry
    callback_framework = Runner.callback_framework
    extension_registry = ExtensionRegistry.create_instance(
        callback_framework=callback_framework,
        config={},
        logger=logger,
    )
    extension_manager = ExtensionManager(registry=extension_registry)
    await extension_manager.load_all_extensions()
    logger.info("[App] 扩展加载完成，共 %d 个", len(extension_manager.list_extensions()))
    return extension_registry


async def _run(agent_server_url: str, web_host: str, web_port: int, web_path: str) -> None:
    # 插件必须提前加载, 否则会影响配置的加解密解析
    extension_registry = await load_all_extensions()
    from jiuwenclaw.channel import (
        DingTalkChannel,
        DingTalkConfig,
        WhatsAppChannel,
        WhatsAppChannelConfig,
        WechatChannel,
        WechatConfig,
    )
    from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig
    from jiuwenclaw.channel.web_channel import WebChannel, WebChannelConfig
    from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig
    from jiuwenclaw.channel.telegram_channel import TelegramChannel, TelegramChannelConfig
    from jiuwenclaw.channel.discord_channel import DiscordChannel, DiscordChannelConfig
    from jiuwenclaw.channel.wecom_channel import WecomChannel, WecomConfig
    from jiuwenclaw.config import get_config
    from jiuwenclaw.gateway import (
        GatewayHeartbeatService,
        HeartbeatConfig,
        WebSocketAgentServerClient,
    )
    from jiuwenclaw.gateway.channel_manager import ChannelManager
    from jiuwenclaw.gateway.cron import CronController, CronJobStore, CronSchedulerService
    from jiuwenclaw.gateway.message_handler import MessageHandler
    from jiuwenclaw.app_web_handlers import (
        WebHandlersBindParams,
        _DummyBus,
        _CONFIG_SET_ENV_MAP,
        _CONFIG_YAML_KEYS,
        _FORWARD_NO_LOCAL_HANDLER_METHODS,
        _FORWARD_REQ_METHODS,
        _register_web_handlers,
    )
    from jiuwenclaw.schema.message import Message, ReqMethod
    from jiuwenclaw.updater import WindowsUpdaterService

    def _do_restart() -> None:
        logger.info("[App] 配置已写回 .env，正在重启 Gateway 服务…")
        os.execv(sys.executable, [sys.executable, *sys.argv])

    def _schedule_restart() -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.call_later(2.0, _do_restart)
        except RuntimeError:
            _do_restart()

    logger.info("[App] Gateway starting, connecting AgentServer: %s", agent_server_url)

    max_retries = int(os.getenv("AGENT_CONNECT_RETRY", "20"))
    retry_interval = float(os.getenv("AGENT_CONNECT_RETRY_INTERVAL", "3"))

    agent_server_ext = extension_registry.get_agent_server_client_extension()
    if agent_server_ext is not None:
        logger.info("[App] 使用扩展提供的 AgentServerClient: %s", agent_server_ext.metadata.name)
        client = agent_server_ext.get_client()
    else:
        client = WebSocketAgentServerClient(ping_interval=20.0, ping_timeout=20.0)
    await _connect_with_retry(
        client,
        agent_server_url,
        max_retries=max_retries,
        interval=retry_interval,
    )

    message_handler = MessageHandler(client)
    await message_handler.start_forwarding()

    cron_store = CronJobStore()
    cron_scheduler = CronSchedulerService(
        store=cron_store,
        agent_client=client,
        message_handler=message_handler,
    )
    cron_controller = CronController.get_instance(store=cron_store, scheduler=cron_scheduler)

    full_cfg: dict[str, Any] = {}
    heartbeat_cfg: dict | None = None
    channels_cfg: dict | None = None
    try:
        full_cfg = get_config()
        heartbeat_cfg = full_cfg.get("heartbeat") if isinstance(full_cfg, dict) else None
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
        client,
        heartbeat_config,
        message_handler=message_handler,
    )
    await heartbeat_service.start()

    initial_channels_conf: dict = channels_cfg if isinstance(channels_cfg, dict) else {}
    channel_manager = ChannelManager(message_handler, config=initial_channels_conf)
    updater_service = WindowsUpdaterService()

    async def _on_config_saved(
        updated_env_keys: set[str] | None = None,
        *,
        env_updates: dict[str, str] | None = None,
        config_payload: dict[str, Any] | None = None,
    ) -> bool:
        browser_runtime_keys = {
            "MODEL_PROVIDER",
            "MODEL_NAME",
            "API_BASE",
            "API_KEY",
            "VIDEO_PROVIDER",
            "VIDEO_MODEL_NAME",
            "VIDEO_API_BASE",
            "VIDEO_API_KEY",
            "AUDIO_PROVIDER",
            "AUDIO_MODEL_NAME",
            "AUDIO_API_BASE",
            "AUDIO_API_KEY",
            "VISION_PROVIDER",
            "VISION_MODEL_NAME",
            "VISION_API_BASE",
            "VISION_API_KEY",
        }
        try:
            client.set_or_update_server_config(
                config=dict(config_payload or {}),
                env=dict(env_updates or {}),
            )

            from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
            from jiuwenclaw.schema.message import ReqMethod
            import uuid

            reload_env = e2a_from_agent_fields(
                request_id=f"agent-reload-{uuid.uuid4().hex[:8]}",
                channel_id="",
                req_method=ReqMethod.AGENT_RELOAD_CONFIG,
                params={
                    # config: 本次保存后的完整配置快照，Agent 优先使用它而不是本地 yaml。
                    "config": dict(config_payload or {}),
                    # env: 本次更新的环境变量增量；未出现的 key 表示不变。
                    "env": dict(env_updates or {}),
                },
            )
            await client.send_request(reload_env)

            if updated_env_keys and (browser_runtime_keys & set(updated_env_keys)):
                restart_env = e2a_from_agent_fields(
                    request_id=f"browser-restart-{uuid.uuid4().hex[:8]}",
                    channel_id="",
                    req_method=ReqMethod.BROWSER_RUNTIME_RESTART,
                )
                await client.send_request(restart_env)
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("[App] 配置热更新失败，将延迟重启: %s", e)
            _schedule_restart()
            return False
    # 启动时将配置同步给agentserver
    callback_result = _on_config_saved(
        set(_CONFIG_SET_ENV_MAP.values()) | _CONFIG_YAML_KEYS,
        env_updates=dict(env_dict),
        config_payload=dict(full_cfg or {})
    )
    if inspect.isawaitable(callback_result):
        await callback_result
    web_config = WebChannelConfig(enabled=True, host=web_host, port=web_port, path=web_path)
    web_channel = WebChannel(web_config, _DummyBus())
    _register_web_handlers(
        WebHandlersBindParams(
            channel=web_channel,
            agent_client=client,
            message_handler=message_handler,
            channel_manager=channel_manager,
            on_config_saved=_on_config_saved,
            heartbeat_service=heartbeat_service,
            cron_controller=cron_controller,
            updater_service=updater_service,
        )
    )

    def _norm_and_forward(msg: Message) -> bool:
        method_val = getattr(getattr(msg, "req_method", None), "value", None) or ""
        if method_val not in _FORWARD_REQ_METHODS:
            return False
        is_stream = bool(
            msg.is_stream
            or method_val in (ReqMethod.CHAT_SEND.value, ReqMethod.HISTORY_GET.value)
        )
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
        channel_manager.deliver_to_message_handler(normalized)
        logger.info("[App] Web 入站 -> MessageHandler: id=%s channel_id=%s", msg.id, msg.channel_id)
        if method_val in _FORWARD_NO_LOCAL_HANDLER_METHODS:
            return True
        return False

    channel_manager.register_channel_with_inbound(web_channel, _norm_and_forward)

    feishu_channel = None
    feishu_task = None
    feishu_enterprise_channels: dict[str, FeishuChannel] = {}
    feishu_enterprise_tasks: dict[str, asyncio.Task] = {}
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
    wecom_channel = None
    wecom_task = None
    wechat_channel = None
    wechat_task = None

    _last_channels_conf: dict = {}

    def _should_restart_channel(channel_name: str, old_conf: dict, new_conf: dict) -> bool:
        old_channel_conf = old_conf.get(channel_name) if isinstance(old_conf, dict) else None
        new_channel_conf = new_conf.get(channel_name) if isinstance(new_conf, dict) else None
        if (old_channel_conf is None) != (new_channel_conf is None):
            return True
        if old_channel_conf is None:
            return False
        return old_channel_conf != new_channel_conf

    async def _stop_channel(channel, task, channel_name: str, background_wait: bool = False) -> None:
        if task is not None:
            task.cancel()
            if background_wait:

                async def wait_cancel():
                    try:
                        await task
                    except (TypeError, asyncio.CancelledError):
                        logger.info("[App] 取消旧 %sChannel 任务成功", channel_name.capitalize())
                    except Exception as e:  # noqa: BLE001
                        logger.warning(
                            "[App] 等待旧 %sChannel 任务结束时忽略异常: %s",
                            channel_name.capitalize(),
                            e,
                        )

                asyncio.create_task(wait_cancel(), name=f"wait_{channel_name}_cancel")
            else:
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except asyncio.TimeoutError:
                    logger.warning("[App] 等待 %sChannel 任务取消超时", channel_name.capitalize())
                except asyncio.CancelledError:
                    pass
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        "[App] 等待旧 %sChannel 任务结束时忽略异常: %s",
                        channel_name.capitalize(),
                        e,
                    )

        if channel is not None:
            try:
                await asyncio.wait_for(channel.stop(), timeout=10.0)
            except asyncio.TimeoutError:
                logger.warning("[App] 停止 %sChannel 超时", channel_name.capitalize())
            except Exception as e:  # noqa: BLE001
                logger.warning("[App] 停止旧 %sChannel 失败: %s", channel_name.capitalize(), e)
            channel_manager.unregister_channel(channel.channel_id)

    def _is_channel_enabled(conf: dict | None, required_fields: list[str]) -> tuple[bool, str]:
        if conf is None:
            return False, "未配置或格式错误"
        enabled_raw = conf.get("enabled", None)
        if enabled_raw is None:
            all_fields_present = all(conf.get(f) for f in required_fields)
            return all_fields_present, f"缺少 {','.join(required_fields)}" if not all_fields_present else ""
        return bool(enabled_raw), "enabled = false" if not enabled_raw else ""

    async def _apply_channel_config(conf: dict) -> None:
        nonlocal feishu_channel, feishu_task, xiaoyi_channel, xiaoyi_task
        nonlocal dingtalk_channel, dingtalk_task, telegram_channel, telegram_task
        nonlocal discord_channel, discord_task
        nonlocal whatsapp_channel, whatsapp_task
        nonlocal wecom_channel, wecom_task
        nonlocal wechat_channel, wechat_task
        nonlocal _last_channels_conf
        nonlocal feishu_enterprise_channels, feishu_enterprise_tasks

        changed_channels: list[str] = []
        for channel_name in [
            "feishu",
            "feishu_enterprise",
            "xiaoyi",
            "dingtalk",
            "telegram",
            "whatsapp",
            "discord",
            "wecom",
            "wechat",
        ]:
            if _should_restart_channel(channel_name, _last_channels_conf, conf):
                changed_channels.append(channel_name)
        _last_channels_conf = dict(conf or {})

        if "feishu" in changed_channels:
            feishu_conf = conf.get("feishu") if isinstance(conf, dict) else None
            await _stop_channel(feishu_channel, feishu_task, "feishu")
            feishu_channel, feishu_task = None, None

            if isinstance(feishu_conf, dict):
                enabled, reason = _is_channel_enabled(feishu_conf, ["app_id", "app_secret"])
                if not enabled:
                    logger.info("[App] channels.feishu.%s，FeishuChannel 未启用", reason)
                else:
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=str(feishu_conf.get("app_id") or "").strip(),
                        app_secret=str(feishu_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(feishu_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(feishu_conf.get("verification_token") or "").strip(),
                        allow_from=feishu_conf.get("allow_from") or [],
                        enable_streaming=bool(feishu_conf.get("enable_streaming", True)),
                        chat_id=str(feishu_conf.get("chat_id") or "").strip(),
                        last_chat_id=str(feishu_conf.get("last_chat_id") or "").strip(),
                        last_open_id=str(feishu_conf.get("last_open_id") or "").strip(),
                    )
                    feishu_channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(feishu_channel)
                    feishu_task = asyncio.create_task(feishu_channel.start(), name="feishu")
                    logger.info("[App] 已按 config.yaml.channels.feishu 注册 FeishuChannel")
            else:
                logger.info("[App] channels.feishu 未配置或格式错误，FeishuChannel 不启用")

        if "feishu_enterprise" in changed_channels:
            for bot_key, task in list(feishu_enterprise_tasks.items()):
                await _stop_channel(
                    feishu_enterprise_channels.get(bot_key),
                    task,
                    f"feishu_enterprise[{bot_key}]",
                )
            feishu_enterprise_channels = {}
            feishu_enterprise_tasks = {}

            enterprise_conf = conf.get("feishu_enterprise") if isinstance(conf, dict) else None
            if not isinstance(enterprise_conf, dict):
                logger.info("[App] channels.feishu_enterprise 未配置或格式错误，FeishuEnterpriseChannel 不启用")
            else:
                for bot_key, bot_conf_raw in enterprise_conf.items():
                    if not isinstance(bot_key, str) or not bot_key.strip():
                        continue
                    bot_conf = bot_conf_raw if isinstance(bot_conf_raw, dict) else None
                    if bot_conf is None:
                        logger.info("[App] channels.feishu_enterprise.%s 配置格式错误，跳过", bot_key)
                        continue
                    enabled, reason = _is_channel_enabled(bot_conf, ["app_id", "app_secret"])
                    if not enabled:
                        logger.info(
                            "[App] channels.feishu_enterprise.%s.%s，FeishuEnterpriseChannel 未启用",
                            bot_key,
                            reason,
                        )
                        continue

                    bot_key = bot_key.strip()
                    app_id = str(bot_conf.get("app_id") or "").strip()
                    channel_id = f"feishu_enterprise:{app_id}"
                    feishu_config = FeishuConfig(
                        enabled=True,
                        app_id=app_id,
                        app_secret=str(bot_conf.get("app_secret") or "").strip(),
                        encrypt_key=str(bot_conf.get("encrypt_key") or "").strip(),
                        verification_token=str(bot_conf.get("verification_token") or "").strip(),
                        allow_from=bot_conf.get("allow_from") or [],
                        enable_streaming=bool(bot_conf.get("enable_streaming", True)),
                        chat_id=str(bot_conf.get("chat_id") or "").strip(),
                        channel_id=channel_id,
                        bot_key=bot_key,
                        last_chat_id=str(bot_conf.get("last_chat_id") or "").strip(),
                        last_open_id=str(bot_conf.get("last_open_id") or "").strip(),
                    )
                    channel = FeishuChannel(feishu_config, _DummyBus())
                    channel_manager.register_channel(channel)
                    task = asyncio.create_task(channel.start(), name=f"feishu-enterprise-{bot_key}")
                    feishu_enterprise_channels[bot_key] = channel
                    feishu_enterprise_tasks[bot_key] = task
                    logger.info(
                        "[App] 已按 config.yaml.channels.feishu_enterprise.%s 注册 FeishuChannel(%s)",
                        bot_key,
                        channel_id,
                    )

        if "xiaoyi" in changed_channels:
            xiaoyi_conf = conf.get("xiaoyi") if isinstance(conf, dict) else None
            await _stop_channel(xiaoyi_channel, xiaoyi_task, "xiaoyi")
            xiaoyi_channel, xiaoyi_task = None, None

            if isinstance(xiaoyi_conf, dict):
                enabled, reason = _is_channel_enabled(xiaoyi_conf, ["ak", "sk", "agent_id"])
                if not enabled:
                    logger.info("[App] channels.xiaoyi.%s，XiaoyiChannel 未启用", reason)
                else:
                    if xiaoyi_conf.get("mode") == "xiaoyi_claw":
                        xiaoyi_config = XiaoyiChannelConfig(
                            enabled=True,
                            mode=str(xiaoyi_conf.get("mode") or "xiaoyi_claw").strip(),
                            api_id=str(xiaoyi_conf.get("api_id") or "").strip(),
                            push_id=str(xiaoyi_conf.get("push_id") or "").strip(),
                            push_url=str(xiaoyi_conf.get("push_url") or "").strip(),
                            agent_id=str(xiaoyi_conf.get("agent_id") or "").strip(),
                            uid=str(xiaoyi_conf.get("uid") or "").strip(),
                            api_key=str(xiaoyi_conf.get("api_key") or "").strip(),
                            file_upload_url=str(xiaoyi_conf.get("file_upload_url") or "").strip(),
                            ws_url1=str(xiaoyi_conf.get("ws_url1")).strip(),
                            ws_url2=str(xiaoyi_conf.get("ws_url2")).strip(),
                            enable_streaming=bool(xiaoyi_conf.get("enable_streaming", True)),
                        )
                    else:
                        xiaoyi_config = XiaoyiChannelConfig(
                            enabled=True,
                            mode=str(xiaoyi_conf.get("mode") or "xiaoyi_channel").strip(),
                            ak=str(xiaoyi_conf.get("ak") or "").strip(),
                            sk=str(xiaoyi_conf.get("sk") or "").strip(),
                            api_id=str(xiaoyi_conf.get("api_id") or "").strip(),
                            push_id=str(xiaoyi_conf.get("push_id") or "").strip(),
                            push_url=str(xiaoyi_conf.get("push_url") or "").strip(),
                            agent_id=str(xiaoyi_conf.get("agent_id") or "").strip(),
                            ws_url1=str(xiaoyi_conf.get("ws_url1") or "").strip()
                            or "wss://hag.cloud.huawei.com/openclaw/v1/ws/link",
                            ws_url2=str(xiaoyi_conf.get("ws_url2") or "").strip()
                            or "wss://116.63.174.231/openclaw/v1/ws/link",
                            enable_streaming=bool(xiaoyi_conf.get("enable_streaming", True)),
                        )
                    xiaoyi_channel = XiaoyiChannel(xiaoyi_config, _DummyBus())
                    channel_manager.register_channel(xiaoyi_channel)
                    xiaoyi_task = asyncio.create_task(xiaoyi_channel.start(), name="xiaoyi")
                    logger.info("[App] 已按 config.yaml.channels.xiaoyi 注册 XiaoyiChannel")
            else:
                logger.info("[App] channels.xiaoyi 未配置或格式错误，XiaoyiChannel 不启用")

        if "dingtalk" in changed_channels:
            dingtalk_conf = conf.get("dingtalk") if isinstance(conf, dict) else None
            await _stop_channel(dingtalk_channel, dingtalk_task, "dingtalk", background_wait=True)
            dingtalk_channel, dingtalk_task = None, None

            if isinstance(dingtalk_conf, dict):
                enabled, reason = _is_channel_enabled(dingtalk_conf, ["client_id", "client_secret"])
                if not enabled:
                    logger.info("[App] channels.dingtalk.%s，DingtalkChannel 未启用", reason)
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
                    logger.info("[App] 已按 config.yaml.channels.dingtalk 注册 DingtalkChannel")
            else:
                logger.info("[App] channels.dingtalk 未配置或格式错误，DingtalkChannel 不启用")

        if "telegram" in changed_channels:
            telegram_conf = conf.get("telegram") if isinstance(conf, dict) else None
            await _stop_channel(telegram_channel, telegram_task, "telegram")
            telegram_channel, telegram_task = None, None

            if isinstance(telegram_conf, dict):
                enabled, reason = _is_channel_enabled(telegram_conf, ["bot_token"])
                if not enabled:
                    logger.info("[App] channels.telegram.%s，TelegramChannel 未启用", reason)
                else:
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
                    logger.info("[App] 已按 config.yaml.channels.telegram 注册 TelegramChannel")
            else:
                logger.info("[App] channels.telegram 未配置或格式错误，TelegramChannel 不启用")

        if "discord" in changed_channels:
            discord_conf = conf.get("discord") if isinstance(conf, dict) else None
            await _stop_channel(discord_channel, discord_task, "discord")
            discord_channel, discord_task = None, None

            if isinstance(discord_conf, dict):
                enabled, reason = _is_channel_enabled(discord_conf, ["bot_token"])
                if not enabled:
                    logger.info("[App] channels.discord.%s，DiscordChannel 未启用", reason)
                else:
                    discord_config = DiscordChannelConfig(
                        enabled=True,
                        bot_token=str(discord_conf.get("bot_token") or "").strip(),
                        application_id=str(discord_conf.get("application_id") or "").strip(),
                        guild_id=str(discord_conf.get("guild_id") or "").strip(),
                        channel_id=str(discord_conf.get("channel_id") or "").strip(),
                        allow_from=discord_conf.get("allow_from") or [],
                        block_dm=(str(discord_conf.get("block_dm")).lower() in ["true", "1"]) or False,
                    )
                    discord_channel = DiscordChannel(discord_config, _DummyBus())
                    channel_manager.register_channel(discord_channel)
                    discord_task = asyncio.create_task(discord_channel.start(), name="discord")
                    logger.info("[App] 已按 config.yaml.channels.discord 注册 DiscordChannel")
            else:
                logger.info("[App] channels.discord 未配置或格式错误，DiscordChannel 不启用")

        if "whatsapp" in changed_channels:
            whatsapp_conf = conf.get("whatsapp") if isinstance(conf, dict) else None
            await _stop_channel(whatsapp_channel, whatsapp_task, "whatsapp")
            whatsapp_channel, whatsapp_task = None, None

            if isinstance(whatsapp_conf, dict):
                bridge_ws_url = str(whatsapp_conf.get("bridge_ws_url") or "ws://127.0.0.1:19600/ws").strip()
                default_jid = str(whatsapp_conf.get("default_jid") or "").strip()
                allow_from = whatsapp_conf.get("allow_from") or []
                enable_streaming = bool(whatsapp_conf.get("enable_streaming", True))
                auto_start_bridge = bool(whatsapp_conf.get("auto_start_bridge", False))
                bridge_command = str(
                    whatsapp_conf.get("bridge_command") or "node scripts/whatsapp-bridge.js"
                ).strip()
                bridge_workdir = str(whatsapp_conf.get("bridge_workdir") or "").strip()
                bridge_env_raw = whatsapp_conf.get("bridge_env") or {}
                bridge_env = bridge_env_raw if isinstance(bridge_env_raw, dict) else {}

                enabled_raw = whatsapp_conf.get("enabled", None)
                if enabled_raw is None:
                    enabled = bool(bridge_ws_url)
                else:
                    enabled = bool(enabled_raw)

                if not enabled:
                    logger.info("[App] channels.whatsapp.enabled = false，WhatsAppChannel 未启用")
                elif not bridge_ws_url:
                    logger.info("[App] channels.whatsapp 缺少 bridge_ws_url，WhatsAppChannel 未启用")
                else:
                    whatsapp_config = WhatsAppChannelConfig(
                        enabled=True,
                        enable_streaming=enable_streaming,
                        bridge_ws_url=bridge_ws_url,
                        allow_from=allow_from,
                        default_jid=default_jid,
                        auto_start_bridge=auto_start_bridge,
                        bridge_command=bridge_command,
                        bridge_workdir=bridge_workdir,
                        bridge_env={str(k): str(v) for k, v in bridge_env.items()},
                    )
                    whatsapp_channel = WhatsAppChannel(whatsapp_config, _DummyBus())
                    channel_manager.register_channel(whatsapp_channel)
                    whatsapp_task = asyncio.create_task(whatsapp_channel.start(), name="whatsapp")
                    logger.info("[App] 已按 config.yaml.channels.whatsapp 注册 WhatsAppChannel")
            else:
                logger.info("[App] channels.whatsapp 未配置或格式错误，WhatsAppChannel 不启用")

        if "wecom" in changed_channels:
            wecom_conf = conf.get("wecom") if isinstance(conf, dict) else None
            await _stop_channel(wecom_channel, wecom_task, "wecom")
            wecom_channel, wecom_task = None, None

            if isinstance(wecom_conf, dict):
                enabled, reason = _is_channel_enabled(wecom_conf, ["bot_id", "secret"])
                if not enabled:
                    logger.info("[App] channels.wecom.%s，WecomChannel 未启用", reason)
                else:
                    wecom_config = WecomConfig(
                        enabled=True,
                        bot_id=str(wecom_conf.get("bot_id") or "").strip(),
                        secret=str(wecom_conf.get("secret") or "").strip(),
                        ws_url=str(wecom_conf.get("ws_url") or "wss://openws.work.weixin.qq.com").strip(),
                        allow_from=wecom_conf.get("allow_from") or [],
                        enable_streaming=bool(wecom_conf.get("enable_streaming", True)),
                        send_thinking_message=bool(wecom_conf.get("send_thinking_message", True)),
                    )
                    wecom_channel = WecomChannel(wecom_config, _DummyBus())
                    channel_manager.register_channel(wecom_channel)
                    wecom_task = asyncio.create_task(wecom_channel.start(), name="wecom")
                    logger.info("[App] 已按 config.yaml.channels.wecom 注册 WecomChannel")
            else:
                logger.info("[App] channels.wecom 未配置或格式错误，WecomChannel 不启用")

        if "wechat" in changed_channels:
            wechat_conf = conf.get("wechat") if isinstance(conf, dict) else None
            await _stop_channel(wechat_channel, wechat_task, "wechat")
            wechat_channel, wechat_task = None, None

            if isinstance(wechat_conf, dict):
                enabled, reason = _is_channel_enabled(wechat_conf, [])
                if not enabled:
                    logger.info("[App] channels.wechat.%s，WechatChannel 未启用", reason)
                else:
                    wechat_config = WechatConfig(
                        enabled=True,
                        base_url=str(wechat_conf.get("base_url") or "https://ilinkai.weixin.qq.com").strip(),
                        bot_token=str(wechat_conf.get("bot_token") or "").strip(),
                        ilink_bot_id=str(wechat_conf.get("ilink_bot_id") or "").strip(),
                        ilink_user_id=str(wechat_conf.get("ilink_user_id") or "").strip(),
                        allow_from=wechat_conf.get("allow_from") or [],
                        auto_login=bool(wechat_conf.get("auto_login", True)),
                        qrcode_poll_interval_sec=float(wechat_conf.get("qrcode_poll_interval_sec", 2.0)),
                        long_poll_timeout_sec=int(wechat_conf.get("long_poll_timeout_sec", 45)),
                        backoff_base_sec=float(wechat_conf.get("backoff_base_sec", 1.0)),
                        backoff_max_sec=float(wechat_conf.get("backoff_max_sec", 30.0)),
                        credential_file=str(
                            wechat_conf.get("credential_file") or "~/.wx-ai-bridge/credentials.json"
                        ).strip(),
                        enable_streaming=bool(wechat_conf.get("enable_streaming", True)),
                    )
                    wechat_channel = WechatChannel(wechat_config, _DummyBus())
                    channel_manager.register_channel(wechat_channel)
                    wechat_task = asyncio.create_task(wechat_channel.start(), name="wechat")
                    logger.info("[App] 已按 config.yaml.channels.wechat 注册 WechatChannel")
            else:
                logger.info("[App] channels.wechat 未配置或格式错误，WechatChannel 不启用")

    channel_manager.set_config_callback(_apply_channel_config)
    await channel_manager.set_config(initial_channels_conf)

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

        if feishu_channel is not None and feishu_task is not None:
            feishu_task.cancel()
            try:
                await feishu_task
            except asyncio.CancelledError:
                pass
            await feishu_channel.stop()
        for bot_key, task in list(feishu_enterprise_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            channel = feishu_enterprise_channels.get(bot_key)
            if channel is not None:
                await channel.stop()
        if xiaoyi_channel is not None and xiaoyi_task is not None:
            xiaoyi_task.cancel()
            try:
                await xiaoyi_task
            except asyncio.CancelledError:
                pass
            await xiaoyi_channel.stop()
        if dingtalk_channel is not None and dingtalk_task is not None:
            dingtalk_task.cancel()
            try:
                await dingtalk_task
            except (TypeError, asyncio.CancelledError):
                pass
            await dingtalk_channel.stop()
        if telegram_channel is not None and telegram_task is not None:
            telegram_task.cancel()
            try:
                await telegram_task
            except asyncio.CancelledError:
                pass
            await telegram_channel.stop()
        if discord_channel is not None and discord_task is not None:
            discord_task.cancel()
            try:
                await discord_task
            except asyncio.CancelledError:
                pass
            await discord_channel.stop()
        if whatsapp_channel is not None and whatsapp_task is not None:
            whatsapp_task.cancel()
            try:
                await whatsapp_task
            except asyncio.CancelledError:
                pass
            await whatsapp_channel.stop()
        if wecom_channel is not None and wecom_task is not None:
            wecom_task.cancel()
            try:
                await wecom_task
            except asyncio.CancelledError:
                pass
            await wecom_channel.stop()
        if wechat_channel is not None and wechat_task is not None:
            wechat_task.cancel()
            try:
                await wechat_task
            except asyncio.CancelledError:
                pass
            await wechat_channel.stop()

        await cron_scheduler.stop()
        await channel_manager.stop_dispatch()
        await heartbeat_service.stop()
        await message_handler.stop_forwarding()
        await client.disconnect()
        logger.info("[App] Gateway 已停止")


def main() -> None:
    from jiuwenclaw.telemetry import init_telemetry

    init_telemetry()

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
