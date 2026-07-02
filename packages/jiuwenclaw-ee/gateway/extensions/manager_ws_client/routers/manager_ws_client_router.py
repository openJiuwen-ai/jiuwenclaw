# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""将 Claw Manager config.push 按 key 路由到各业务同步处理器。"""

from __future__ import annotations

import logging
from typing import Any

from ..core.application_config.channel_config import apply_channel_config
from ..core.application_config.embed_config import apply_embed_config
from ..core.application_config.log_masking_rule import apply_log_masking_rule
from ..core.application_config.logging_config import apply_logging_config
from ..core.application_config.task_memory_config import apply_task_memory_config
from ..core.application_config.permissions_config import apply_permissions_config
from ..core.instance import apply_instance_data_lifecycle
from ..infrastructure.utils import assert_jiuwenclaw_id_matches
from ..core.config_effective_policy.config_default_template_mapping import (
    apply_config_default_template_mapping,
)
from ..core.config_effective_policy.config_effective_agent_policy import (
    apply_config_effective_agent_policy,
)
from ..core.config_effective_policy.config_effective_global_policy import (
    apply_config_effective_global_policy,
)
from ..core.config_effective_policy.config_effective_service_policy import (
    apply_config_effective_service_policy,
)
from ..core.template.extension_config_template import (
    apply_extension_config_template,
)
from ..core.template.model_template import apply_model_template
from ..core.template.service_config_template import apply_service_config_template
from ..core.template.skill_whitelist_template import apply_skill_whitelist_template

logger = logging.getLogger(__name__)

_RUNTIME_MUTATING_OPS = frozenset({"create", "update", "delete", "upsert"})


def _trigger_runtime_management_config_update(op: str) -> None:
    logger.info(
        "[ManagerWsClient] trigger runtime management config update op=%s",
        op,
    )
    try:
        from jiuwenclaw.extensions.registry import ExtensionRegistry

        registry = ExtensionRegistry.get_instance()
        ext = registry.get_agent_server_client_extension()
        if ext is None or not hasattr(ext, "get_client"):
            return
        client = ext.get_client()
        if client is None or not hasattr(client, "set_or_update_server_config"):
            return
        client.set_or_update_server_config(config={"enterprise_config_update": True})
        logger.info(
            "[ManagerWsClient] triggered runtime management config update after config.push %s",
            op,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[ManagerWsClient] failed to trigger runtime management config update: %s",
            exc,
        )


async def apply_config_push(
    revision: str,
    jiuwenclaw_id: str,
    config: dict[str, Any],
) -> dict[str, Any] | None:
    assert_jiuwenclaw_id_matches(jiuwenclaw_id)
    logger.info(
        "[ManagerWsClient] config.push revision=%s jiuwenclaw_id=%s keys=%s",
        revision,
        jiuwenclaw_id,
        list(config.keys()),
    )
    matched_payload: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    skip_runtime_update = False

    channel_config = config.get("channel_config")
    log_masking_rule = config.get("log_masking_rule")
    logging_config = config.get("logging_config")
    embed_config = config.get("embed_config")
    task_memory_config = config.get("task_memory_config")
    permissions_config = config.get("permissions_config")
    extension_config_templates = config.get("extension_config_templates")
    skill_whitelist_templates = config.get("skill_whitelist_templates")
    service_config_templates = config.get("service_config_templates")
    model_templates = config.get("model_templates")
    template_mappings = config.get("config_default_template_mappings")
    agent_policies = config.get("config_effective_agent_policies")
    global_policies = config.get("config_effective_global_policies")
    service_policies = config.get("config_effective_service_policies")
    instance_data_lifecycle = config.get("instance_data_lifecycle")

    if isinstance(instance_data_lifecycle, dict) and instance_data_lifecycle.get("op"):
        matched_payload = instance_data_lifecycle
        skip_runtime_update = True
        result = await apply_instance_data_lifecycle(instance_data_lifecycle)

    elif isinstance(channel_config, dict) and channel_config.get("op"):
        matched_payload = channel_config
        skip_runtime_update = True
        result = await apply_channel_config(channel_config)

    elif isinstance(log_masking_rule, dict) and log_masking_rule.get("op"):
        matched_payload = log_masking_rule
        result = await apply_log_masking_rule(log_masking_rule)

    elif isinstance(logging_config, dict) and logging_config.get("op"):
        matched_payload = logging_config
        result = await apply_logging_config(logging_config)

    elif isinstance(embed_config, dict) and embed_config.get("op"):
        matched_payload = embed_config
        result = await apply_embed_config(embed_config)

    elif isinstance(task_memory_config, dict) and task_memory_config.get("op"):
        matched_payload = task_memory_config
        result = await apply_task_memory_config(task_memory_config)

    elif isinstance(permissions_config, dict) and permissions_config.get("op"):
        matched_payload = permissions_config
        result = await apply_permissions_config(permissions_config)

    elif isinstance(extension_config_templates, dict) and extension_config_templates.get("op"):
        matched_payload = extension_config_templates
        result = await apply_extension_config_template(extension_config_templates)

    elif isinstance(skill_whitelist_templates, dict) and skill_whitelist_templates.get("op"):
        matched_payload = skill_whitelist_templates
        result = await apply_skill_whitelist_template(skill_whitelist_templates)

    elif isinstance(service_config_templates, dict) and service_config_templates.get("op"):
        matched_payload = service_config_templates
        result = await apply_service_config_template(service_config_templates)

    elif isinstance(model_templates, dict) and model_templates.get("op"):
        matched_payload = model_templates
        result = await apply_model_template(model_templates)

    elif isinstance(template_mappings, dict) and template_mappings.get("op"):
        matched_payload = template_mappings
        result = await apply_config_default_template_mapping(template_mappings)

    elif isinstance(agent_policies, dict) and agent_policies.get("op"):
        matched_payload = agent_policies
        result = await apply_config_effective_agent_policy(agent_policies)

    elif isinstance(global_policies, dict) and global_policies.get("op"):
        matched_payload = global_policies
        result = await apply_config_effective_global_policy(global_policies)

    elif isinstance(service_policies, dict) and service_policies.get("op"):
        matched_payload = service_policies
        result = await apply_config_effective_service_policy(service_policies)

    if matched_payload is not None:
        op = str(matched_payload.get("op") or "").strip()
        skip_runtime_update = skip_runtime_update or bool(
            matched_payload.get("skip_runtime_update")
        )
        if op in _RUNTIME_MUTATING_OPS and not skip_runtime_update:
            _trigger_runtime_management_config_update(op)
        return result
    return None
