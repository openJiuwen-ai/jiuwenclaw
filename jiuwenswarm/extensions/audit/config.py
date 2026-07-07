# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计配置管理 — 从 config.yaml 的 audit 字段读取配置."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jiuwenswarm.common.config import get_config
from jiuwenswarm.common.utils import get_agent_workspace_dir

_AUDIT_SUBDIR = "audit"


@dataclass
class AuditConfig:
    """审计模块配置."""

    enabled: bool = True
    audit_dir: str = ""                     # 空=自动推导到 workspace/audit/
    retention_days: int = 30
    consecutive_failure_threshold: int = 3
    token_daily_threshold: int = 1_000_000
    response_timeout_seconds: float = 120.0
    permission_denial_window_minutes: int = 5
    permission_denial_threshold: int = 10
    error_rate_window_minutes: int = 15
    error_rate_threshold_ratio: float = 0.5

    def resolve_audit_dir(self) -> Path:
        """解析审计日志目录（优先用配置值，否则推导默认路径）."""
        if self.audit_dir and self.audit_dir.strip():
            return Path(self.audit_dir.strip()).resolve()
        return get_agent_workspace_dir() / _AUDIT_SUBDIR


def load_audit_config() -> AuditConfig:
    """从 config.yaml 的 audit 字段加载配置.

    若 config.yaml 中没有 audit 段，则全部使用默认值。
    """
    cfg = get_config()
    audit_cfg: dict[str, Any] | None = None

    if isinstance(cfg, dict):
        audit_cfg = cfg.get("audit")
    if not isinstance(audit_cfg, dict):
        return AuditConfig()

    return AuditConfig(
        enabled=_bool_val(audit_cfg.get("enabled"), default=True),
        audit_dir=str(audit_cfg.get("audit_dir") or ""),
        retention_days=_int_val(audit_cfg.get("retention_days"), default=30),
        consecutive_failure_threshold=_int_val(
            audit_cfg.get("consecutive_failure_threshold"), default=3,
        ),
        token_daily_threshold=_int_val(
            audit_cfg.get("token_daily_threshold"), default=1_000_000,
        ),
        response_timeout_seconds=_float_val(
            audit_cfg.get("response_timeout_seconds"), default=120.0,
        ),
        permission_denial_window_minutes=_int_val(
            audit_cfg.get("permission_denial_window_minutes"), default=5,
        ),
        permission_denial_threshold=_int_val(
            audit_cfg.get("permission_denial_threshold"), default=10,
        ),
        error_rate_window_minutes=_int_val(
            audit_cfg.get("error_rate_window_minutes"), default=15,
        ),
        error_rate_threshold_ratio=_float_val(
            audit_cfg.get("error_rate_threshold_ratio"), default=0.5,
        ),
    )


# ── 类型安全辅助 ────────────────────────────────────────────────

def _bool_val(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _int_val(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float_val(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
