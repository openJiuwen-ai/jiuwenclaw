#!/usr/bin/env python
"""自定义 ExtensionConfig Rail 示例：通过环境变量非侵入式注册。

使用方式：
    export AGENT_EXTRA_RAILS=examples.custom_extension_rail
    # 然后启动 AgentServer

本模块实现了一个简单的自定义 Rail，用于消费 extension_config 中的配置，
并在 before_tool_call 时根据配置决定是否拦截或修改工具调用。
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class CustomExtensionConfigRail(DeepAgentRail):
    """示例：自定义 Rail，消费 extension_config 并执行业务逻辑。

    假设 extension_config 包含如下结构::

        [
            {
                "template_id": "tpl-001",
                "template_name": "限制工具调用",
                "component": "agent_server",
                "hook_type": "pre_request",
                "hook_config": {
                    "handler": "hooks.limit_tools",
                    "params": {"allowed_tools": ["bash", "read_file"]}
                }
            }
        ]

    本 Rail 在 before_tool_call 时检查当前工具是否在允许列表内。
    """

    priority = 50  # 在 ExtensionConfigDebugRail (45) 之后，TaskExecutionRail (85) 之前

    @staticmethod
    def _get_extension_config(ctx: AgentCallbackContext) -> list[dict[str, Any]] | None:
        """从 AgentCallbackContext.inputs 提取 extension_config。"""
        if not hasattr(ctx, "inputs"):
            return None
        inputs = ctx.inputs
        if isinstance(inputs, dict):
            ext_config = inputs.get("extension_config")
            if isinstance(ext_config, list):
                return ext_config
        return None

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        """invoke 开始时执行初始化逻辑。"""
        ext_config = self._get_extension_config(ctx)
        if ext_config:
            logger.info(
                "[CustomExtensionConfigRail] before_invoke: loaded %d config(s)",
                len(ext_config),
            )
            for cfg in ext_config:
                hook_config = cfg.get("hook_config", {})
                params = hook_config.get("params", {})
                logger.info(
                    "[CustomExtensionConfigRail]   - handler=%s params=%s",
                    hook_config.get("handler"), params,
                )
        else:
            logger.debug("[CustomExtensionConfigRail] before_invoke: no extension_config")

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """工具调用前执行权限检查等业务逻辑。"""
        ext_config = self._get_extension_config(ctx)
        if not ext_config:
            return

        # 示例：从 extension_config 中提取 allowed_tools 并检查
        for cfg in ext_config:
            hook_config = cfg.get("hook_config", {})
            params = hook_config.get("params", {})
            allowed_tools = params.get("allowed_tools")
            if allowed_tools:
                # 实际业务逻辑：检查当前 tool_name 是否在 allowed_tools 中
                # 这里仅打印日志演示
                logger.info(
                    "[CustomExtensionConfigRail] before_tool_call: "
                    "allowed_tools=%s",
                    allowed_tools,
                )

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """工具调用后执行清理逻辑。"""
        pass


def register_rails() -> list[DeepAgentRail]:
    """注册入口：返回要挂载到 DeepAgent 的 Rail 实例列表。

    框架会通过 importlib 动态导入本模块并调用此函数。
    """
    return [CustomExtensionConfigRail()]
