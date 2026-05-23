"""从 Gateway 本地库解析企业级配置生效策略与模板。"""

from jiuwenclaw.agentserver.enterprise_config.loader import (
    load_effective_enterprise_config,
    routing_context_from_request,
)
from jiuwenclaw.agentserver.enterprise_config.schemas import (
    EffectiveEnterpriseConfig,
    MODEL_SLOT_KEYS,
    RoutingContext,
    SLOT_ENTITY_TABLE,
    TemplateRefSlot,
    normalize_template_ref,
)

__all__ = [
    "EffectiveEnterpriseConfig",
    "MODEL_SLOT_KEYS",
    "RoutingContext",
    "SLOT_ENTITY_TABLE",
    "TemplateRefSlot",
    "normalize_template_ref",
    "load_effective_enterprise_config",
    "routing_context_from_request",
]
