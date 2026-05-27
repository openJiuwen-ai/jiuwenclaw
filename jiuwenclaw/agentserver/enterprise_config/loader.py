"""从 Gateway DB 加载企业级生效配置（委托 ``manager_ws_client`` 实现）。"""

from __future__ import annotations

import importlib
import os
from typing import Any

from jiuwenclaw.gateway.channel_config_db import (
    _EXT_PKG,
    _ensure_extension_package,
    _resolve_manager_ws_client_root,
)


def _load_enterprise_submodule(name: str) -> Any:
    ext_root = _resolve_manager_ws_client_root()
    if ext_root is None:
        raise ImportError("manager_ws_client extension not found")
    _ensure_extension_package(ext_root)
    return importlib.import_module(f"{_EXT_PKG}.core.enterprise_config.{name}")


_gateway_db_mod = _load_enterprise_submodule("gateway_db")
GatewayDb = _gateway_db_mod.GatewayDb
schemas = _load_enterprise_submodule("schemas")
loader = _load_enterprise_submodule("loader")

_jiuwenclaw_id = os.getenv("JIUWENCLAW_ID", "").strip() or None
gateway_db = GatewayDb.bind(_jiuwenclaw_id)

EffectiveEnterpriseConfig = schemas.EffectiveEnterpriseConfig
DEFAULT_AGENT_LOAD_SLOTS = schemas.DEFAULT_AGENT_LOAD_SLOTS
TemplateRefSlot = schemas.TemplateRefSlot
load_effective_enterprise_config = loader.load_effective_enterprise_config


__all__ = (
    "DEFAULT_AGENT_LOAD_SLOTS",
    "EffectiveEnterpriseConfig",
    "TemplateRefSlot",
    "load_effective_enterprise_config",
)
