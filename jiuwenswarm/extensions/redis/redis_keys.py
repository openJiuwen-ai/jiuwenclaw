# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Gateway Redis Key / 频道命名（gateway-reliability-enhancement-design §3.3.7）.

相对路径片段会与 ``RedisConfig.key_prefix`` 拼接为完整 Redis key 或 Pub/Sub 频道名。
"""

from __future__ import annotations


def _normalize_prefix(key_prefix: str) -> str:
    p = (key_prefix or "").strip()
    if not p:
        return ""
    return p if p.endswith(":") else f"{p}:"


def session_map_hash_key(key_prefix: str, identity_key: str) -> str:
    """``{key_prefix}gateway:session_map:{identity_key}`` Hash。"""
    return f"{_normalize_prefix(key_prefix)}gateway:session_map:{identity_key}"


def cron_jobs_hash_key(key_prefix: str) -> str:
    """``{key_prefix}gateway:cron_jobs`` Hash。"""
    return f"{_normalize_prefix(key_prefix)}gateway:cron_jobs"


def leader_lock_key(key_prefix: str, channel_id: str) -> str:
    """``{key_prefix}gateway:leader:{channel_id}`` String（带 TTL）。"""
    return f"{_normalize_prefix(key_prefix)}gateway:leader:{channel_id}"


def cron_changes_pubsub_channel(key_prefix: str) -> str:
    """``{key_prefix}gateway:cron_changes`` Pub/Sub 频道（方案 A：全局订阅）。"""
    return f"{_normalize_prefix(key_prefix)}gateway:cron_changes"
