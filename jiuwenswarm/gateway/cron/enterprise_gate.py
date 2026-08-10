# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""企业级 cron 门控与路由三元组工具。"""

from __future__ import annotations

import logging
import os
from typing import Any

from jiuwenswarm.deployment_mode import MODE_DISTRIBUTED, normalize_deployment_mode

logger = logging.getLogger(__name__)

STICKY_IDENTITY_FIELDS = frozenset(
    {"group_id", "bot_id", "user_id", "jiuwenclaw_id", "job_id", "created_at"}
)


def is_enterprise_edition() -> bool:
    """产品形态：``AGENT_RUNTIME`` 非空。不单独决定企业 cron 开门。"""
    return bool(os.getenv("AGENT_RUNTIME", "").strip())


def get_bound_jiuwenclaw_id() -> str | None:
    """读取当前绑定的实例 id。"""
    try:
        from jiuwenswarm.server.runtime.enterprise_config import gateway_db

        return gateway_db.resolve_jiuwenclaw_id()
    except Exception:
        logger.debug("Failed to read jiuwenclaw_id from gateway_db", exc_info=True)
    env = (
        os.getenv("JIUWENCLAW_ID", "").strip()
        or os.getenv("JIUWENSWARM_ID", "").strip()
    )
    return env or None


def _resolve_deployment_mode() -> str:
    try:
        from jiuwenswarm.common.config import get_config

        mode = (get_config().get("gateway") or {}).get("deployment_mode")
        if mode:
            return normalize_deployment_mode(mode)
    except Exception as exc:
        logger.debug("deployment mode from config unavailable: %s", exc)
    return normalize_deployment_mode(os.getenv("DEPLOYMENT_MODE", "standalone"))


def enterprise_cron_enabled(*, deployment_mode: str | None = None) -> bool:
    """企业 cron 真正开门：实例 id 已绑定，且非 distributed。"""
    if not get_bound_jiuwenclaw_id():
        return False
    mode = deployment_mode if deployment_mode is not None else _resolve_deployment_mode()
    return normalize_deployment_mode(mode) != MODE_DISTRIBUTED


def coerce_routing_id(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        value = value[0] if value else None
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def extract_routing_triple(*sources: Any) -> tuple[str | None, str | None, str | None]:
    """从若干 dict / 对象按优先级解析 group_id / bot_id / user_id。"""
    group_id: str | None = None
    bot_id: str | None = None
    user_id: str | None = None

    def _merge_from_mapping(mapping: dict[str, Any]) -> None:
        nonlocal group_id, bot_id, user_id
        if group_id is None:
            group_id = coerce_routing_id(mapping.get("group_id"))
        if bot_id is None:
            bot_id = coerce_routing_id(mapping.get("bot_id"))
        if user_id is None:
            user_id = coerce_routing_id(mapping.get("user_id"))

    for source in sources:
        if source is None:
            continue
        if isinstance(source, dict):
            _merge_from_mapping(source)
            continue
        params = getattr(source, "params", None)
        if isinstance(params, dict):
            _merge_from_mapping(params)
        metadata = getattr(source, "metadata", None)
        if isinstance(metadata, dict):
            _merge_from_mapping(metadata)
            query = metadata.get("query")
            if isinstance(query, dict):
                _merge_from_mapping(query)
        chat_id = coerce_routing_id(getattr(source, "chat_id", None))
        if group_id is None and chat_id:
            group_id = chat_id

    return group_id, bot_id, user_id


def routing_triple_complete(
    group_id: str | None,
    bot_id: str | None,
    user_id: str | None,
) -> bool:
    return bool(group_id and bot_id and user_id)


def strip_sticky_identity_fields(patch: dict[str, Any]) -> dict[str, Any]:
    """从 update patch 中剥离禁止改动的身份字段。"""
    out = dict(patch or {})
    for key in STICKY_IDENTITY_FIELDS:
        out.pop(key, None)
    return out


def job_matches_routing(
    job: Any,
    *,
    group_id: str | None,
    bot_id: str | None,
    user_id: str | None,
    match: str = "and",
    include_unbound: bool = False,
) -> bool:
    """按维过滤单条任务。"""
    jg = coerce_routing_id(
        getattr(job, "group_id", None) if not isinstance(job, dict) else job.get("group_id")
    )
    jb = coerce_routing_id(
        getattr(job, "bot_id", None) if not isinstance(job, dict) else job.get("bot_id")
    )
    ju = coerce_routing_id(
        getattr(job, "user_id", None) if not isinstance(job, dict) else job.get("user_id")
    )

    if not (jg or jb or ju):
        return bool(include_unbound)

    wanted = [
        (group_id, jg),
        (bot_id, jb),
        (user_id, ju),
    ]
    active = [(w, j) for w, j in wanted if w]
    if not active:
        return True

    if str(match or "and").strip().lower() == "or":
        return any(w == j for w, j in active)
    return all(w == j for w, j in active)


__all__ = (
    "STICKY_IDENTITY_FIELDS",
    "coerce_routing_id",
    "enterprise_cron_enabled",
    "extract_routing_triple",
    "get_bound_jiuwenclaw_id",
    "is_enterprise_edition",
    "job_matches_routing",
    "routing_triple_complete",
    "strip_sticky_identity_fields",
)
