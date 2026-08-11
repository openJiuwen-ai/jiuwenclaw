# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Artifact detection rail for SkillTurbo tool calls（预留，当前未挂入 executor）。

TaskExecutionRail 使用独立的
image artifact hook。本 rail 暂不注册到 ``SkillTurboExecutor._rails``，避免每次
``use_tool`` 空跑。若后续需要 turbo 产物上报，应接目标 TaskExecutionRail hook，
而非迁源 artifact_emitter。
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

logger = logging.getLogger(__name__)


class SkillTurboArtifactRail:
    """预留：SkillTurbo 工具调用后产物检测（当前未启用）。"""

    priority = 90

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        return

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return
        logger.debug(
            "[SkillTurboArtifact] reserved/unwired; skip tool=%s",
            ctx.inputs.tool_name,
        )
