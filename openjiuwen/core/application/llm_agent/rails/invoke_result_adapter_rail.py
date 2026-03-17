# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""AFTER_INVOKE rail that converts ReActAgent raw result to legacy LLMAgent schema.

The ReActAgent.invoke() return value is a raw dict with keys like
``result_type``, ``workflow_execution_state``, ``component_ids``, ``output``.
Legacy callers (via LLMAgentRefactor) expect a different shape:
  - interrupt -> List[OutputSchema]
  - answer/error -> {"output": str, "result_type": str}

This rail performs the conversion inside the AFTER_INVOKE hook and writes
the adapted result into ``ctx.extra["invoke_result"]``.  After the
lifecycle block ends, ReActAgent.invoke() returns that value instead of
the raw dict.
"""
from typing import Dict, List, Union

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, AgentRail

#: Key used in ctx.extra to pass the adapted result back to the caller.
INVOKE_RESULT_KEY = "invoke_result"


def _convert_dict_to_schema(result: dict) -> Union[list, dict]:
    """Convert ReActAgent raw result dict to legacy LLMAgent output format."""
    result_type = result.get("result_type", "")
    if result_type == "interrupt":
        workflow_state = result.get("workflow_execution_state")
        component_ids = result.get("component_ids", [])
        pending_id = component_ids[0] if component_ids else None
        schemas = (
            workflow_state.result
            if workflow_state is not None
               and isinstance(getattr(workflow_state, "result", None), list)
            else []
        )
        output_schemas = []
        for schema in schemas:
            if (pending_id is None
                    or (hasattr(schema, "payload")
                        and hasattr(schema.payload, "id")
                        and schema.payload.id == pending_id)):
                output_schemas.append(schema)
        return output_schemas
    else:
        return {"output": result.get("output", ""), "result_type": result_type}


class InvokeResultAdapterRail(AgentRail):
    """AFTER_INVOKE rail: convert ReActAgent raw result to legacy schema.

    The converted result is written to ``ctx.extra[INVOKE_RESULT_KEY]``
    so the caller can read it after the lifecycle block completes.

    Priority is set to 90 (lower than MemoryRail's 50) so that MemoryRail
    reads the raw result first before this rail transforms it.
    """

    priority = 90

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        raw_result = getattr(ctx.inputs, "result", None)
        if raw_result is None:
            return
        ctx.extra[INVOKE_RESULT_KEY] = _convert_dict_to_schema(raw_result)
