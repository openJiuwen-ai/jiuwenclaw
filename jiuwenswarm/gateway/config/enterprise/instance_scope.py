# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业 Gateway DB 实例隔离：catalog 推导 scoped 表 + 统一 instance id 解析。

Gateway 写（``EnterpriseRecordRepository``）与 AgentServer 读（``gateway_db``）
共用本模块，避免各维护一份 ``_INSTANCE_SCOPED_TABLES``。
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from jiuwenswarm.gateway.config.enterprise.catalog import ENTERPRISE_RECORD_SPECS


@lru_cache(maxsize=1)
def instance_scoped_store_names() -> frozenset[str]:
    """需自动附加 ``jiuwenclaw_id`` 的 store / 表名（与写侧 catalog 对齐）。"""
    return frozenset(
        name
        for name, spec in ENTERPRISE_RECORD_SPECS.items()
        if spec.scope_field == "jiuwenclaw_id"
    )


def table_requires_instance_scope(table: str) -> bool:
    return table in instance_scoped_store_names()


def list_records_requires_bound_instance(table: str, instance_id: str | None) -> bool:
    """scoped 表在未绑定实例 id 时不应查询（fail-closed）。"""
    return table_requires_instance_scope(table) and not instance_id


def resolve_gateway_instance_id(cfg: dict[str, Any] | None = None) -> str | None:
    """读写共用的实例 ID 解析。

    优先级（与 Gateway ``resolve_storage_instance_id`` 写路径对齐）：

    1. ``gateway.instance_id``（``config.yaml``）
    2. Redis ``get_gateway_instance_id()``（若 extension 可用）
    3. ``JIUWENCLAW_ID`` / ``JIUWENSWARM_ID``（Manager register.ack）
    4. ``*_PROVISIONED_INSTANCE_ID``
    5. ``GATEWAY_INSTANCE_ID``
    """
    if cfg is None:
        try:
            from jiuwenswarm.common.config import get_config

            cfg = get_config()
        except Exception:
            cfg = None

    if cfg:
        raw = (cfg.get("gateway") or {}).get("instance_id")
        if raw and str(raw).strip():
            return str(raw).strip()

    try:
        from jiuwenswarm.extensions.redis.redis_runtime import get_gateway_instance_id

        redis_id = get_gateway_instance_id()
    except Exception:
        # 模块缺失、初始化异常或未来实现变更均不应打断 instance id 解析；回退 env。
        redis_id = None
    if redis_id and str(redis_id).strip():
        return str(redis_id).strip()

    instance_id = (
        os.getenv("JIUWENCLAW_ID", "").strip()
        or os.getenv("JIUWENSWARM_ID", "").strip()
        or os.getenv("JIUWENSWARM_PROVISIONED_INSTANCE_ID", "").strip()
        or os.getenv("JIUWENCLAW_PROVISIONED_INSTANCE_ID", "").strip()
        or os.getenv("GATEWAY_INSTANCE_ID", "").strip()
    )
    return instance_id or None


def apply_instance_scope(
    table: str,
    filters: dict[str, Any],
    *,
    instance_id: str | None = None,
) -> dict[str, Any]:
    """为 scoped 表查询附加 ``jiuwenclaw_id``；显式 filter 优先。"""
    query = dict(filters)
    if not table_requires_instance_scope(table):
        return query
    resolved = instance_id
    if resolved is None:
        resolved = resolve_gateway_instance_id()
    if resolved and "jiuwenclaw_id" not in query:
        query["jiuwenclaw_id"] = resolved
    return query


__all__ = [
    "apply_instance_scope",
    "instance_scoped_store_names",
    "list_records_requires_bound_instance",
    "resolve_gateway_instance_id",
    "table_requires_instance_scope",
]
