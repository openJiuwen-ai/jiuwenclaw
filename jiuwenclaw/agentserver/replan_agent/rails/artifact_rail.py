# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Artifact detection rail for RePlanAgent tool calls."""

from __future__ import annotations

import logging
import time
from typing import Any

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, ToolCallInputs

logger = logging.getLogger(__name__)


class RePlanArtifactRail:
    """复用 DeepAgent artifact_emitter，在 RePlan 工具调用后检测产物。"""

    priority = 90

    def __init__(self, executor: Any) -> None:
        self._executor = executor

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        setattr(ctx, '_tool_start_time', time.time())

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if ctx.session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return

        from jiuwenclaw.agentserver.deep_agent.artifact_emitter import (
            ArtifactEmitContext,
            emit_artifact_generated,
        )

        detect_start = time.perf_counter()
        artifact_ctx = ArtifactEmitContext(
            session=ctx.session,
            tool_result=ctx.inputs.tool_result,
            tool_name=ctx.inputs.tool_name,
            workspace_base=self._executor.get_workspace_base_path(),
            tool_start_time=getattr(ctx, '_tool_start_time', None),
            task_id=self._executor.current_task_id(),
            tool_args=getattr(ctx.inputs, "tool_args", None),
            log_prefix="[RePlanArtifact]",
        )
        logger.info(
            "[RePlanArtifact] Detect start: tool=%s session_id=%s",
            ctx.inputs.tool_name,
            ctx.session.get_session_id(),
        )
        emitted = await emit_artifact_generated(artifact_ctx)
        logger.info(
            "[RePlanArtifact] Detect done: tool=%s session_id=%s emitted=%s elapsed_ms=%d",
            ctx.inputs.tool_name,
            ctx.session.get_session_id(),
            emitted,
            int((time.perf_counter() - detect_start) * 1000),
        )
