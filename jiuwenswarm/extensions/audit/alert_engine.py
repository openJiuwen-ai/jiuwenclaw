# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""异常告警引擎 — 检测异常模式并触发告警.

工作流程:
1. 每条新审计事件写入后 → AlertEngine.check_event()
2. 遍历所有 AlertRule.evaluate()
3. 匹配规则 → 创建 Alert → 持久化 + logger 输出
"""

from __future__ import annotations

import logging
from typing import Any

from .alert_rules import AlertRule
from .config import AuditConfig
from .log_store import LogStore
from .models import Alert, AlertSeverity, AuditEvent

logger = logging.getLogger(__name__)


class AlertEngine:
    """告警引擎 — 接收审计事件，遍历规则，触发告警.

    用法::

        engine = AlertEngine(store, rules, config)
        alerts = await engine.check_event(event)
        for alert in alerts:
            # 告警已自动写入 store
    """

    def __init__(
        self,
        store: LogStore,
        rules: list[AlertRule],
        config: AuditConfig,
    ) -> None:
        self._store = store
        self._rules = rules
        self._config = config
        self._suppressed_rules: set[str] = set()  # 可配置抑制某些规则

    async def check_event(self, event: AuditEvent) -> list[Alert]:
        """检查一条新事件是否触发告警.

        遍历所有注册的规则，返回触发的告警列表。
        告警会自动写入 LogStore。
        """
        triggered: list[Alert] = []

        for rule in self._rules:
            if rule.name in self._suppressed_rules:
                continue

            try:
                alert = await rule.evaluate(event, self._store, self._config)
            except Exception as exc:
                logger.warning("[Audit] AlertRule %s evaluate failed: %s", rule.name, exc)
                continue

            if alert is not None:
                triggered.append(alert)
                # 持久化告警
                await self._store.write_alert(alert)
                # 日志输出
                self._log_alert(alert)

        return triggered

    async def evaluate_periodic_rules(self) -> list[Alert]:
        """定期评估周期性规则（如 Token 超限等).

        注: 当前所有规则都是 event-driven（由 check_event 触发），
        此方法为未来预留的周期性评估接口。
        """
        return []

    async def get_active_alerts(self) -> list[Alert]:
        """获取所有活跃告警."""
        return await self._store.query_alerts({"status": "active", "limit": 100})

    async def get_alert_history(self, hours: int = 48) -> list[Alert]:
        """获取告警历史."""
        return await self._store.query_alerts({"hours": hours, "limit": 500})

    async def resolve_alert(self, alert_id: str) -> None:
        """手动解决一条告警."""
        await self._store.resolve_alert(alert_id)

    def suppress_rule(self, rule_name: str) -> None:
        """抑制某个规则（不触发告警但仍记录审计事件）."""
        self._suppressed_rules.add(rule_name)

    def unsuppress_rule(self, rule_name: str) -> None:
        """取消规则抑制."""
        self._suppressed_rules.discard(rule_name)

    # ── 内部方法 ────────────────────────────────────────────────

    def _log_alert(self, alert: Alert) -> None:
        """将告警写入 logger（按严重级别选择日志级别）."""
        message = f"[Audit Alert] {alert.rule_name}: {alert.message}"

        if alert.severity == AlertSeverity.CRITICAL:
            logger.critical(message)
        elif alert.severity == AlertSeverity.WARNING:
            logger.warning(message)
        else:
            logger.info(message)

        # 同时输出告警上下文（供运维排查）
        if alert.context:
            logger.info("[Audit Alert Context] %s", alert.context)
