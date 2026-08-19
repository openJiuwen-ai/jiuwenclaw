# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""UserHookRail —— 将用户配置的 hooks 以 Rail 形态注册到 DeepAgent，拦截工具调用和 Agent 生命周期."""

from __future__ import annotations

import logging

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.hooks_config import HooksConfig, HookEvent
from jiuwenswarm.server.hooks.executor import HookExecutor

logger = logging.getLogger(__name__)


class UserHookRail(DeepAgentRail):
    """用户配置的 hooks 执行引擎.

    Priority=60: 在 SecurityRail (80) 之后，JiuSwarmStreamEventRail (50) 之前。
    确保安全检查先于用户 hook，用户 hook 先于流式事件发送。
    """

    priority = 60

    def __init__(self, hooks_config: HooksConfig):
        super().__init__()
        self._config = hooks_config
        self._executor = HookExecutor()

    # 与 StreamEventRail 一致的会话键：主 agent 的 before_invoke 阶段
    # StreamEventRail 会把 conversation_id 写入 ctx.extra（子 agent 不触发
    # 自己的 before_invoke，此时 ctx.session 也为 None，最终回退为空串）。
    # 详见 StreamEventRail.before_invoke / StreamEventRail._SID_KEY。
    _SESSION_ID_KEY = "__jiuwenswarm_session_id__"

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str:
        """从回调 ctx 中解析会话 id.

        AgentCallbackContext 没有 session_id 字段（只有 session 对象），
        ToolCallInputs / ModelCallInputs 也没有 conversation_id，因此直接
        getattr(ctx, "session_id", "") 恒返回空串。这里按优先级回退：
          1. StreamEventRail 在 before_invoke 写入 ctx.extra 的会话 id
             （主 agent；"default" 哨兵值视为未设置）；
          2. ctx.session.get_session_id()（agent 内部 uuid，兜底）。
        """
        extra = getattr(ctx, "extra", None)
        sid = extra.get(UserHookRail._SESSION_ID_KEY, "") if isinstance(extra, dict) else ""
        if isinstance(sid, str) and sid and sid != "default":
            return sid
        session = getattr(ctx, "session", None)
        if session is not None:
            try:
                get_session_id = getattr(session, "get_session_id", None)
                if callable(get_session_id):
                    got = get_session_id()
                    if isinstance(got, str) and got:
                        return got
            except Exception:
                logger.debug("UserHookRail: get_session_id() failed", exc_info=True)
        return ""

    # ---- PreToolUse: BEFORE_TOOL_CALL ----

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name or ""
        tool_args = ctx.inputs.tool_args

        hook_configs = self._config.match(
            HookEvent.PRE_TOOL_USE.value, query=tool_name,
        )
        if not hook_configs:
            return

        results = await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_args,
                "session_id": self._resolve_session_id(ctx),
            },
        )

        for r in results:
            if r.outcome == "blocking":
                ctx.extra["_skip_tool"] = True
                ctx.extra["_hook_feedback"] = r.error
                logger.info(
                    "UserHookRail: PreToolUse BLOCKED tool=%s reason=%s",
                    tool_name, r.error,
                )
                return
            if r.modified_input:
                ctx.inputs.tool_args = r.modified_input
                new_name = r.modified_input.get("_tool_name")
                if new_name:
                    ctx.inputs.tool_name = new_name
                logger.info(
                    "UserHookRail: PreToolUse modified input for tool=%s", tool_name,
                )
            if r.additional_context:
                existing = ctx.extra.get("_hook_additional_context", "")
                ctx.extra["_hook_additional_context"] = existing + "\n" + r.additional_context

    # ---- PostToolUse: AFTER_TOOL_CALL ----

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name or ""

        hook_configs = self._config.match(
            HookEvent.POST_TOOL_USE.value, query=tool_name,
        )
        if not hook_configs:
            return

        results = await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": ctx.inputs.tool_args,
                "tool_result": ctx.inputs.tool_result,
                "session_id": self._resolve_session_id(ctx),
            },
        )

        for r in results:
            if r.outcome == "blocking":
                ctx.extra["_post_tool_hook_feedback"] = r.error
                logger.info(
                    "UserHookRail: PostToolUse BLOCKED continuation tool=%s reason=%s",
                    tool_name, r.error,
                )
            if r.additional_context:
                current = ctx.inputs.tool_result or ""
                ctx.inputs.tool_result = current + "\n[Hook 发现]: " + r.additional_context

    # ---- PostToolUseFailure: ON_TOOL_EXCEPTION ----

    async def on_tool_exception(self, ctx: AgentCallbackContext) -> None:
        tool_name = ctx.inputs.tool_name or ""

        hook_configs = self._config.match(
            HookEvent.POST_TOOL_USE_FAILURE.value, query=tool_name,
        )
        if not hook_configs:
            return

        await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "PostToolUseFailure",
                "tool_name": tool_name,
                "tool_input": ctx.inputs.tool_args,
                "error": str(getattr(ctx, "exception", "")),
                "session_id": self._resolve_session_id(ctx),
            },
        )

    # ---- Stop: AFTER_INVOKE ----

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        hook_configs = self._config.match(HookEvent.STOP.value)
        if not hook_configs:
            return

        results = await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "Stop",
                "final_response": getattr(ctx.inputs, "result", None),
                "session_id": self._resolve_session_id(ctx),
            },
        )

        for r in results:
            if r.outcome == "blocking":
                ctx.extra["_stop_hook_feedback"] = r.error
                logger.info("UserHookRail: Stop hook feedback: %s", r.error[:200])

    # ---- BeforeModelCall: BEFORE_MODEL_CALL ----
    # 注：模型调用在 Rail 层无“跳过本次调用”的安全短路机制
    # （不同于 before_tool_call 的 _skip_tool），因此这里仅作为非阻塞观察者：
    # 执行用户 hook、汇集 additional_context；若 hook 返回 blocking 仅记录日志
    # 与 feedback，不强制终止，避免破坏模型调用流程。

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        hook_configs = self._config.match(HookEvent.BEFORE_MODEL_CALL.value)
        if not hook_configs:
            return

        inputs = ctx.inputs
        messages = getattr(inputs, "messages", None) or []
        tools = getattr(inputs, "tools", None) or []

        results = await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "BeforeModelCall",
                "messages": messages,
                "tools": tools,
                "session_id": self._resolve_session_id(ctx),
            },
        )

        for r in results:
            if r.outcome == "blocking":
                ctx.extra["_before_model_hook_feedback"] = r.error
                logger.info(
                    "UserHookRail: BeforeModelCall hook blocked (advisory) reason=%s",
                    r.error,
                )
            if r.additional_context:
                existing = ctx.extra.get("_hook_additional_context", "")
                ctx.extra["_hook_additional_context"] = existing + "\n" + r.additional_context

    # ---- AfterModelCall: AFTER_MODEL_CALL ----

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        hook_configs = self._config.match(HookEvent.AFTER_MODEL_CALL.value)
        if not hook_configs:
            return

        inputs = ctx.inputs
        messages = getattr(inputs, "messages", None) or []
        response = getattr(inputs, "response", None)

        results = await self._executor.run_all(
            hook_configs,
            hook_input={
                "event": "AfterModelCall",
                "messages": messages,
                "response": response,
                "session_id": self._resolve_session_id(ctx),
            },
        )

        for r in results:
            if r.outcome == "blocking":
                ctx.extra["_after_model_hook_feedback"] = r.error
                logger.info(
                    "UserHookRail: AfterModelCall hook blocked (advisory) reason=%s",
                    r.error,
                )
            if r.additional_context:
                existing = ctx.extra.get("_hook_additional_context", "")
                ctx.extra["_hook_additional_context"] = existing + "\n" + r.additional_context
