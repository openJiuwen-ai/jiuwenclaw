# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""RailHookEmitter —— 在 AgentServer 侧触发无 rail 回调的 Rail 事件 hooks.

PermissionRequest / PermissionDenied / SubagentStart / SubagentStop 这 4 个
事件归类为 Rail 事件（hooks_config._AGENT_RAIL_EVENTS），但 openjiuwen 的
AgentCallbackEvent 没有对应回调，PermissionInterruptRail 与子 agent 生命周期
也无 emit 点。本模块提供共享发射器，供权限回调与子 agent spawn/stop 调用点
直接触发，使配置在这些事件下的 hook 真正生效。

设计：fire-and-forget，永不阻塞调用方，失败仅记录日志（与 GatewayHookHandler
契约一致）。
"""

from __future__ import annotations

import asyncio
import logging

from jiuwenswarm.common.hooks_config import HookEvent, HooksConfig, load_hooks_config
from jiuwenswarm.server.hooks.executor import HookExecutor

logger = logging.getLogger(__name__)


class RailHookEmitter:
    """AgentServer 侧共享的 hooks 发射器（用于无 rail 回调的事件）."""

    def __init__(self, hooks_config: HooksConfig | None = None):
        self._config: HooksConfig | None = hooks_config
        self._executor = HookExecutor()

    @property
    def config(self) -> HooksConfig:
        if self._config is None:
            self._config = load_hooks_config()
        return self._config

    def reload(self, hooks_config: HooksConfig | None = None) -> None:
        """重置配置缓存；传 None 则下次访问时重新从 config.yaml 加载."""
        self._config = hooks_config

    async def fire(
        self,
        event: HookEvent,
        query: str = "",
        hook_input: dict | None = None,
        session_id: str = "",
        timeout: int = 30,
    ) -> None:
        """异步触发指定事件的全部匹配 hook."""
        cfg = self.config
        if cfg.disable_all_hooks:
            return
        hook_configs = cfg.match(event.value, query=query)
        if not hook_configs:
            return
        for c in hook_configs:
            c.setdefault("timeout", timeout)
        payload = dict(hook_input or {})
        payload.setdefault("event", event.value)
        payload.setdefault("session_id", session_id)
        logger.info(
            "RailHookEmitter: fire %s hooks query=%s count=%d",
            event.value, query, len(hook_configs),
        )
        try:
            await self._executor.run_all(hook_configs, hook_input=payload)
        except Exception as e:
            logger.warning("RailHookEmitter: %s hook failed: %s", event.value, e)

    def trigger(
        self,
        event: HookEvent,
        query: str = "",
        hook_input: dict | None = None,
        session_id: str = "",
    ) -> None:
        """同步触发（fire-and-forget），永不阻塞调用方.

        需在运行中的事件循环内调用（async 上下文）；否则降级为忽略并记录 debug。
        """
        try:
            asyncio.create_task(
                self.fire(
                    event, query=query, hook_input=hook_input, session_id=session_id,
                )
            )
        except RuntimeError:
            logger.debug(
                "RailHookEmitter.trigger: no running loop for %s, skipped", event.value,
            )


# 模块级单例：懒加载 HooksConfig（从 AgentServer 的 get_config() 读取）
_emitter: RailHookEmitter | None = None


def get_rail_hook_emitter() -> RailHookEmitter:
    global _emitter
    if _emitter is None:
        _emitter = RailHookEmitter()
    return _emitter
