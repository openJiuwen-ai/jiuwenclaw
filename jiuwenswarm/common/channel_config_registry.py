"""Shared registry for channels that can be configured from model tools."""

from __future__ import annotations

CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS: tuple[str, ...] = (
    "feishu",
    "feishu_enterprise",
    "xiaoyi",
    "dingtalk",
    "telegram",
    "discord",
    "whatsapp",
    "wecom",
    "wechat",
    "qq",
    "weibo",
)

CONFIGURABLE_THIRD_PARTY_CHANNEL_ID_SET = frozenset(CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS)
CONFIGURABLE_THIRD_PARTY_CHANNEL_ID_TEXT = "、".join(CONFIGURABLE_THIRD_PARTY_CHANNEL_IDS)


def normalize_configurable_channel_id(channel_id: str) -> str:
    """Return the canonical channel id used by the config tool."""
    return str(channel_id or "").strip().lower()


def is_configurable_third_party_channel(channel_id: str) -> bool:
    """Whether the channel can be updated through the model-facing config tool."""
    return normalize_configurable_channel_id(channel_id) in CONFIGURABLE_THIRD_PARTY_CHANNEL_ID_SET
