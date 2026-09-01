# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""企业版默认 service_id / agent_id / workspace_key 拼接（与旧 RuntimeManagement 路由策略一致）。"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Any

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


def _default_workspace_key(group_id: str, bot_id: str, user_id: str) -> str:
    """未配置 workspace_key 时默认 ``{group}{bot}{user}``（不做 bot_id hash 分桶）。"""
    return f"{group_id}{bot_id}{user_id}".strip()


def _md5_invoke_id(value: str) -> str:
    """逻辑 ID → MD5 32 位 hex。"""
    text = str(value or "").strip()
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def coalesce_invoke_ids(
    *,
    group_id: str = "",
    bot_id: str = "",
    user_id: str = "",
    service_id: str | None = None,
    agent_id: str | None = None,
    workspace_key: str | None = None,
) -> tuple[str, str, str]:
    """已有非空 id 原样保留；缺失时用路由三元组拼默认逻辑字符串（尚未 MD5）。

    ``agent_id`` 只认信封顶层，不再从 ``agent_ref`` 推导，故此处只需空串判断。
    """
    svc = str(service_id or "").strip()
    ag = str(agent_id or "").strip()
    wk = str(workspace_key or "").strip()
    g = str(group_id or "").strip()
    b = str(bot_id or "").strip()
    u = str(user_id or "").strip()

    if g and b and u:
        if not svc or not ag:
            default_svc, default_ag = _default_invoke_ids(g, b, u)
            if not svc:
                svc = default_svc
            if not ag:
                ag = default_ag
        if not wk:
            wk = _default_workspace_key(g, b, u)

    return (
        svc or "default_service_id",
        ag or "default_agent_id",
        wk or "default_workspace_key",
    )


def routing_triple_from_envelope(envelope: Any) -> tuple[str, str, str]:
    """只从权威源取 ``(group_id, bot_id, user_id)``。

    权威：``channel_context`` 上的顶层 ``user_id`` + ``routing``（见
    :func:`web_routing_identity`）；``envelope.user_id`` 为同字段协议镜像。
    """
    from jiuwenswarm.common.request_identity import web_routing_identity

    ctx = (
        envelope.channel_context
        if isinstance(getattr(envelope, "channel_context", None), dict)
        else None
    )
    identity = web_routing_identity(ctx)
    group_id = str(identity.get("group_id") or "").strip()
    bot_id = str(identity.get("bot_id") or "").strip()
    user_id = str(
        identity.get("user_id") or getattr(envelope, "user_id", None) or ""
    ).strip()
    return group_id, bot_id, user_id


def apply_invoke_ids_to_envelope(envelope: Any) -> Any:
    """补齐并 MD5 化信封顶层 ``service_id`` / ``agent_id`` / ``workspace_key``。

    非空顶层字段优先；空串视为未配置，改用权威 routing 三元组拼默认值。
    """
    group_id, bot_id, user_id = routing_triple_from_envelope(envelope)

    service_id, agent_id, workspace_key = coalesce_invoke_ids(
        group_id=group_id,
        bot_id=bot_id,
        user_id=user_id,
        service_id=getattr(envelope, "service_id", None),
        agent_id=getattr(envelope, "agent_id", None),
        workspace_key=getattr(envelope, "workspace_key", None),
    )
    hashed_svc = _md5_invoke_id(service_id)
    hashed_ag = _md5_invoke_id(agent_id)
    hashed_wk = _md5_invoke_id(workspace_key)

    logger.info(
        "[invoke_ids] envelope tenant ids: "
        "group=%s bot=%s user=%s logical=(%s,%s,%s) hashed=(%s,%s,%s)",
        group_id,
        bot_id,
        user_id,
        service_id,
        agent_id,
        workspace_key,
        hashed_svc,
        hashed_ag,
        hashed_wk,
    )

    envelope.service_id = hashed_svc
    envelope.agent_id = hashed_ag
    envelope.workspace_key = hashed_wk
    return envelope
