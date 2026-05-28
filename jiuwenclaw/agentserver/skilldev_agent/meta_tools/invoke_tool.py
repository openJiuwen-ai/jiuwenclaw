# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified invoke meta-tool: routes to agent or external function call by funcName."""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Tool, ToolCard

from jiuwenclaw.agentserver.skilldev_agent.meta_tools.agent_as_a_tool import AgentAsATool
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.external_tool_registry import (
    load_external_tools,
)
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.function_call_tool import (
    _function_call_tool_impl,
)
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.llm_call_client import call_llm
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.skilldev_tool_context import (
    load_agent_output_schema,
    load_tool_output_schema,
    resolve_session_id,
)
from jiuwenclaw.agentserver.tools.subagent_executor.context_vars import (
    get_effective_request_workspace_dir,
)
from jiuwenclaw.sandbox.sandbox_routing_settings import sandbox_routing_enabled

logger = logging.getLogger(__name__)

_AGENT_FUNC_NAME = "agent_as_a_tool"
_BUNDLE_NAME_KEY = "bundleName"


def _parse_invoke_inputs(inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    func_name = str(inputs.get("funcName") or inputs.get("func_name") or "").strip()
    params = inputs.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("params 必须是对象")
    return func_name, dict(params)


def _resolve_plugin_id(func_name: str, params: dict[str, Any]) -> str:
    plugin_id = str(params.get(_BUNDLE_NAME_KEY) or "").strip()
    if plugin_id:
        return plugin_id

    workspace = get_effective_request_workspace_dir()
    if not workspace:
        return ""

    registry = load_external_tools(workspace)
    matches = sorted(
        pid for (pid, tool_name) in registry.keys() if tool_name == func_name
    )
    if len(matches) == 1:
        return matches[0]
    return ""


def _build_function_call_inputs(func_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Map invoke fields to function_call_tool: bundleName→pluginId, funcName→toolName."""
    return {
        "pluginId": _resolve_plugin_id(func_name, params),
        "toolName": func_name.strip(),
        "arguments": {
            k: v for k, v in params.items() if k != _BUNDLE_NAME_KEY
        },
    }


def _build_call_llm_params(func_name: str, params: dict[str, Any]) -> Any:
    """Build call_llm params from InvokeTool input.params."""
    if func_name == _AGENT_FUNC_NAME:
        return params.get("query", "")
    return {k: v for k, v in params.items() if k != _BUNDLE_NAME_KEY}


def _load_output_schema(
    func_name: str,
    params: dict[str, Any],
    session_id: str | None,
) -> Any | None:
    if not session_id:
        return None
    if func_name == _AGENT_FUNC_NAME:
        agent_id = str(params.get("agentId") or params.get("agent_id") or "").strip()
        if not agent_id:
            return None
        return load_agent_output_schema(
            session_id,
            agent_id,
            log_prefix="InvokeTool",
        )

    plugin_id = _resolve_plugin_id(func_name, params)
    if not plugin_id:
        return None
    return load_tool_output_schema(
        session_id,
        plugin_id,
        func_name,
        log_prefix="InvokeTool",
    )


async def _invoke_sandbox(
    func_name: str,
    params: dict[str, Any],
    *,
    session_id: str | None,
) -> Any:
    output_schema = _load_output_schema(func_name, params, session_id)
    llm_params = _build_call_llm_params(func_name, params)
    result = await call_llm(
        func_name=func_name,
        params=llm_params,
        output_schema=output_schema,
        session_id=session_id,
    )
    return result.get("content")


async def _invoke_agent(params: dict[str, Any], **kwargs: Any) -> Any:
    tool = AgentAsATool()
    return await tool.invoke(params, **kwargs)


async def _invoke_function(
    func_name: str,
    params: dict[str, Any],
    *,
    session_id: str | None,
    **kwargs: Any,
) -> Any:
    # bundleName → pluginId，funcName → toolName，其余 params 字段 → arguments
    plugin_id = _resolve_plugin_id(func_name, params)
    tool_name = func_name.strip()
    arguments = {k: v for k, v in params.items() if k != _BUNDLE_NAME_KEY}
    function_inputs = {
        "pluginId": plugin_id,
        "toolName": tool_name,
        "arguments": arguments,
    }
    output_schema = None
    if session_id:
        plugin_id = function_inputs.get("pluginId", "")
        tool_name = function_inputs.get("toolName", "")
        if plugin_id and tool_name:
            output_schema = load_tool_output_schema(
                session_id,
                str(plugin_id),
                str(tool_name),
                log_prefix="InvokeTool",
            )
    logger.info(
        "[InvokeTool] sessionId=%s funcName=%s outputSchema=%s",
        session_id or "",
        func_name,
        "set" if output_schema is not None else "missing",
    )
    result = await _function_call_tool_impl(**function_inputs)
    if output_schema is not None and isinstance(result, dict):
        result.setdefault("outputSchema", output_schema)
    return result


async def _dispatch_invoke(
    func_name: str,
    params: dict[str, Any],
    *,
    session_id: str | None,
    **kwargs: Any,
) -> Any:
    if sandbox_routing_enabled():
        return await _invoke_sandbox(func_name, params, session_id=session_id)

    if func_name == _AGENT_FUNC_NAME:
        return await _invoke_agent(params, **kwargs)

    return await _invoke_function(
        func_name,
        params,
        session_id=session_id,
        **kwargs,
    )


async def _invoke_tool_impl(**inputs: Any) -> Any:
    try:
        func_name, params = _parse_invoke_inputs(inputs)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    if not func_name:
        return {"success": False, "error": "funcName 为必填参数"}

    session_id = resolve_session_id(inputs)
    logger.info(
        "[invoke_tool] funcName=%s sessionId=%s sandbox=%s",
        func_name,
        session_id or "",
        sandbox_routing_enabled(),
    )
    return await _dispatch_invoke(
        func_name,
        params,
        session_id=session_id,
        **inputs,
    )


_INVOKE_TOOL_CARD = ToolCard(
    id="jiuwenclaw_invoke_tool",
    name="invoke",
    description=(
        "统一调用外部插件工具或 Agent 服务。"
        "funcName 为 agent_as_a_tool 时，params 需包含 agentId、query 及可选 filesInfo；"
        "否则 funcName 为 toolName，params 含 bundleName 及工具自定义参数。"
    ),
    input_params={
        "type": "object",
        "properties": {
            "funcName": {
                "type": "string",
                "description": (
                    "工具或 Agent 标识；调用 Agent 时固定为 agent_as_a_tool，"
                    "调用外部插件时为 toolName"
                ),
            },
            "params": {
                "type": "object",
                "description": "调用参数对象",
            },
        },
        "required": ["funcName", "params"],
    },
)


class InvokeTool(Tool):
    """Routes invoke requests to agent or function-call backends."""

    def __init__(self, card: ToolCard | None = None) -> None:
        super().__init__(card or _INVOKE_TOOL_CARD)

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        merged = {**inputs, **kwargs}
        try:
            func_name, params = _parse_invoke_inputs(merged)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        if not func_name:
            return {"success": False, "error": "funcName 为必填参数"}

        session_id = resolve_session_id(kwargs)
        logger.info(
            "[InvokeTool] funcName=%s sessionId=%s sandbox=%s",
            func_name,
            session_id or "",
            sandbox_routing_enabled(),
        )
        return await _dispatch_invoke(
            func_name,
            params,
            session_id=session_id,
            **kwargs,
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


def get_invoke_tool() -> InvokeTool:
    return InvokeTool()


__all__ = [
    "InvokeTool",
    "get_invoke_tool",
]
