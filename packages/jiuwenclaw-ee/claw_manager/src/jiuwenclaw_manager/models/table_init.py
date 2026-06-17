"""Claw Manager 库表初始化（幂等）。"""

from __future__ import annotations

from openjiuwen_runtime.foundation.db.handler import DBHandler

from jiuwenclaw_manager.models.config_effective_policy_models import (
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
)
from jiuwenclaw_manager.models.instance_models import INSTANCE_INFO_TABLE_DEF
from jiuwenclaw_manager.models.key_models import (
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
)
from jiuwenclaw_manager.models.application_config_models import (
    LOG_MASKING_RULE_TABLE_DEF,
    _CHANNEL_CONFIG_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    _EMBED_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
)
from jiuwenclaw_manager.models.jid_template_ref_models import (
    JID_TEMPLATE_REF_TABLE_DEF,
)
from jiuwenclaw_manager.models.template_models import (
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_WHITELIST_TEMPLATE_TABLE_DEF,
)

ALL_TABLE_DEFINITIONS = (
    INSTANCE_INFO_TABLE_DEF,
    MANAGER_IDENTITY_TABLE_DEF,
    INSTANCE_ENC_PUBKEY_TABLE_DEF,
    _CHANNEL_CONFIG_TABLE_DEF,
    _EMBED_CONFIG_TABLE_DEF,
    _TASK_MEMORY_CONFIG_TABLE_DEF,
    LOG_MASKING_RULE_TABLE_DEF,
    LOGGING_CONFIG_TABLE_DEF,
    MODEL_TEMPLATE_TABLE_DEF,
    EXTENSION_CONFIG_TEMPLATE_TABLE_DEF,
    SKILL_WHITELIST_TEMPLATE_TABLE_DEF,
    SERVICE_CONFIG_TEMPLATE_TABLE_DEF,
    CONFIG_DEFAULT_TEMPLATE_MAPPING_TABLE_DEF,
    CONFIG_EFFECTIVE_GLOBAL_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_SERVICE_POLICY_TABLE_DEF,
    CONFIG_EFFECTIVE_AGENT_POLICY_TABLE_DEF,
    JID_TEMPLATE_REF_TABLE_DEF,
)


async def init_all_tables(handler: DBHandler) -> None:
    for table_def in ALL_TABLE_DEFINITIONS:
        await handler.init_table(table_def)
