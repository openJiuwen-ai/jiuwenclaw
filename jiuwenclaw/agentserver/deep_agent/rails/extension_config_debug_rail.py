# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""ExtensionConfigDebugRail — 用于验证扩展配置端到端联调的调试 Rail。

在企业级配置（Enterprise Config）下发链路中，Manager 通过三层匹配策略将
``extension_config`` 下发到 Gateway，Gateway 再随请求透传到 AgentServer。
本 Rail 在工具调用前打印日志，验证扩展配置是否成功到达 AgentServer。
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)


class ExtensionConfigDebugRail(DeepAgentRail):
    """在工具调用前打印扩展配置日志的调试 Rail。"""

    priority = 45  # 比 TaskExecutionRail (85) 更优先执行

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
        """每次 invoke 开始时打印扩展配置摘要。"""
        ext_config = self._get_extension_config(ctx)
        if ext_config:
            logger.info(
                "[ExtensionConfigDebugRail] before_invoke: extension_config present, "
                "count=%d, names=%s",
                len(ext_config),
                [c.get("name") or c.get("template_name") or c.get("id") for c in ext_config],
            )
        else:
            logger.info("[ExtensionConfigDebugRail] before_invoke: no extension_config found")

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """工具调用前打印扩展配置日志。"""
        ext_config = self._get_extension_config(ctx)
        if ext_config:
            logger.info(
                "[ExtensionConfigDebugRail] before_tool_call: extension_config present, "
                "count=%d, configs=%s",
                len(ext_config),
                ext_config,
            )
        else:
            logger.info("[ExtensionConfigDebugRail] before_tool_call: no extension_config found")
