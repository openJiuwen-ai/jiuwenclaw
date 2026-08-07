# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""CsplSentinelRail - CSPL tool input/output security scanning.

Ported from xy_channel sentinel_hook.ts; output REJECT uses request_force_finish
instead of steer-context.ts injection.
"""

from __future__ import annotations

import uuid

from openjiuwen.core.foundation.llm import ToolMessage
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.rails.cspl.client import CsplConfig, scan
from jiuwenswarm.agents.harness.common.rails.cspl.constants import (
    ABORT_MESSAGE,
    TOOL_INPUT_REJECT_TEMPLATE,
    TOOL_INPUT_SCAN,
    TOOL_OUTPUT_SCAN,
)
from jiuwenswarm.agents.harness.common.rails.cspl.scanners import (
    build_tool_input_payload,
    build_tool_output_payload,
)
from jiuwenswarm.common.utils import logger

_SESSION_ID_KEY = "cspl_session_id"


class CsplSentinelRail(DeepAgentRail):
    """CSPL Sentinel — scan tool input before execution and tool output after."""

    priority: int = 78

    def __init__(self, config: CsplConfig | None = None) -> None:
        super().__init__()
        self._config = config or CsplConfig.load()

    @staticmethod
    def _resolve_session_id(ctx: AgentCallbackContext) -> str:
        existing = ctx.extra.get(_SESSION_ID_KEY)
        if isinstance(existing, str) and existing:
            return existing

        for attr in ("session_id", "conversation_id"):
            value = getattr(ctx, attr, None)
            if isinstance(value, str) and value:
                sid = value.replace("-", "")
                ctx.extra[_SESSION_ID_KEY] = sid
                return sid

        inputs = ctx.inputs
        for attr in ("conversation_id", "session_id"):
            value = getattr(inputs, attr, None)
            if isinstance(value, str) and value:
                sid = value.replace("-", "")
                ctx.extra[_SESSION_ID_KEY] = sid
                return sid

        generated = uuid.uuid4().hex
        ctx.extra[_SESSION_ID_KEY] = generated
        return generated

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._config.enabled or not self._config.scan_tool_input:
            return

        tool_name = ctx.inputs.tool_name or ""
        if not tool_name:
            return

        payload = build_tool_input_payload(tool_name, ctx.inputs.tool_args)
        if not payload:
            logger.debug(
                "[CsplSentinelRail] TOOL_INPUT skip tool=%s (no scannable payload, args=%r)",
                tool_name,
                ctx.inputs.tool_args,
            )
            return

        session_id = self._resolve_session_id(ctx)
        logger.info(
            "[CsplSentinelRail] TOOL_INPUT scan start tool=%s session=%s",
            tool_name,
            session_id,
        )
        try:
            result = await scan(payload, TOOL_INPUT_SCAN, session_id, self._config)
        except Exception as exc:
            logger.warning(
                "[CsplSentinelRail] TOOL_INPUT scan error tool=%s: %s", tool_name, exc
            )
            if not self._config.fail_open:
                message = TOOL_INPUT_REJECT_TEMPLATE.format(tool_name=tool_name)
                self._reject_tool(ctx, message)
            return

        logger.info(
            "[CsplSentinelRail] TOOL_INPUT scan done tool=%s result=%s",
            tool_name,
            result,
        )
        if result == "REJECT":
            message = TOOL_INPUT_REJECT_TEMPLATE.format(tool_name=tool_name)
            logger.warning("[CsplSentinelRail] TOOL_INPUT REJECT, blocking tool=%s", tool_name)
            self._reject_tool(ctx, message)

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        if not self._config.enabled or not self._config.scan_tool_output:
            return

        tool_name = ctx.inputs.tool_name or ""
        if not tool_name:
            return

        payload = build_tool_output_payload(tool_name, ctx.inputs.tool_result)
        if not payload:
            return

        session_id = self._resolve_session_id(ctx)
        try:
            result = await scan(payload, TOOL_OUTPUT_SCAN, session_id, self._config)
        except Exception as exc:
            logger.warning(
                "[CsplSentinelRail] TOOL_OUTPUT scan error tool=%s: %s", tool_name, exc
            )
            if not self._config.fail_open:
                ctx.request_force_finish({"output": ABORT_MESSAGE, "result_type": "answer"})
            return

        if result == "REJECT":
            logger.warning(
                "[CsplSentinelRail] TOOL_OUTPUT REJECT, force finishing tool=%s", tool_name
            )
            ctx.request_force_finish({"output": ABORT_MESSAGE, "result_type": "answer"})

    @staticmethod
    def _reject_tool(ctx: AgentCallbackContext, message: str) -> None:
        tool_call = ctx.inputs.tool_call
        tool_call_id = tool_call.id if tool_call else ""
        ctx.extra["_skip_tool"] = True
        ctx.inputs.tool_result = message
        ctx.inputs.tool_msg = ToolMessage(content=message, tool_call_id=tool_call_id)


__all__ = ["CsplSentinelRail"]
