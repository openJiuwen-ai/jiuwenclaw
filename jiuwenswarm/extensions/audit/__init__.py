# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""JiuwenSwarm Execution Audit & Alerting — 执行审计与异常告警模块.

本模块作为 JiuwenSwarm 的标准扩展（Extension），通过 ExtensionLoader
自动发现和加载，订阅 Hook 事件实现执行审计追踪和异常告警。

核心组件:
- Auditor:       Hook 事件回调 → AuditEvent 转换与记录
- LogStore:      JSONL + SQLite 双格式持久化
- AlertEngine:   异常告警引擎（遍历规则检测异常模式）
- AlertRule:     告警规则基类 + 5 种预置规则

使用方式:
1. 自动模式: 将此目录放置在 jiuwenswarm/extensions/ 下，
   ExtensionManager 会自动加载
2. 配置: 在 config.yaml 中添加 audit 字段控制阈值
3. 查询: python -m jiuwenswarm.extensions.audit.cli status

便捷 API:
    from jiuwenswarm.extensions.audit import get_audit_store, get_audit_config
"""

from __future__ import annotations

from .config import AuditConfig, load_audit_config
from .models import Alert, AlertSeverity, AlertStatus, AuditEvent, AuditEventType
from .log_store import LogStore
from .alert_engine import AlertEngine
from .alert_rules import AlertRule, DEFAULT_RULES
from .auditor import Auditor

__all__ = [
    "AuditConfig",
    "load_audit_config",
    "AuditEvent",
    "AuditEventType",
    "Alert",
    "AlertSeverity",
    "AlertStatus",
    "LogStore",
    "AlertEngine",
    "AlertRule",
    "DEFAULT_RULES",
    "Auditor",
    "get_audit_store",
    "get_audit_config",
]


def get_audit_config() -> AuditConfig:
    """获取审计配置（从 config.yaml 加载）."""
    return load_audit_config()


def get_audit_store() -> LogStore:
    """获取审计 LogStore 实例（按默认配置初始化）.

    注意: 返回的 LogStore 需要先调用 initialize() 才能使用。
    对于运行中的系统，审计扩展已自动初始化，可直接查询。
    """
    config = load_audit_config()
    audit_dir = config.resolve_audit_dir()
    return LogStore(audit_dir)
