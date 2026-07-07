# Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

"""核心审计器 — Hook 回调处理.

订阅 ExtensionRegistry 的 Hook 事件，将每个事件转换为 AuditEvent，
写入 LogStore，并交由 AlertEngine 检查是否触发告警。

用法::

    auditor = Auditor(store, alert_engine, config)
    # 在 extension.py 中通过 registry.register("gateway:before_chat_request", auditor.on_gateway_chat_request)
    # 注册回调
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .alert_engine import AlertEngine
from .config import AuditConfig
from .log_store import LogStore
from .models import (
    AuditEvent,
    AuditEventType,
    HOOK_EVENT_MAP,
)

logger = logging.getLogger(__name__)


class Auditor:
    """审计器 — Hook 事件的接收与转换中心.

    每个 Hook 回调方法对应一个 ExtensionRegistry 事件，
    将 Hook 上下文转换为 AuditEvent 并持久化。
    """

    def __init__(
        self,
        store: LogStore,
        alert_engine: AlertEngine,
        config: AuditConfig,
    ) -> None:
        self._store = store
        self._alert_engine = alert_engine
        self._config = config
        self._session_tracker: dict[str, dict[str, Any]] = {}
        self._request_timestamps: dict[str, float] = {}  # request_id → 首次看到时间

    # ── Gateway 层 Hook 回调 ────────────────────────────────────

    async def on_gateway_started(self, context: Any = None, **kwargs: Any) -> None:
        """Gateway 启动审计."""
        event = AuditEvent(
            event_type=AuditEventType.SYSTEM_START,
            metadata={"source": "gateway", "context_type": type(context).__name__ if context else "none"},
        )
        await self._record_and_check(event)

    async def on_gateway_chat_request(self, context: Any = None, **kwargs: Any) -> None:
        """Gateway 层用户请求审计.

        context 是 GatewayChatHookContext 或 dict。
        """
        session_id, channel_id, request_id, req_method, params = _extract_context(context)

        # 记录请求到达时间（用于后续计算响应时延）
        if request_id:
            self._request_timestamps[request_id] = time.time()

        # 更新会话追踪器
        if session_id:
            tracker = self._session_tracker.setdefault(session_id, {
                "first_request_ts": time.time(),
                "request_count": 0,
                "error_count": 0,
                "channel_id": channel_id,
            })
            tracker["request_count"] += 1
            tracker["last_request_ts"] = time.time()

        event = AuditEvent(
            event_type=AuditEventType.CHAT_REQUEST,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            metadata={
                "req_method": req_method,
                "source": "gateway",
                "params_keys": list(params.keys()) if isinstance(params, dict) else [],
            },
        )
        await self._record_and_check(event)

    async def on_gateway_stopped(self, context: Any = None, **kwargs: Any) -> None:
        """Gateway 关闭审计."""
        event = AuditEvent(
            event_type=AuditEventType.SYSTEM_STOP,
            metadata={"source": "gateway"},
        )
        await self._record_and_check(event)

    # ── AgentServer 层 Hook 回调 ────────────────────────────────

    async def on_agent_server_started(self, context: Any = None, **kwargs: Any) -> None:
        """AgentServer 启动审计."""
        event = AuditEvent(
            event_type=AuditEventType.SYSTEM_START,
            metadata={"source": "agent_server"},
        )
        await self._record_and_check(event)

    async def on_agent_server_chat_request(self, context: Any = None, **kwargs: Any) -> None:
        """AgentServer 层请求审计."""
        session_id, channel_id, request_id, req_method, params = _extract_context(context)

        if request_id and request_id not in self._request_timestamps:
            self._request_timestamps[request_id] = time.time()

        event = AuditEvent(
            event_type=AuditEventType.CHAT_REQUEST,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            metadata={
                "req_method": req_method,
                "source": "agent_server",
                "params_keys": list(params.keys()) if isinstance(params, dict) else [],
            },
        )
        await self._record_and_check(event)

    async def on_memory_before_chat(self, context: Any = None, **kwargs: Any) -> None:
        """记忆注入前审计."""
        session_id, channel_id, request_id, _, _ = _extract_memory_context(context)

        event = AuditEvent(
            event_type=AuditEventType.MEMORY_BEFORE_CHAT,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            metadata=_extract_memory_metadata(context),
        )
        await self._record_and_check(event)

    async def on_memory_after_chat(self, context: Any = None, **kwargs: Any) -> None:
        """对话完成审计 — 提取 Token 消耗和响应信息.

        这是审计的关键事件：从中提取 Token 消耗、响应时长等核心数据。
        """
        session_id, channel_id, request_id, agent_name, _ = _extract_memory_context(context)
        memory_meta = _extract_memory_metadata(context)

        # 计算响应时延（从请求首次出现到完成）
        duration_ms = None
        if request_id and request_id in self._request_timestamps:
            elapsed = (time.time() - self._request_timestamps[request_id]) * 1000
            duration_ms = elapsed
            # 清理计时器
            del self._request_timestamps[request_id]

        # 提取 Token 消耗
        token_usage = None
        if isinstance(memory_meta, dict):
            token_data = memory_meta.get("token_usage") or memory_meta.get("usage")
            if isinstance(token_data, dict):
                token_usage = token_data

        # 更新会话追踪器
        if session_id and session_id in self._session_tracker:
            tracker = self._session_tracker[session_id]
            if token_usage:
                prev_tokens = tracker.get("total_tokens", {})
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    prev_tokens[key] = prev_tokens.get(key, 0) + (token_usage.get(key) or 0)
                tracker["total_tokens"] = prev_tokens

        event = AuditEvent(
            event_type=AuditEventType.MEMORY_AFTER_CHAT,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            agent_name=agent_name,
            duration_ms=duration_ms,
            token_usage=token_usage,
            metadata=memory_meta,
        )
        await self._record_and_check(event)

    async def on_agent_server_stopped(self, context: Any = None, **kwargs: Any) -> None:
        """AgentServer 关闭审计."""
        event = AuditEvent(
            event_type=AuditEventType.SYSTEM_STOP,
            metadata={"source": "agent_server"},
        )
        await self._record_and_check(event)

    async def on_before_system_prompt_build(self, context: Any = None, **kwargs: Any) -> None:
        """系统提示构建审计（记录会话启动标记）."""
        event = AuditEvent(
            event_type=AuditEventType.SESSION_START,
            metadata={"source": "agent_server", "context_type": type(context).__name__ if context else "none"},
        )
        await self._record_and_check(event)

    # ── 通用 Hook 回调 ──────────────────────────────────────────

    async def on_generic_event(self, event_name: str, context: Any = None, **kwargs: Any) -> None:
        """通用事件回调 — 将未专门处理的 Hook 事件转换为审计事件.

        用于应对未来新增的 Hook 事件，保证审计覆盖完整。
        """
        event_type = HOOK_EVENT_MAP.get(event_name, AuditEventType.SYSTEM_START)

        session_id, channel_id, request_id, _, params = _extract_context(context)

        event = AuditEvent(
            event_type=event_type,
            session_id=session_id,
            channel_id=channel_id,
            request_id=request_id,
            metadata={
                "hook_event_name": event_name,
                "context_type": type(context).__name__ if context else "none",
                "kwargs_keys": list(kwargs.keys()),
            },
        )
        await self._record_and_check(event)

    # ── 内部方法 ────────────────────────────────────────────────

    async def _record_and_check(self, event: AuditEvent) -> None:
        """写入日志 + 告警检查（核心流水线）."""
        if not self._config.enabled:
            return

        try:
            await self._store.write_event(event)
        except Exception as exc:
            logger.warning("[Audit] write_event failed: %s", exc)
            return

        try:
            alerts = await self._alert_engine.check_event(event)
            # 告警已由 AlertEngine 自动持久化
        except Exception as exc:
            logger.warning("[Audit] check_event alert failed: %s", exc)

    def get_session_tracker(self) -> dict[str, dict[str, Any]]:
        """获取会话追踪器（供 CLI 查询使用）."""
        return self._session_tracker


# ── 上下文提取辅助 ──────────────────────────────────────────────

def _extract_context(context: Any) -> tuple[str | None, str | None, str | None, str | None, dict]:
    """从 Hook context 中提取通用字段.

    支持 GatewayChatHookContext、AgentServerChatHookContext、dict 等多种格式。
    """
    if context is None:
        return None, None, None, None, {}

    # dataclass 类型（有 to_dict 方法）
    if hasattr(context, "to_dict") and callable(context.to_dict):
        d = context.to_dict()
        return (
            d.get("session_id"),
            d.get("channel_id"),
            d.get("request_id"),
            d.get("req_method"),
            d.get("params", {}),
        )

    # dict 类型
    if isinstance(context, dict):
        return (
            context.get("session_id"),
            context.get("channel_id"),
            context.get("request_id"),
            context.get("req_method"),
            context.get("params", {}),
        )

    # 有属性的对象
    return (
        getattr(context, "session_id", None),
        getattr(context, "channel_id", None),
        getattr(context, "request_id", None),
        getattr(context, "req_method", None),
        getattr(context, "params", {}),
    )


def _extract_memory_context(context: Any) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """从 MemoryHookContext 提取字段."""
    if context is None:
        return None, None, None, None, None

    if hasattr(context, "to_dict") and callable(context.to_dict):
        d = context.to_dict()
        return (
            d.get("session_id"),
            d.get("channel_id"),
            d.get("request_id"),
            d.get("agent_name"),
            d.get("workspace_dir"),
        )

    if isinstance(context, dict):
        return (
            context.get("session_id"),
            context.get("channel_id"),
            context.get("request_id"),
            context.get("agent_name"),
            context.get("workspace_dir"),
        )

    return (
        getattr(context, "session_id", None),
        getattr(context, "channel_id", None),
        getattr(context, "request_id", None),
        getattr(context, "agent_name", None),
        getattr(context, "workspace_dir", None),
    )


def _extract_memory_metadata(context: Any) -> dict:
    """从 MemoryHookContext 提取 metadata 字段."""
    if context is None:
        return {}

    if hasattr(context, "metadata"):
        meta = context.metadata
        if isinstance(meta, dict):
            return meta

    if isinstance(context, dict):
        return context.get("metadata", {})

    return {}
