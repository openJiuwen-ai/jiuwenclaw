# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""企业版默认 service_id / agent_id 拼接（与旧 RuntimeManagement 路由策略一致）。"""

from __future__ import annotations

import hashlib
import logging
import os

logger = logging.getLogger(__name__)


def _agent_bot_id_group_num() -> int:
    """AGENT_BOT_ID_GROUP_NUM：>0 时对 bot_id 做稳定 hash 分桶后再拼默认 service_id/agent_id。"""
    raw = os.getenv("AGENT_BOT_ID_GROUP_NUM", "0").strip()
    try:
        n = int(raw)
    except ValueError:
        logger.warning(
            "[invoke_ids] invalid AGENT_BOT_ID_GROUP_NUM=%r, fallback to 0",
            raw,
        )
        return 0
    return n if n > 0 else 0


def _routing_bot_id(bot_id: str, group_num: int | None = None) -> str:
    """Hash bot_id with SHA-256 and bucket; returns ``b{0..N-1}`` or raw bot_id when N<=0."""
    n = _agent_bot_id_group_num() if group_num is None else group_num
    if n <= 0:
        return bot_id
    digest = hashlib.sha256(bot_id.encode("utf-8")).hexdigest()
    bucket = int(digest, 16) % n
    return f"b{bucket}"


def _default_invoke_ids(group_id: str, bot_id: str, user_id: str) -> tuple[str, str]:
    """企业策略未配置 service_id/agent_id 时的默认拼接（bot_id 可按 env 分桶）。"""
    routed_bot = _routing_bot_id(bot_id)
    default_svc = f"{group_id}{routed_bot}"
    default_ag = f"{group_id}{routed_bot}{user_id}"
    logger.info(
        "user_id=%s, group_id=%s, bot_id=%s, routed_bot=%s, default_svc=%s, default_ag=%s",
        user_id,
        group_id,
        bot_id,
        routed_bot,
        default_svc,
        default_ag,
    )
    return default_svc, default_ag
