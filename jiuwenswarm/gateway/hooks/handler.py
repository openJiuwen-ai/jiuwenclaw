# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""GatewayHookHandler —— 在 Gateway 层执行 session / 生命周期类 hooks."""

from __future__ import annotations

import logging
from pathlib import Path

from jiuwenswarm.common.hooks_config import HooksConfig, HookEvent
from jiuwenswarm.server.hooks.executor import HookExecutor

logger = logging.getLogger(__name__)


class GatewayHookHandler:
    """Gateway 层的 hooks 处理器.

    Gateway hooks 特点：
    - 同步串行执行（保证 session 生命周期顺序）
    - 超时默认 10s（短于 AgentServer 30s，避免阻塞消息转发）
    - 失败永不阻塞用户请求（只做日志记录）
    """

    def __init__(self, hooks_config: HooksConfig):
        self._config = hooks_config
        self._executor = HookExecutor()
        self._gateway_timeout = 10
        self._active_sessions: set[str] = set()

    async def on_session_start(self, session_id: str, source: str = "startup") -> None:
        """会话开始时触发 SessionStart hooks."""
        if session_id in self._active_sessions:
            return
        self._active_sessions.add(session_id)

        hook_configs = self._config.match(
            HookEvent.SESSION_START.value, query=source,
        )
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        logger.info(
            "GatewayHookHandler: fire SessionStart hooks session=%s source=%s count=%d",
            session_id, source, len(hook_configs),
        )
        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "SessionStart",
                    "source": source,
                    "session_id": session_id,
                    "cwd": str(Path.cwd()),
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: SessionStart hook failed: %s", e)

    async def on_user_prompt_submit(self, session_id: str, prompt: str) -> None:
        """用户提交消息时触发 UserPromptSubmit hooks."""
        hook_configs = self._config.match(HookEvent.USER_PROMPT_SUBMIT.value)
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "UserPromptSubmit",
                    "prompt": prompt,
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: UserPromptSubmit hook failed: %s", e)

    async def on_session_end(self, session_id: str, reason: str = "clear") -> None:
        """会话结束时触发 SessionEnd hooks."""
        self._active_sessions.discard(session_id)

        hook_configs = self._config.match(
            HookEvent.SESSION_END.value, query=reason,
        )
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "SessionEnd",
                    "reason": reason,
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: SessionEnd hook failed: %s", e)

    async def on_notification(self, notification_type: str, message: str,
                              session_id: str = "") -> None:
        """通知发送时触发 Notification hooks."""
        hook_configs = self._config.match(
            HookEvent.NOTIFICATION.value, query=notification_type,
        )
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "Notification",
                    "notification_type": notification_type,
                    "message": message,
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: Notification hook failed: %s", e)

    async def on_config_change(self, changed_keys: list[str] | None = None,
                               session_id: str = "") -> None:
        """配置发生变更时触发 ConfigChange hooks.

        changed_keys 为本次变更的 yaml 配置路径列表（如 ["models.defaults"]），
        matcher 可按 key 精确匹配或用 "*" 匹配任意变更。
        """
        keys = list(changed_keys or [])
        # 任一变更 key 命中即触发；按 hook 配置对象 id 去重，避免同一
        # matcher（如 "*"）对多 key 重复触发。
        hook_configs: list[dict] = []
        seen: set[int] = set()
        if keys:
            for k in keys:
                for cfg in self._config.match(HookEvent.CONFIG_CHANGE.value, query=k):
                    cid = id(cfg)
                    if cid not in seen:
                        seen.add(cid)
                        hook_configs.append(cfg)
        else:
            hook_configs = self._config.match(HookEvent.CONFIG_CHANGE.value)
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        logger.info(
            "GatewayHookHandler: fire ConfigChange hooks keys=%s count=%d",
            keys, len(hook_configs),
        )
        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "ConfigChange",
                    "changed_keys": keys,
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: ConfigChange hook failed: %s", e)

    async def on_instructions_loaded(self, source: str = "AGENTS.md",
                                     session_id: str = "") -> None:
        """加载指令文件（AGENTS.md / 系统提示等）时触发 InstructionsLoaded hooks."""
        hook_configs = self._config.match(
            HookEvent.INSTRUCTIONS_LOADED.value, query=source,
        )
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "InstructionsLoaded",
                    "source": source,
                    "session_id": session_id,
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: InstructionsLoaded hook failed: %s", e)

    async def on_setup(self, source: str = "startup", session_id: str = "") -> None:
        """工作区/环境初始化时触发 Setup hooks（如首次启动、工作区初始化）."""
        hook_configs = self._config.match(
            HookEvent.SETUP.value, query=source,
        )
        if not hook_configs:
            return

        for cfg in hook_configs:
            cfg.setdefault("timeout", self._gateway_timeout)

        logger.info(
            "GatewayHookHandler: fire Setup hooks source=%s count=%d",
            source, len(hook_configs),
        )
        try:
            await self._executor.run_all(
                hook_configs,
                hook_input={
                    "event": "Setup",
                    "source": source,
                    "session_id": session_id,
                    "cwd": str(Path.cwd()),
                },
            )
        except Exception as e:
            logger.warning("GatewayHookHandler: Setup hook failed: %s", e)