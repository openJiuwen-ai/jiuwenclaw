# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Gateway 产品形态：个人版 vs 企业版（唯一来源：``config.yaml::gateway.edition``）。"""

from __future__ import annotations

from typing import Any, Literal

GatewayEdition = Literal["personal", "enterprise"]

EDITION_PERSONAL: GatewayEdition = "personal"
EDITION_ENTERPRISE: GatewayEdition = "enterprise"

_VALID_EDITIONS: frozenset[str] = frozenset({EDITION_PERSONAL, EDITION_ENTERPRISE})


def normalize_gateway_edition(raw: object) -> GatewayEdition:
    """归一化 ``gateway.edition``；非法/空值回退 ``personal``。"""
    edition = str(raw or "").strip().lower()
    if edition in _VALID_EDITIONS:
        return edition  # type: ignore[return-value]
    if edition in {"ee", "corp", "enterprise_edition"}:
        return EDITION_ENTERPRISE
    return EDITION_PERSONAL


def resolve_gateway_edition(cfg: dict[str, Any] | None = None) -> GatewayEdition:
    """解析 Gateway 产品形态；仅读 ``gateway.edition``，缺省为 ``personal``。"""
    config = cfg
    if config is None:
        try:
            from jiuwenswarm.common.config import get_config

            config = get_config()
        except Exception:
            config = {}

    gw = config.get("gateway") if isinstance(config, dict) else None
    gw = gw if isinstance(gw, dict) else {}
    return normalize_gateway_edition(gw.get("edition"))


def is_gateway_enterprise(cfg: dict[str, Any] | None = None) -> bool:
    return resolve_gateway_edition(cfg) == EDITION_ENTERPRISE


__all__ = [
    "EDITION_ENTERPRISE",
    "EDITION_PERSONAL",
    "GatewayEdition",
    "is_gateway_enterprise",
    "normalize_gateway_edition",
    "resolve_gateway_edition",
]
