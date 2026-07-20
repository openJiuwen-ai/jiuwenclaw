"""实例生命周期：注册 bootstrap、删除时清理 Manager MDB 与 Gateway GDB。"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.core.application_config.log_masking_rule import (
    push_log_masking_rules_sync_to_gateway,
    seed_builtin_log_masking_rules,
)
from jiuwenclaw_manager.core.config_effective_policy.config_default_template_mapping import (
    push_template_mappings_sync_to_gateway,
)
from jiuwenclaw_manager.core.config_effective_policy.config_effective_agent_policy import (
    push_agent_policies_sync_to_gateway,
)
from jiuwenclaw_manager.core.config_effective_policy.config_effective_global_policy import (
    push_global_policies_sync_to_gateway,
)
from jiuwenclaw_manager.core.config_effective_policy.config_effective_service_policy import (
    push_service_policies_sync_to_gateway,
)
from jiuwenclaw_manager.core.instance.instance_service import (
    _LOG_MASKING_SEEDED_KEY,
    is_log_masking_seeded,
    merge_instance_data,
)
from jiuwenclaw_manager.core.template.push_template_to_gateway import (
    rebuild_jid_template_ref_for_gateway,
    sync_referenced_templates_to_gateway,
)
from jiuwenclaw_manager.manager_ws_server.server import (
    ManagerWsServer,
    push_config_op,
)
from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from jiuwenclaw_manager.models.application_config_models import (
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    PERMISSIONS_CONFIG_TABLE_DEF,
    _CHANNEL_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
)

logger = logging.getLogger(__name__)

_LIST_ALL_CAP = 10_000

_MANAGER_POLICY_TABLES = (
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF.table_name,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF.table_name,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF.table_name,
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF.table_name,
)

_MANAGER_INSTANCE_TABLES = (
    _CHANNEL_CONFIG_TABLE_DEF.table_name,
    LOG_MASKING_RULE_TABLE_DEF.table_name,
    LOGGING_CONFIG_TABLE_DEF.table_name,
    _TASK_MEMORY_CONFIG_TABLE_DEF.table_name,
    PERMISSIONS_CONFIG_TABLE_DEF.table_name,
)

_JID_TEMPLATE_REF_TABLE = JID_TEMPLATE_REF_TABLE_DEF.table_name


async def _seed_log_masking_if_needed(handler: DBHandler, jiuwenclaw_id: str) -> None:
    if await is_log_masking_seeded(handler, jiuwenclaw_id):
        return
    seeded = await seed_builtin_log_masking_rules(handler, jiuwenclaw_id)
    await merge_instance_data(handler, jiuwenclaw_id, {_LOG_MASKING_SEEDED_KEY: True})
    if seeded:
        logger.info(
            "[GatewayBootstrap] seeded %d builtin log_masking_rule row(s) for %s",
            seeded,
            jiuwenclaw_id,
        )


async def sync_data_to_gateway_on_register(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """Gateway 注册成功后：按固定顺序向 GDB 全量同步 Manager 权威配置。

    顺序说明：
    1. 模板（策略/映射依赖）
    2. 全局 / Service / Agent 策略（Agent 依赖 Service）
    3. 默认模板映射
    4. 日志脱敏规则
    5. 重建 Manager 侧 jid_template_ref 索引
    """
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    results: dict[str, Any] = {}
    try:
        results["templates"] = await sync_referenced_templates_to_gateway(handler, jid)
    except Exception:
        logger.warning(
            "[GatewayBootstrap] template sync failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        raise

    for name, push_fn, before_fn in (
        ("global_policies", push_global_policies_sync_to_gateway, None),
        ("service_policies", push_service_policies_sync_to_gateway, None),
        ("agent_policies", push_agent_policies_sync_to_gateway, None),
        ("template_mappings", push_template_mappings_sync_to_gateway, None),
        (
            "log_masking_rule",
            push_log_masking_rules_sync_to_gateway,
            _seed_log_masking_if_needed,
        ),
    ):
        try:
            if before_fn is not None:
                await before_fn(handler, jid)
            results[name] = await push_fn(handler, jid)
        except Exception:
            logger.warning(
                "[GatewayBootstrap] %s sync failed jiuwenclaw_id=%s",
                name,
                jid,
                exc_info=True,
            )
            raise

    try:
        await rebuild_jid_template_ref_for_gateway(handler, jid)
        results["jid_template_ref_rebuilt"] = True
    except Exception:
        logger.warning(
            "[GatewayBootstrap] jid_template_ref rebuild failed jiuwenclaw_id=%s",
            jid,
            exc_info=True,
        )
        raise

    logger.info("[GatewayBootstrap] completed jiuwenclaw_id=%s sections=%s", jid, list(results))
    return results


def _delete_pk_for_row(table: str, row: Any, jiuwenclaw_id: str) -> dict[str, Any]:
    if table == _JID_TEMPLATE_REF_TABLE:
        return {
            "jiuwenclaw_id": jiuwenclaw_id,
            "slot": getattr(row, "slot"),
            "template_id": getattr(row, "template_id"),
        }
    if table in _MANAGER_POLICY_TABLES:
        return {"id": getattr(row, "id"), "jiuwenclaw_id": jiuwenclaw_id}
    return {"id": getattr(row, "id")}


async def _purge_table_rows(
    handler: DBHandler,
    table: str,
    jiuwenclaw_id: str,
) -> int:
    rows = await handler.list_records(
        table,
        {"jiuwenclaw_id": jiuwenclaw_id},
        limit=_LIST_ALL_CAP,
        offset=0,
    )
    deleted = 0
    for row in rows:
        pk = _delete_pk_for_row(table, row, jiuwenclaw_id)
        if await handler.delete(table, pk):
            deleted += 1
    return deleted


async def purge_manager_instance_data(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, int]:
    """删除 Manager MDB 中指定实例的全部配置数据（不含 ``instance_info``）。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {}

    deleted_counts: dict[str, int] = {}
    for table in (*_MANAGER_POLICY_TABLES, *_MANAGER_INSTANCE_TABLES):
        count = await _purge_table_rows(handler, table, jid)
        if count:
            deleted_counts[table] = count

    count = await _purge_table_rows(handler, _JID_TEMPLATE_REF_TABLE, jid)
    if count:
        deleted_counts[_JID_TEMPLATE_REF_TABLE] = count

    logger.info(
        "[InstanceDataLifecycle] purged manager instance data jiuwenclaw_id=%s counts=%s",
        jid,
        deleted_counts,
    )
    return deleted_counts


async def _purge_gateway_via_ws(jiuwenclaw_id: str) -> bool:
    server = ManagerWsServer.get_instance()
    if server is None:
        return False
    client = await server.lookup_active_client(jiuwenclaw_id, service_type="gateway")
    if client is None:
        return False
    try:
        await push_config_op(
            jiuwenclaw_id,
            {
                "instance_data_lifecycle": {
                    "op": "purge",
                    "skip_runtime_update": True,
                }
            },
        )
        return True
    except ValueError:
        logger.warning(
            "[InstanceDataLifecycle] gateway ws purge failed jiuwenclaw_id=%s",
            jiuwenclaw_id,
            exc_info=True,
        )
        return False


async def purge_gateway_instance_data(jiuwenclaw_id: str) -> dict[str, Any]:
    """通过 WS 通知在线 Gateway 清理 GDB；未连接则跳过。"""
    jid = str(jiuwenclaw_id or "").strip()
    if not jid:
        return {"purged": False}

    if await _purge_gateway_via_ws(jid):
        return {"purged": True}

    logger.info(
        "[InstanceDataLifecycle] gateway purge skipped jiuwenclaw_id=%s (not connected)",
        jid,
    )
    return {"purged": False}


async def purge_instance_all_data(
    handler: DBHandler,
    jiuwenclaw_id: str,
) -> dict[str, Any]:
    """删除 Manager 实例数据；Gateway 在线时同步清理 GDB。"""
    jid = str(jiuwenclaw_id or "").strip()
    manager_counts = await purge_manager_instance_data(handler, jid)
    gateway_result = await purge_gateway_instance_data(jid)
    return {
        "manager": manager_counts,
        "gateway": gateway_result,
    }


__all__ = (
    "sync_data_to_gateway_on_register",
    "purge_instance_all_data",
    "purge_manager_instance_data",
    "purge_gateway_instance_data",
)
