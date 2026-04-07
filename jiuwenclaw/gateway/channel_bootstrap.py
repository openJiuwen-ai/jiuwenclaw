# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Channel Bootstrap - 频道启动管理器."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jiuwenclaw.gateway.channel_manager import ChannelManager

logger = logging.getLogger(__name__)


@dataclass
class ChannelInstances:
    """频道实例容器"""

    feishu_channel: Any = None
    feishu_task: asyncio.Task | None = None
    feishu_enterprise_channels: dict[str, Any] = field(default_factory=dict)
    feishu_enterprise_tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    xiaoyi_channel: Any = None
    xiaoyi_task: asyncio.Task | None = None
    dingtalk_channel: Any = None
    dingtalk_task: asyncio.Task | None = None
    telegram_channel: Any = None
    telegram_task: asyncio.Task | None = None
    discord_channel: Any = None
    discord_task: asyncio.Task | None = None
    whatsapp_channel: Any = None
    whatsapp_task: asyncio.Task | None = None
    wecom_channel: Any = None
    wecom_task: asyncio.Task | None = None
    wechat_channel: Any = None
    wechat_task: asyncio.Task | None = None


def should_restart_channel(channel_name: str, old_conf: dict, new_conf: dict) -> bool:
    """判断是否需要重启频道.

    Args:
        channel_name: 频道名称
        old_conf: 旧配置
        new_conf: 新配置

    Returns:
        是否需要重启
    """
    old_channel_conf = (
        old_conf.get(channel_name) if isinstance(old_conf, dict) else None
    )
    new_channel_conf = (
        new_conf.get(channel_name) if isinstance(new_conf, dict) else None
    )

    if (old_channel_conf is None) != (new_channel_conf is None):
        return True
    if old_channel_conf is None:
        return False

    return old_channel_conf != new_channel_conf


def is_channel_enabled(
    conf: dict | None, required_fields: list[str]
) -> tuple[bool, str]:
    """检查频道是否启用.

    Args:
        conf: 频道配置
        required_fields: 必需字段列表

    Returns:
        (是否启用, 未启用原因)
    """
    if conf is None:
        return False, "未配置或格式错误"

    enabled_raw = conf.get("enabled", None)
    if enabled_raw is None:
        all_fields_present = all(conf.get(f) for f in required_fields)
        return (
            all_fields_present,
            f"缺少 {','.join(required_fields)}" if not all_fields_present else "",
        )

    return bool(enabled_raw), "enabled = false" if not enabled_raw else ""


class ChannelBootstrap:
    """频道启动管理器.

    负责：
    1. 根据配置启动/停止各个频道
    2. 管理频道实例的生命周期
    3. 清理所有频道实例
    """

    def __init__(self, channel_manager: "ChannelManager", bus: Any) -> None:
        """初始化频道启动管理器.

        Args:
            channel_manager: 频道管理器
            bus: 消息总线（通常是 _DummyBus）
        """
        self._channel_manager = channel_manager
        self._bus = bus
        self._instances = ChannelInstances()
        self._last_channels_conf: dict = {}

    async def _stop_channel(
        self,
        channel: Any,
        task: asyncio.Task | None,
        channel_name: str,
        background_wait: bool = False,
    ) -> None:
        """停止单个频道.

        Args:
            channel: 频道实例
            task: 频道任务
            channel_name: 频道名称
            background_wait: 是否后台等待
        """
        if task is not None:
            task.cancel()
            if background_wait:

                async def wait_cancel():
                    try:
                        await task
                    except (TypeError, asyncio.CancelledError):
                        logger.info(
                            "[App] 取消旧 %sChannel 任务成功", channel_name.capitalize()
                        )
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
                    logger.warning(
                        "[App] 等待 %sChannel 任务取消超时", channel_name.capitalize()
                    )
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
                logger.warning(
                    "[App] 停止旧 %sChannel 失败: %s", channel_name.capitalize(), e
                )
            self._channel_manager.unregister_channel(channel.channel_id)

    async def apply_config(self, conf: dict) -> None:
        """应用频道配置.

        Args:
            conf: 频道配置字典
        """

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
            if should_restart_channel(channel_name, self._last_channels_conf, conf):
                changed_channels.append(channel_name)
        self._last_channels_conf = dict(conf or {})

        if "feishu" in changed_channels:
            await self._apply_feishu_config(conf)

        if "feishu_enterprise" in changed_channels:
            await self._apply_feishu_enterprise_config(conf)

        if "xiaoyi" in changed_channels:
            await self._apply_xiaoyi_config(conf)

        if "dingtalk" in changed_channels:
            await self._apply_dingtalk_config(conf)

        if "telegram" in changed_channels:
            await self._apply_telegram_config(conf)

        if "discord" in changed_channels:
            await self._apply_discord_config(conf)

        if "whatsapp" in changed_channels:
            await self._apply_whatsapp_config(conf)

        if "wecom" in changed_channels:
            await self._apply_wecom_config(conf)

        if "wechat" in changed_channels:
            await self._apply_wechat_config(conf)

    async def _apply_feishu_config(self, conf: dict) -> None:
        """应用飞书频道配置."""
        from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig

        feishu_conf = conf.get("feishu") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.feishu_channel, self._instances.feishu_task, "feishu"
        )
        self._instances.feishu_channel = None
        self._instances.feishu_task = None

        if isinstance(feishu_conf, dict):
            enabled, reason = is_channel_enabled(feishu_conf, ["app_id", "app_secret"])
            if not enabled:
                logger.info("[App] channels.feishu.%s，FeishuChannel 未启用", reason)
            else:
                feishu_config = FeishuConfig(
                    enabled=True,
                    app_id=str(feishu_conf.get("app_id") or "").strip(),
                    app_secret=str(feishu_conf.get("app_secret") or "").strip(),
                    encrypt_key=str(feishu_conf.get("encrypt_key") or "").strip(),
                    verification_token=str(
                        feishu_conf.get("verification_token") or ""
                    ).strip(),
                    allow_from=feishu_conf.get("allow_from") or [],
                    enable_streaming=bool(feishu_conf.get("enable_streaming", True)),
                    chat_id=str(feishu_conf.get("chat_id") or "").strip(),
                    last_chat_id=str(feishu_conf.get("last_chat_id") or "").strip(),
                    last_open_id=str(feishu_conf.get("last_open_id") or "").strip(),
                )
                feishu_channel = FeishuChannel(feishu_config, self._bus)
                self._channel_manager.register_channel(feishu_channel)
                feishu_task = asyncio.create_task(feishu_channel.start(), name="feishu")
                self._instances.feishu_channel = feishu_channel
                self._instances.feishu_task = feishu_task
                logger.info("[App] 已按 config.yaml.channels.feishu 注册 FeishuChannel")
        else:
            logger.info("[App] channels.feishu 未配置或格式错误，FeishuChannel 不启用")

    async def _apply_feishu_enterprise_config(self, conf: dict) -> None:
        """应用飞书企业频道配置."""
        from jiuwenclaw.channel.feishu import FeishuChannel, FeishuConfig

        for bot_key, task in list(self._instances.feishu_enterprise_tasks.items()):
            await self._stop_channel(
                self._instances.feishu_enterprise_channels.get(bot_key),
                task,
                f"feishu_enterprise[{bot_key}]",
            )
        self._instances.feishu_enterprise_channels = {}
        self._instances.feishu_enterprise_tasks = {}

        enterprise_conf = (
            conf.get("feishu_enterprise") if isinstance(conf, dict) else None
        )
        if not isinstance(enterprise_conf, dict):
            logger.info(
                "[App] channels.feishu_enterprise 未配置或格式错误，FeishuEnterpriseChannel 不启用"
            )
        else:
            for bot_key, bot_conf_raw in enterprise_conf.items():
                if not isinstance(bot_key, str) or not bot_key.strip():
                    continue
                bot_conf = bot_conf_raw if isinstance(bot_conf_raw, dict) else None
                if bot_conf is None:
                    logger.info(
                        "[App] channels.feishu_enterprise.%s 配置格式错误，跳过",
                        bot_key,
                    )
                    continue
                enabled, reason = is_channel_enabled(bot_conf, ["app_id", "app_secret"])
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
                    verification_token=str(
                        bot_conf.get("verification_token") or ""
                    ).strip(),
                    allow_from=bot_conf.get("allow_from") or [],
                    enable_streaming=bool(bot_conf.get("enable_streaming", True)),
                    chat_id=str(bot_conf.get("chat_id") or "").strip(),
                    channel_id=channel_id,
                    bot_key=bot_key,
                    last_chat_id=str(bot_conf.get("last_chat_id") or "").strip(),
                    last_open_id=str(bot_conf.get("last_open_id") or "").strip(),
                )
                channel = FeishuChannel(feishu_config, self._bus)
                self._channel_manager.register_channel(channel)
                task = asyncio.create_task(
                    channel.start(), name=f"feishu-enterprise-{bot_key}"
                )
                self._instances.feishu_enterprise_channels[bot_key] = channel
                self._instances.feishu_enterprise_tasks[bot_key] = task
                logger.info(
                    "[App] 已按 config.yaml.channels.feishu_enterprise.%s 注册 FeishuChannel(%s)",
                    bot_key,
                    channel_id,
                )

    async def _apply_xiaoyi_config(self, conf: dict) -> None:
        """应用小艺频道配置."""
        from jiuwenclaw.channel.xiaoyi_channel import XiaoyiChannel, XiaoyiChannelConfig

        xiaoyi_conf = conf.get("xiaoyi") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.xiaoyi_channel, self._instances.xiaoyi_task, "xiaoyi"
        )
        self._instances.xiaoyi_channel = None
        self._instances.xiaoyi_task = None

        if isinstance(xiaoyi_conf, dict):
            enabled, reason = is_channel_enabled(xiaoyi_conf, ["ak", "sk", "agent_id"])
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
                        file_upload_url=str(
                            xiaoyi_conf.get("file_upload_url") or ""
                        ).strip(),
                        ws_url1=str(xiaoyi_conf.get("ws_url1")).strip(),
                        ws_url2=str(xiaoyi_conf.get("ws_url2")).strip(),
                        enable_streaming=bool(
                            xiaoyi_conf.get("enable_streaming", True)
                        ),
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
                        enable_streaming=bool(
                            xiaoyi_conf.get("enable_streaming", True)
                        ),
                    )
                xiaoyi_channel = XiaoyiChannel(xiaoyi_config, self._bus)
                self._channel_manager.register_channel(xiaoyi_channel)
                xiaoyi_task = asyncio.create_task(xiaoyi_channel.start(), name="xiaoyi")
                self._instances.xiaoyi_channel = xiaoyi_channel
                self._instances.xiaoyi_task = xiaoyi_task
                logger.info("[App] 已按 config.yaml.channels.xiaoyi 注册 XiaoyiChannel")
        else:
            logger.info("[App] channels.xiaoyi 未配置或格式错误，XiaoyiChannel 不启用")

    async def _apply_dingtalk_config(self, conf: dict) -> None:
        """应用钉钉频道配置."""
        from jiuwenclaw.channel import DingTalkChannel, DingTalkConfig

        dingtalk_conf = conf.get("dingtalk") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.dingtalk_channel,
            self._instances.dingtalk_task,
            "dingtalk",
            background_wait=True,
        )
        self._instances.dingtalk_channel = None
        self._instances.dingtalk_task = None

        if isinstance(dingtalk_conf, dict):
            enabled, reason = is_channel_enabled(
                dingtalk_conf, ["client_id", "client_secret"]
            )
            if not enabled:
                logger.info(
                    "[App] channels.dingtalk.%s，DingtalkChannel 未启用", reason
                )
            else:
                dingtalk_config = DingTalkConfig(
                    enabled=True,
                    client_id=str(dingtalk_conf.get("client_id") or "").strip(),
                    client_secret=str(dingtalk_conf.get("client_secret") or "").strip(),
                    allow_from=dingtalk_conf.get("allow_from") or [],
                )
                dingtalk_channel = DingTalkChannel(dingtalk_config, self._bus)
                self._channel_manager.register_channel(dingtalk_channel)
                dingtalk_task = asyncio.create_task(
                    dingtalk_channel.start(), name="dingtalk"
                )
                self._instances.dingtalk_channel = dingtalk_channel
                self._instances.dingtalk_task = dingtalk_task
                logger.info(
                    "[App] 已按 config.yaml.channels.dingtalk 注册 DingtalkChannel"
                )
        else:
            logger.info(
                "[App] channels.dingtalk 未配置或格式错误，DingtalkChannel 不启用"
            )

    async def _apply_telegram_config(self, conf: dict) -> None:
        """应用 Telegram 频道配置."""
        from jiuwenclaw.channel.telegram_channel import (
            TelegramChannel,
            TelegramChannelConfig,
        )

        telegram_conf = conf.get("telegram") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.telegram_channel, self._instances.telegram_task, "telegram"
        )
        self._instances.telegram_channel = None
        self._instances.telegram_task = None

        if isinstance(telegram_conf, dict):
            enabled, reason = is_channel_enabled(telegram_conf, ["bot_token"])
            if not enabled:
                logger.info(
                    "[App] channels.telegram.%s，TelegramChannel 未启用", reason
                )
            else:
                telegram_config = TelegramChannelConfig(
                    enabled=True,
                    bot_token=str(telegram_conf.get("bot_token") or "").strip(),
                    allow_from=telegram_conf.get("allow_from") or [],
                    parse_mode=str(
                        telegram_conf.get("parse_mode") or "Markdown"
                    ).strip(),
                    group_chat_mode=str(
                        telegram_conf.get("group_chat_mode") or "mention"
                    ).strip(),
                )
                telegram_channel = TelegramChannel(telegram_config, self._bus)
                self._channel_manager.register_channel(telegram_channel)
                telegram_task = asyncio.create_task(
                    telegram_channel.start(), name="telegram"
                )
                self._instances.telegram_channel = telegram_channel
                self._instances.telegram_task = telegram_task
                logger.info(
                    "[App] 已按 config.yaml.channels.telegram 注册 TelegramChannel"
                )
        else:
            logger.info(
                "[App] channels.telegram 未配置或格式错误，TelegramChannel 不启用"
            )

    async def _apply_discord_config(self, conf: dict) -> None:
        """应用 Discord 频道配置."""
        from jiuwenclaw.channel.discord_channel import (
            DiscordChannel,
            DiscordChannelConfig,
        )

        discord_conf = conf.get("discord") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.discord_channel, self._instances.discord_task, "discord"
        )
        self._instances.discord_channel = None
        self._instances.discord_task = None

        if isinstance(discord_conf, dict):
            enabled, reason = is_channel_enabled(discord_conf, ["bot_token"])
            if not enabled:
                logger.info("[App] channels.discord.%s，DiscordChannel 未启用", reason)
            else:
                discord_config = DiscordChannelConfig(
                    enabled=True,
                    bot_token=str(discord_conf.get("bot_token") or "").strip(),
                    application_id=str(
                        discord_conf.get("application_id") or ""
                    ).strip(),
                    guild_id=str(discord_conf.get("guild_id") or "").strip(),
                    channel_id=str(discord_conf.get("channel_id") or "").strip(),
                    allow_from=discord_conf.get("allow_from") or [],
                    block_dm=(
                        str(discord_conf.get("block_dm")).lower() in ["true", "1"]
                    )
                    or False,
                )
                discord_channel = DiscordChannel(discord_config, self._bus)
                self._channel_manager.register_channel(discord_channel)
                discord_task = asyncio.create_task(
                    discord_channel.start(), name="discord"
                )
                self._instances.discord_channel = discord_channel
                self._instances.discord_task = discord_task
                logger.info(
                    "[App] 已按 config.yaml.channels.discord 注册 DiscordChannel"
                )
        else:
            logger.info(
                "[App] channels.discord 未配置或格式错误，DiscordChannel 不启用"
            )

    async def _apply_whatsapp_config(self, conf: dict) -> None:
        """应用 WhatsApp 频道配置."""
        from jiuwenclaw.channel.whatsapp_channel import (
            WhatsAppChannel,
            WhatsAppChannelConfig,
        )

        whatsapp_conf = conf.get("whatsapp") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.whatsapp_channel, self._instances.whatsapp_task, "whatsapp"
        )
        self._instances.whatsapp_channel = None
        self._instances.whatsapp_task = None

        if isinstance(whatsapp_conf, dict):
            bridge_ws_url = str(
                whatsapp_conf.get("bridge_ws_url") or "ws://127.0.0.1:19600/ws"
            ).strip()
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
                logger.info(
                    "[App] channels.whatsapp.enabled = false，WhatsAppChannel 未启用"
                )
            elif not bridge_ws_url:
                logger.info(
                    "[App] channels.whatsapp 缺少 bridge_ws_url，WhatsAppChannel 未启用"
                )
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
                whatsapp_channel = WhatsAppChannel(whatsapp_config, self._bus)
                self._channel_manager.register_channel(whatsapp_channel)
                whatsapp_task = asyncio.create_task(
                    whatsapp_channel.start(), name="whatsapp"
                )
                self._instances.whatsapp_channel = whatsapp_channel
                self._instances.whatsapp_task = whatsapp_task
                logger.info(
                    "[App] 已按 config.yaml.channels.whatsapp 注册 WhatsAppChannel"
                )
        else:
            logger.info(
                "[App] channels.whatsapp 未配置或格式错误，WhatsAppChannel 不启用"
            )

    async def _apply_wecom_config(self, conf: dict) -> None:
        """应用企业微信频道配置."""
        from jiuwenclaw.channel.wecom_channel import WecomChannel, WecomConfig

        wecom_conf = conf.get("wecom") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.wecom_channel, self._instances.wecom_task, "wecom"
        )
        self._instances.wecom_channel = None
        self._instances.wecom_task = None

        if isinstance(wecom_conf, dict):
            enabled, reason = is_channel_enabled(wecom_conf, ["bot_id", "secret"])
            if not enabled:
                logger.info("[App] channels.wecom.%s，WecomChannel 未启用", reason)
            else:
                wecom_config = WecomConfig(
                    enabled=True,
                    bot_id=str(wecom_conf.get("bot_id") or "").strip(),
                    secret=str(wecom_conf.get("secret") or "").strip(),
                    ws_url=str(
                        wecom_conf.get("ws_url") or "wss://openws.work.weixin.qq.com"
                    ).strip(),
                    allow_from=wecom_conf.get("allow_from") or [],
                    enable_streaming=bool(wecom_conf.get("enable_streaming", True)),
                    send_thinking_message=bool(
                        wecom_conf.get("send_thinking_message", True)
                    ),
                )
                wecom_channel = WecomChannel(wecom_config, self._bus)
                self._channel_manager.register_channel(wecom_channel)
                wecom_task = asyncio.create_task(wecom_channel.start(), name="wecom")
                self._instances.wecom_channel = wecom_channel
                self._instances.wecom_task = wecom_task
                logger.info("[App] 已按 config.yaml.channels.wecom 注册 WecomChannel")
        else:
            logger.info("[App] channels.wecom 未配置或格式错误，WecomChannel 不启用")

    async def _apply_wechat_config(self, conf: dict) -> None:
        """应用微信频道配置."""
        from jiuwenclaw.channel.wechat_channel import WechatChannel, WechatConfig

        wechat_conf = conf.get("wechat") if isinstance(conf, dict) else None
        await self._stop_channel(
            self._instances.wechat_channel, self._instances.wechat_task, "wechat"
        )
        self._instances.wechat_channel = None
        self._instances.wechat_task = None

        if isinstance(wechat_conf, dict):
            enabled, reason = is_channel_enabled(wechat_conf, [])
            if not enabled:
                logger.info("[App] channels.wechat.%s，WechatChannel 未启用", reason)
            else:
                wechat_config = WechatConfig(
                    enabled=True,
                    base_url=str(
                        wechat_conf.get("base_url") or "https://ilinkai.weixin.qq.com"
                    ).strip(),
                    bot_token=str(wechat_conf.get("bot_token") or "").strip(),
                    ilink_bot_id=str(wechat_conf.get("ilink_bot_id") or "").strip(),
                    ilink_user_id=str(wechat_conf.get("ilink_user_id") or "").strip(),
                    allow_from=wechat_conf.get("allow_from") or [],
                    auto_login=bool(wechat_conf.get("auto_login", True)),
                    qrcode_poll_interval_sec=float(
                        wechat_conf.get("qrcode_poll_interval_sec", 2.0)
                    ),
                    long_poll_timeout_sec=int(
                        wechat_conf.get("long_poll_timeout_sec", 45)
                    ),
                    backoff_base_sec=float(wechat_conf.get("backoff_base_sec", 1.0)),
                    backoff_max_sec=float(wechat_conf.get("backoff_max_sec", 30.0)),
                    credential_file=str(
                        wechat_conf.get("credential_file")
                        or "~/.wx-ai-bridge/credentials.json"
                    ).strip(),
                    enable_streaming=bool(wechat_conf.get("enable_streaming", True)),
                )
                wechat_channel = WechatChannel(wechat_config, self._bus)
                self._channel_manager.register_channel(wechat_channel)
                wechat_task = asyncio.create_task(wechat_channel.start(), name="wechat")
                self._instances.wechat_channel = wechat_channel
                self._instances.wechat_task = wechat_task
                logger.info("[App] 已按 config.yaml.channels.wechat 注册 WechatChannel")
        else:
            logger.info("[App] channels.wechat 未配置或格式错误，WechatChannel 不启用")

    async def cleanup_all(self) -> None:
        """清理所有频道实例."""
        # 清理飞书
        if (
            self._instances.feishu_channel is not None
            and self._instances.feishu_task is not None
        ):
            self._instances.feishu_task.cancel()
            try:
                await self._instances.feishu_task
            except asyncio.CancelledError:
                pass
            await self._instances.feishu_channel.stop()

        # 清理飞书企业频道
        for bot_key, task in list(self._instances.feishu_enterprise_tasks.items()):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            channel = self._instances.feishu_enterprise_channels.get(bot_key)
            if channel is not None:
                await channel.stop()

        # 清理小艺
        if (
            self._instances.xiaoyi_channel is not None
            and self._instances.xiaoyi_task is not None
        ):
            self._instances.xiaoyi_task.cancel()
            try:
                await self._instances.xiaoyi_task
            except asyncio.CancelledError:
                pass
            await self._instances.xiaoyi_channel.stop()

        # 清理钉钉
        if (
            self._instances.dingtalk_channel is not None
            and self._instances.dingtalk_task is not None
        ):
            self._instances.dingtalk_task.cancel()
            try:
                await self._instances.dingtalk_task
            except (TypeError, asyncio.CancelledError):
                pass
            await self._instances.dingtalk_channel.stop()

        # 清理 Telegram
        if (
            self._instances.telegram_channel is not None
            and self._instances.telegram_task is not None
        ):
            self._instances.telegram_task.cancel()
            try:
                await self._instances.telegram_task
            except asyncio.CancelledError:
                pass
            await self._instances.telegram_channel.stop()

        # 清理 Discord
        if (
            self._instances.discord_channel is not None
            and self._instances.discord_task is not None
        ):
            self._instances.discord_task.cancel()
            try:
                await self._instances.discord_task
            except asyncio.CancelledError:
                pass
            await self._instances.discord_channel.stop()

        # 清理 WhatsApp
        if (
            self._instances.whatsapp_channel is not None
            and self._instances.whatsapp_task is not None
        ):
            self._instances.whatsapp_task.cancel()
            try:
                await self._instances.whatsapp_task
            except asyncio.CancelledError:
                pass
            await self._instances.whatsapp_channel.stop()

        # 清理企业微信
        if (
            self._instances.wecom_channel is not None
            and self._instances.wecom_task is not None
        ):
            self._instances.wecom_task.cancel()
            try:
                await self._instances.wecom_task
            except asyncio.CancelledError:
                pass
            await self._instances.wecom_channel.stop()

        # 清理微信
        if (
            self._instances.wechat_channel is not None
            and self._instances.wechat_task is not None
        ):
            self._instances.wechat_task.cancel()
            try:
                await self._instances.wechat_task
            except asyncio.CancelledError:
                pass
            await self._instances.wechat_channel.stop()
