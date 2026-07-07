# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""审计扩展入口 — BaseExtension 实现 + Hook 事件注册.

ExtensionLoader 在 jiuwenswarm/extensions/ 目录下扫描，
发现 audit/extension.yaml + audit/extension.py 后自动加载。

注册流程:
1. initialize() — 创建 LogStore、AlertEngine、Auditor
2. register(registry) — 将 Auditor 的回调方法注册到各个 Hook 事件
3. 随后 Gateway/AgentServer 触发 Hook 事件时，Auditor 自动记录审计日志

查询方式:
- python -m jiuwenswarm.extensions.audit.cli status
- python -m jiuwenswarm.extensions.audit.cli errors --hours 24
- 直接导入: from jiuwenswarm.extensions.audit import get_audit_store
"""

from __future__ import annotations

import logging

from jiuwenswarm.extensions.sdk.base import BaseExtension
from jiuwenswarm.extensions.types import ExtensionConfig, ExtensionMetadata

from .alert_engine import AlertEngine
from .alert_rules import DEFAULT_RULES
from .auditor import Auditor
from .config import AuditConfig, load_audit_config
from .log_store import LogStore

logger = logging.getLogger(__name__)

# ── Hook 事件名（带 scope 前缀）与 Auditor 回调方法的映射 ───────

_HOOK_REGISTRY = {
    "gateway:gateway_started":            "on_gateway_started",
    "gateway:gateway_stopped":            "on_gateway_stopped",
    "gateway:before_chat_request":        "on_gateway_chat_request",
    "agent_server:agent_server_started":  "on_agent_server_started",
    "agent_server:agent_server_stopped":  "on_agent_server_stopped",
    "agent_server:before_chat_request":   "on_agent_server_chat_request",
    "agent_server:memory_before_chat":    "on_memory_before_chat",
    "agent_server:memory_after_chat":     "on_memory_after_chat",
    "agent_server:before_system_prompt_build": "on_before_system_prompt_build",
}


class AuditExtension(BaseExtension):
    """审计扩展 — 记录执行审计日志、检测异常模式、触发告警.

    通过 ExtensionRegistry.register() 注册回调方法，
    监听 Gateway 和 AgentServer 的 Hook 事件。
    """

    def __init__(self) -> None:
        self._config: AuditConfig | None = None
        self._store: LogStore | None = None
        self._alert_engine: AlertEngine | None = None
        self._auditor: Auditor | None = None
        self._registry = None

    async def initialize(self, config: ExtensionConfig) -> None:
        """初始化审计模块.

        1. 加载 AuditConfig（从 config.yaml 的 audit 字段）
        2. 创建 LogStore（指定审计目录）
        3. 创建 AlertEngine（预置规则）
        4. 创建 Auditor（核心审计器）
        """
        self._config = load_audit_config()

        if not self._config.enabled:
            logger.info("[Audit] Audit extension is disabled by config")
            return

        audit_dir = self._config.resolve_audit_dir()
        self._store = LogStore(audit_dir)
        await self._store.initialize()

        self._alert_engine = AlertEngine(
            store=self._store,
            rules=DEFAULT_RULES,
            config=self._config,
        )

        self._auditor = Auditor(
            store=self._store,
            alert_engine=self._alert_engine,
            config=self._config,
        )

        logger.info("[Audit] AuditExtension initialized at %s", audit_dir)

    async def shutdown(self) -> None:
        """关闭审计模块 — 释放 SQLite 连接等资源."""
        if self._store is not None:
            await self._store.close()
            self._store = None
        logger.info("[Audit] AuditExtension shut down")

    def register(self, registry) -> None:
        """注册 Hook 事件回调到 ExtensionRegistry.

        逐个将 Auditor 的回调方法注册到对应的 Hook 事件名。
        """
        if not self._config or not self._config.enabled:
            logger.info("[Audit] Skipping Hook registration (disabled)")
            return

        if self._auditor is None:
            logger.warning("[Audit] Auditor not initialized, skipping registration")
            return

        self._registry = registry

        for event_name, callback_name in _HOOK_REGISTRY.items():
            callback = getattr(self._auditor, callback_name, None)
            if callback is not None:
                registry.register(event_name, callback, priority=200)
                logger.info("[Audit] Registered hook: %s → %s", event_name, callback_name)

        logger.info("[Audit] All hooks registered (%d events)", len(_HOOK_REGISTRY))

    # ── 供外部调用的便捷方法 ────────────────────────────────────

    @property
    def store(self) -> LogStore | None:
        return self._store

    @property
    def auditor(self) -> Auditor | None:
        return self._auditor

    @property
    def alert_engine(self) -> AlertEngine | None:
        return self._alert_engine

    @property
    def audit_config(self) -> AuditConfig | None:
        return self._config


# ── ExtensionLoader 入口函数 ────────────────────────────────────

async def register_extensions(registry):
    """ExtensionLoader 自动调用此函数来加载扩展.

    返回扩展对象列表，ExtensionManager 会追踪其生命周期。
    """
    ext = AuditExtension()
    ext.register(registry)
    return [ext]
