# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""McpProjectIdRail -- 在 MCP 工具调用前自动注入 project_id 隔离键。

GaussPD 记忆系统需要 project_id 按 project 隔离数据，但 MCP stdio server
是长驻进程，无法通过环境变量动态切换 project_id。此 Rail 从当前 session
的元数据中获取 project_id，在 MCP 工具调用前自动注入到 tool_args 中，
确保 MCP server 始终拿到正确的 project_id。

优先级 55：在 StreamEventRail(80) 与 UserHookRail(60) 之后执行。
StreamEventRail 先清理 call_goal 并发送工具调用事件，用户 hook 随后处理，
本 Rail 最后注入 project_id。
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.common.utils import logger

if TYPE_CHECKING:
    pass


class McpProjectIdRail(DeepAgentRail):
    """自动为 MCP 工具调用注入 project_id 隔离键。

    MCP 工具 ID 格式为 ``{server_id}.{server_name}.{tool_name}``（含 ``.``），
    由 ``ToolMgr.generate_mcp_tool_id()`` 生成。据此识别 MCP 工具调用并注入
    project_id。内置工具名称不含 ``.``，因此 ``"." in tool_name`` 可区分。
    """

    priority = 55

    # project_id 的参数键名（注入到 MCP 工具参数中）
    PROJECT_ID_KEY = "project_id"

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """在 MCP 工具调用前注入 project_id。"""
        tool_name = str(getattr(ctx.inputs, "tool_name", "") or "").strip()

        # 仅处理 MCP 工具调用（名称含 ``.``）
        if "." not in tool_name:
            return

        tool_args = getattr(ctx.inputs, "tool_args", None)
        if not isinstance(tool_args, dict):
            return

        # 已有 project_id 则不覆盖（可能是用户 hook 显式设置）
        if self.PROJECT_ID_KEY in tool_args:
            return

        # 从 session 元数据获取 project_id
        project_id = self._resolve_project_id(ctx)
        if not project_id:
            return

        tool_args[self.PROJECT_ID_KEY] = project_id
        ctx.inputs.tool_args = tool_args

        # 同步修改 ToolCall.arguments（与 StreamEventRail 同模式，
        # 确保 AbilityManager._execute_single_tool_call 从 ToolCall
        # 重新解析参数时也能拿到 project_id）
        tc = getattr(ctx.inputs, "tool_call", None)
        if tc is not None:
            try:
                existing_args = getattr(tc, "arguments", None)
                if isinstance(existing_args, dict):
                    existing_args[self.PROJECT_ID_KEY] = project_id
                else:
                    tc.arguments = tool_args
            except (AttributeError, TypeError) as exc:
                logger.warning(
                    "[McpProjectIdRail] rewrite ToolCall.arguments failed: %s", exc
                )

        logger.debug(
            "[McpProjectIdRail] injected project_id=%s for tool=%s",
            project_id, tool_name,
        )

    # ------------------------------------------------------------------
    # Internal: session_id 解析
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str | None:
        """从 AgentCallbackContext 提取 session_id。

        参考 CsplSentinelRail._resolve_session_id() 和
        CodeTaskPlanningRail._session_id() 的模式：
        - ctx.session.get_session_id()（DeepAgent 注入）
        - ctx.session_id / ctx.conversation_id（部分上下文）
        - ctx.inputs.session_id（工具调用上下文）
        """
        # 路径 1: ctx.session 对象
        session = getattr(ctx, "session", None)
        if session is not None and hasattr(session, "get_session_id"):
            sid = session.get_session_id()
            if isinstance(sid, str) and sid.strip():
                return sid.strip()
        # 路径 2: ctx 上的 session_id / conversation_id 属性
        for attr in ("session_id", "conversation_id"):
            value = getattr(ctx, attr, None)
            if isinstance(value, str) and value.strip():
                return value.strip()
        # 路径 3: ctx.inputs 上的 session_id
        inputs = getattr(ctx, "inputs", None)
        for attr in ("session_id", "conversation_id"):
            value = getattr(inputs, attr, None) if inputs else None
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None

    # ------------------------------------------------------------------
    # Internal: project_id 解析
    # ------------------------------------------------------------------

    def _resolve_project_id(self, ctx: AgentCallbackContext) -> str:
        """从 session 元数据获取 project_id。

        优先级：
        1. ctx 关联的 session_metadata["project_id"]
        2. 运行时 contextvar 中的 cron metadata
        3. 环境变量 GSPD_CELIAWORK_PROJECT_ID（兜底）
        """
        # 路径 1: session 元数据
        session_id = self._resolve_session_id(ctx)
        if session_id:
            try:
                from jiuwenswarm.server.runtime.session.session_metadata import (
                    get_session_metadata,
                )
                metadata = get_session_metadata(session_id)
                if isinstance(metadata, dict):
                    pid = str(metadata.get("project_id", "") or "").strip()
                    if pid:
                        return pid
            except Exception as exc:
                logger.debug(
                    "[McpProjectIdRail] session metadata lookup failed: %s", exc
                )

        # 路径 2: cron 工具的 contextvar metadata
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
                _CRON_TOOL_METADATA,
            )
            cron_meta = _CRON_TOOL_METADATA.get()
            if isinstance(cron_meta, dict):
                pid = str(cron_meta.get("project_id", "") or "").strip()
                if pid:
                    return pid
        except (ImportError, AttributeError):
            pass

        # 路径 3: 环境变量兜底
        return os.getenv("GSPD_CELIAWORK_PROJECT_ID", "").strip()


__all__ = ["McpProjectIdRail"]
