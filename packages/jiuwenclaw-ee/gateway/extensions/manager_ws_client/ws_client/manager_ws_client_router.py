# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""将 Claw Manager config.push 按 key 路由到各业务同步处理器。"""

from __future__ import annotations

import logging
from typing import Any

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
from ..core.template.model_template import apply_model_template_sync
from ..infrastructure.db import ensure_db_handler_ready

logger = logging.getLogger(__name__)


async def apply_config_push(config: dict[str, Any]) -> dict[str, Any] | None:
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


async def _apply_model_templates(payload: dict[str, Any]) -> dict[str, Any] | None:
    op = str(payload.get("op") or "").strip()
    if not op:
        raise ValueError("model_templates.op is required")

    handler = await ensure_db_handler_ready()
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

    handler = await ensure_db_handler_ready()
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

    handler = await ensure_db_handler_ready()
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

    handler = await ensure_db_handler_ready()
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

    handler = await ensure_db_handler_ready()
    result = await apply_config_effective_service_policy_sync(handler, op, payload)
    logger.info(
        "[ManagerWsClient] config_effective_service_policies sync op=%s policy_id=%s",
        op,
        (result or {}).get("policy_id")
        or payload.get("policy_id")
        or (payload.get("policy") or {}).get("id"),
    )
    return result
