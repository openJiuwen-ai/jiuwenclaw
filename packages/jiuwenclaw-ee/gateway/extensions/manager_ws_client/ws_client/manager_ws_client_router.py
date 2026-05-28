# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""将 Claw Manager config.push 按 key 路由到各业务同步处理器。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..infrastructure.db import Database

from ..core.config_effective_policy.config_default_template_mapping import (
    apply_config_default_template_mapping_sync,
)
from ..core.config_effective_policy.config_effective_agent_policy import (
    apply_config_effective_agent_policy_sync,
)
from ..core.config_effective_policy.config_effective_global_policy import (
    apply_config_effective_global_policy_sync,
)
from ..core.config_effective_policy.config_effective_service_policy import (
    apply_config_effective_service_policy_sync,
)
from ..core.template.extension_config_template import (
    apply_extension_config_template_sync,
)
from ..core.template.model_template import apply_model_template_sync
from ..core.template.service_config_template import apply_service_config_template_sync
from ..core.template.skill_whitelist_template import (
    apply_skill_whitelist_template_sync,
)
from ..core.application_config.channel_config import apply_channel_config_sync

logger = logging.getLogger(__name__)

_GATEWAY_DB = Database(relative_root=Path(__file__).resolve().parents[1])


async def _ensure_db_handler():
    return await _GATEWAY_DB.ensure_ready(log_prefix="manager_ws_client")


async def apply_config_push(config: dict[str, Any]) -> dict[str, Any] | None:
    channel_config = config.get("channel_config")
    if isinstance(channel_config, dict) and channel_config.get("op"):
        return await _apply_channel_config(channel_config)

    extension_config_templates = config.get("extension_config_templates")
    if isinstance(extension_config_templates, dict) and extension_config_templates.get(
        "op"
    ):
        return await _apply_extension_config_templates(extension_config_templates)

    skill_whitelist_templates = config.get("skill_whitelist_templates")
    if isinstance(skill_whitelist_templates, dict) and skill_whitelist_templates.get(
        "op"
    ):
        return await _apply_skill_whitelist_templates(skill_whitelist_templates)

    service_config_templates = config.get("service_config_templates")
    if isinstance(service_config_templates, dict) and service_config_templates.get(
        "op"
    ):
        return await _apply_service_config_templates(service_config_templates)

    model_templates = config.get("model_templates")
    if isinstance(model_templates, dict) and model_templates.get("op"):
        return await _apply_model_templates(model_templates)

    template_mappings = config.get("config_default_template_mappings")
    if isinstance(template_mappings, dict) and template_mappings.get("op"):
        return await _apply_config_default_template_mappings(template_mappings)

    agent_policies = config.get("config_effective_agent_policies")
    if isinstance(agent_policies, dict) and agent_policies.get("op"):
        return await _apply_config_effective_agent_policies(agent_policies)

    global_policies = config.get("config_effective_global_policies")
    if isinstance(global_policies, dict) and global_policies.get("op"):
        return await _apply_config_effective_global_policies(global_policies)

    service_policies = config.get("config_effective_service_policies")
    if isinstance(service_policies, dict) and service_policies.get("op"):
        return await _apply_config_effective_service_policies(service_policies)
    return None


async def _apply_channel_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("channel_config.op is required")

    handler = await _ensure_db_handler()
    result = await apply_channel_config_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] channel_config sync op=%s channel_id=%s",
        op,
        (result or {}).get("channel_id") or payload.get("channel_id"),
    )
    return result


async def _apply_extension_config_templates(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("extension_config_templates.op is required")

    handler = await _ensure_db_handler()
    result = await apply_extension_config_template_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] extension_config_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("template_id"),
    )
    return result


async def _apply_skill_whitelist_templates(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("skill_whitelist_templates.op is required")

    handler = await _ensure_db_handler()
    result = await apply_skill_whitelist_template_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] skill_whitelist_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("template_id"),
    )
    return result


async def _apply_service_config_templates(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("service_config_templates.op is required")

    handler = await _ensure_db_handler()
    result = await apply_service_config_template_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] service_config_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("template_id"),
    )
    
    # 触发 Runtime Management Client 配置热更新
    try:
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        
        registry = ExtensionRegistry.get_instance()
        if registry is not None:
            # 查找 RuntimeManagementExtension
            for ext in registry._agent_server_clients:
                if hasattr(ext, 'get_client'):
                    client = ext.get_client()
                    if hasattr(client, 'set_or_update_server_config'):
                        # 构造 config 参数，设置 service_template 为 true 以触发更新
                        client.set_or_update_server_config(config={"service_template": True})
                        logger.info(
                            "[ManagerWsClient] triggered runtime management config update after service_config_templates %s",
                            op,
                        )
                        break
    except Exception as exc:
        logger.warning(
            "[ManagerWsClient] failed to trigger runtime management config update: %s",
            exc,
        )
    
    return result


async def _apply_model_templates(payload: dict[str, Any]) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("model_templates.op is required")

    handler = await _ensure_db_handler()
    result = await apply_model_template_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] model_templates sync op=%s template_id=%s",
        op,
        (result or {}).get("template_id")
        or payload.get("template_id")
        or (payload.get("template") or {}).get("id"),
    )
    return result


async def _apply_config_default_template_mappings(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_default_template_mappings.op is required")

    handler = await _ensure_db_handler()
    result = await apply_config_default_template_mapping_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] config_default_template_mappings sync op=%s mapping_id=%s",
        op,
        (result or {}).get("mapping_id")
        or payload.get("mapping_id")
        or (payload.get("mapping") or {}).get("id"),
    )
    return result


async def _apply_config_effective_agent_policies(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_effective_agent_policies.op is required")

    handler = await _ensure_db_handler()
    result = await apply_config_effective_agent_policy_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] config_effective_agent_policies sync op=%s policy_id=%s",
        op,
        (result or {}).get("policy_id")
        or payload.get("policy_id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result


async def _apply_config_effective_global_policies(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_effective_global_policies.op is required")

    handler = await _ensure_db_handler()
    result = await apply_config_effective_global_policy_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] config_effective_global_policies sync op=%s policy_id=%s",
        op,
        (result or {}).get("policy_id")
        or payload.get("policy_id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result


async def _apply_config_effective_service_policies(
    payload: dict[str, Any],
) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("config_effective_service_policies.op is required")

    handler = await _ensure_db_handler()
    result = await apply_config_effective_service_policy_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] config_effective_service_policies sync op=%s policy_id=%s",
        op,
        (result or {}).get("policy_id")
        or payload.get("policy_id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result
