# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 侧企业配置辅助（生效配置 schema/加载见 OSS runtime）。"""

from ...infrastructure.utils import normalize_template_ref
from .gateway_db import (
    GatewayDb,
    ensure_db_handler,
    ensure_gateway_db_handler,
    get_shared_gateway_database,
)

__all__ = (
    "GatewayDb",
    "ensure_db_handler",
    "ensure_gateway_db_handler",
    "get_shared_gateway_database",
    "normalize_template_ref",
)
