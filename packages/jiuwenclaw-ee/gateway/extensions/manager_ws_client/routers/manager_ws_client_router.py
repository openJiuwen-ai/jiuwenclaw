# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved.
"""将 Claw Manager config.push 按 key 路由到各业务同步处理器。"""

from __future__ import annotations

import logging
from typing import Any

from ..core.application_config.channel_config import apply_channel_config
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
    channel_config = config.get("channel_config")
    if isinstance(channel_config, dict) and channel_config.get("op"):
        return await apply_channel_config(channel_config)

    extension_config_templates = config.get("extension_config_templates")
    if isinstance(extension_config_templates, dict) and extension_config_templates.get(
        "op"
    ):
        return await apply_extension_config_template(extension_config_templates)

    skill_whitelist_templates = config.get("skill_whitelist_templates")
    if isinstance(skill_whitelist_templates, dict) and skill_whitelist_templates.get(
        "op"
    ):
        return await apply_skill_whitelist_template(skill_whitelist_templates)

    service_config_templates = config.get("service_config_templates")
    if isinstance(service_config_templates, dict) and service_config_templates.get(
        "op"
    ):
        return await apply_service_config_template(service_config_templates)

    model_templates = config.get("model_templates")
    if isinstance(model_templates, dict) and model_templates.get("op"):
        return await apply_model_template(model_templates)

    template_mappings = config.get("config_default_template_mappings")
    if isinstance(template_mappings, dict) and template_mappings.get("op"):
        return await apply_config_default_template_mapping(template_mappings)

    agent_policies = config.get("config_effective_agent_policies")
    if isinstance(agent_policies, dict) and agent_policies.get("op"):
        return await apply_config_effective_agent_policy(agent_policies)

    global_policies = config.get("config_effective_global_policies")
    if isinstance(global_policies, dict) and global_policies.get("op"):
        return await apply_config_effective_global_policy(global_policies)

    service_policies = config.get("config_effective_service_policies")
    if isinstance(service_policies, dict) and service_policies.get("op"):
        return await apply_config_effective_service_policy(service_policies)
    return None
