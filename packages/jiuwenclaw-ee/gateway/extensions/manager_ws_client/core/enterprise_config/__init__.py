# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 侧企业配置三级策略只读加载。"""

from ...infrastructure.utils import normalize_template_ref
from .loader import load_effective_enterprise_config
from .schemas import (
    SERVICE_CONFIG_SLOT,
    SERVICE_CONFIG_TABLE,
    EffectiveEnterpriseConfig,
    RoutingContext,
    TemplateRefSlot,
)

__all__ = (
    "SERVICE_CONFIG_SLOT",
    "SERVICE_CONFIG_TABLE",
    "EffectiveEnterpriseConfig",
    "RoutingContext",
    "TemplateRefSlot",
    "normalize_template_ref",
    "load_effective_enterprise_config",
)
