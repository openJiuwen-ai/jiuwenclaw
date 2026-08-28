# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unified invoke meta-tool: cloud PluginSkillExec (mcp/run) or remote Agent Runtime."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, AsyncIterator, Dict

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Tool, ToolCard

from jiuwenswarm.agents.harness.common.tools.invoke_meta.agent_as_a_tool import (
    invoke_remote_agent,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.external_tool_registry import (
    ExternalToolSpec,
    load_external_tools,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.plugin_skill_catalog import (
    PLUGIN_SKILL_CATALOG,
    extract_seedance_query_state,
    extract_seedance_task_id,
    invoke_tool_description,
    normalize_plugin_skill_args,
    validate_plugin_skill_args,
    want_seedance_wait,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.schema_context import (
    resolve_session_id,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.useraccess_runtime import (
    build_cloud_plugin_context,
    missing_credential_error,
    missing_plugin_url_error,
    resolve_business_credential,
    resolve_plugin_runtime_url,
)
from jiuwenswarm.agents.harness.common.tools.invoke_meta.workspace_context import (
    get_effective_request_workspace_dir,
)

logger = logging.getLogger(__name__)

_AGENT_FUNC_NAME = "agent_as_a_tool"
_PLUGIN_SKILL_EXEC = "PluginSkillExecTool"
_BUNDLE_NAME_KEY = "bundleName"
_DEVICE_UNSUPPORTED_MSG = "当前不支持pluginType为Device的端插件调用，请到真机进行测试"
_SEEDANCE_TASK = "seedanceMiniTask"
_SEEDANCE_QUERY = "seedanceMiniTaskQuery"
_MUSIC_FUNC = "musicGeneration"


def _parse_invoke_inputs(inputs: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    params = inputs.get("arguments", inputs.get("params", {}))
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ValueError("arguments 必须是对象")
    params = dict(params)

    # Prefer top-level functionName (InvokeTool schema). Models often omit
    # the PluginSkillExecTool wrapper and only put the real capability on
    # arguments.functionName.
    func_name = str(inputs.get("functionName") or inputs.get("funcName") or "").strip()
    if not func_name:
        nested = str(params.get("functionName") or params.get("funcName") or "").strip()
        if nested in PLUGIN_SKILL_CATALOG:
            func_name = _PLUGIN_SKILL_EXEC
        else:
            func_name = nested
    return func_name, params


def _normalize_plugin_skill_call(
    func_name: str, params: dict[str, Any]
) -> tuple[str, dict[str, Any], bool]:
    """Map invoke(PluginSkillExecTool, {functionName, bundleName, ...}).

    Returns (resolved_function_name, params, via_plugin_skill_exec).
    """
    if func_name != _PLUGIN_SKILL_EXEC:
        return func_name, params, False
    nested_name = str(params.get("functionName") or params.get("funcName") or "").strip()
    if not nested_name:
        raise ValueError(
            "functionName=PluginSkillExecTool 时，arguments.functionName 为必填"
            "（如 seedreamLite4Skill / imageUnderStandStream / seedanceMiniTask / "
            "lyricsGeneration / musicGeneration）"
        )
    return nested_name, dict(params), True


def _resolve_plugin_id(func_name: str, params: dict[str, Any]) -> str:
    plugin_id = str(params.get(_BUNDLE_NAME_KEY) or "").strip()
    if plugin_id:
        return plugin_id

    workspace = get_effective_request_workspace_dir()
    if not workspace:
        return ""

    registry = load_external_tools(workspace)
    matches = sorted(pid for (pid, tool_name) in registry.keys() if tool_name == func_name)
    if len(matches) == 1:
        return matches[0]
    return ""


def _resolve_tool_spec(func_name: str, params: dict[str, Any]) -> ExternalToolSpec | None:
    workspace = get_effective_request_workspace_dir()
    if not workspace:
        return None
    registry = load_external_tools(workspace)
    plugin_id = _resolve_plugin_id(func_name, params)
    if not plugin_id:
        matches = [(pid, tid) for (pid, tid) in registry.keys() if tid == func_name]
        if len(matches) == 1:
            plugin_id = matches[0][0]
        else:
            return None
    return registry.get((plugin_id, func_name))


def _build_plugin_spec(func_name: str, params: dict[str, Any]) -> ExternalToolSpec | dict[str, Any]:
    """Resolve ExternalToolSpec from registry or synthesize from bundleName."""
    plugin_id = _resolve_plugin_id(func_name, params)
    if not plugin_id:
        return {
            "success": False,
            "error": "无法解析 bundleName/pluginId，请在 arguments 中提供 bundleName",
            "toolName": func_name,
        }

    spec = _resolve_tool_spec(func_name, params)
    if spec is not None:
        if spec.plugin_type == "Device":
            return {
                "success": False,
                "error": _DEVICE_UNSUPPORTED_MSG,
                "summary": _DEVICE_UNSUPPORTED_MSG,
                "pluginId": spec.plugin_id,
                "toolName": spec.tool_name,
                "pluginType": spec.plugin_type,
            }
        return spec

    return ExternalToolSpec(
        plugin_id=plugin_id,
        tool_name=func_name.strip(),
        description="",
        protocol="WS",
        plugin_type="Cloud",
    )


async def _invoke_cloud_plugin(
    spec: ExternalToolSpec,
    params: dict[str, Any],
    *,
    session_id: str | None,
) -> Any:
    from jiuwenswarm.agents.harness.common.tools.invoke_meta.cloud_plugin_client import (
        CloudPluginClient,
    )

    base_url = resolve_plugin_runtime_url()
    if not base_url:
        return missing_plugin_url_error(plugin_id=spec.plugin_id, tool_name=spec.tool_name)
    if not resolve_business_credential():
        return missing_credential_error(plugin_id=spec.plugin_id, tool_name=spec.tool_name)

    skip = {
        _BUNDLE_NAME_KEY,
        "functionName",
        "funcName",
        "skillName",
        "turnContinue",
        "eventContexts",
        "progressToken",
        "contexts",
        "wait",
    }
    arguments = {k: v for k, v in params.items() if k not in skip}
    arguments["bundleName"] = spec.plugin_id
    arguments["functionName"] = spec.tool_name
    for key in ("skillName", "turnContinue", "eventContexts", "progressToken", "contexts"):
        if key in params:
            arguments[key] = params[key]

    context = build_cloud_plugin_context(session_id=session_id)
    ws_timeout = _plugin_ws_timeout(spec.tool_name)
    client = CloudPluginClient(
        base_url=base_url, session_id=session_id, timeout=ws_timeout
    )
    logger.info(
        "[InvokeTool] [session=%s] plugin via mcp/run pluginId=%s toolName=%s url=%s",
        session_id or "",
        spec.plugin_id,
        spec.tool_name,
        base_url,
    )
    return await client.invoke(spec, arguments=arguments, context=context)


def _plugin_ws_timeout(tool_name: str) -> float | None:
    """musicGeneration waits up to 10 minutes; others keep CloudPluginClient default."""
    if tool_name != _MUSIC_FUNC:
        return None
    return float(os.getenv("MUSIC_WS_TIMEOUT", "600") or "600")


def _seedance_poll_settings() -> tuple[float, float]:
    timeout = float(os.getenv("SEEDANCE_POLL_TIMEOUT", "300") or "300")
    interval = float(os.getenv("SEEDANCE_POLL_INTERVAL", "8") or "8")
    return max(timeout, 1.0), max(interval, 0.0)


async def _poll_seedance_task(
    submit_spec: ExternalToolSpec,
    _params: dict[str, Any],
    submit_result: dict[str, Any],
    *,
    session_id: str | None,
) -> dict[str, Any]:
    task_id = extract_seedance_task_id(submit_result)
    if not task_id:
        return {
            **submit_result,
            "success": False,
            "error": (
                "seedanceMiniTask 未返回 task_id，无法轮询成片。"
                "可传 arguments.wait=false 只取提交结果。"
            ),
            "task_id": "",
        }

    query_spec = ExternalToolSpec(
        plugin_id=submit_spec.plugin_id,
        tool_name=_SEEDANCE_QUERY,
        description=submit_spec.description,
        protocol=submit_spec.protocol,
        plugin_type=submit_spec.plugin_type,
    )
    query_params = {
        _BUNDLE_NAME_KEY: submit_spec.plugin_id,
        "functionName": _SEEDANCE_QUERY,
        "id": task_id,
    }
    timeout_s, interval_s = _seedance_poll_settings()
    deadline = time.monotonic() + timeout_s
    last_query: dict[str, Any] = {}
    status = ""
    video_url = ""

    logger.info(
        "[InvokeTool] seedance poll start task_id=%s timeout=%ss interval=%ss",
        task_id,
        timeout_s,
        interval_s,
    )
    while time.monotonic() < deadline:
        last_query = await _invoke_cloud_plugin(
            query_spec, query_params, session_id=session_id
        )
        if not isinstance(last_query, dict):
            last_query = {"success": False, "error": str(last_query)}
        if not last_query.get("success"):
            return {
                **last_query,
                "task_id": task_id,
                "error": last_query.get("error") or "seedanceMiniTaskQuery 失败",
            }
        status, video_url = extract_seedance_query_state(last_query)
        if status == "succeeded" or video_url:
            merged = dict(last_query)
            merged["success"] = True
            merged["task_id"] = task_id
            merged["status"] = status or "succeeded"
            merged["video_url"] = video_url
            if video_url and not merged.get("content"):
                merged["content"] = video_url
            return merged
        if status and status not in {"running", "queued", "pending", "processing"}:
            return {
                "success": False,
                "error": f"seedance 任务失败 status={status}",
                "task_id": task_id,
                "status": status,
                "content": last_query.get("content", ""),
            }
        if interval_s > 0:
            await asyncio.sleep(interval_s)

    return {
        "success": False,
        "error": f"seedance 轮询超时 ({timeout_s}s) task_id={task_id} status={status or 'unknown'}",
        "task_id": task_id,
        "status": status or "timeout",
        "content": last_query.get("content", "") if isinstance(last_query, dict) else "",
    }


async def _dispatch_invoke(
    func_name: str,
    params: dict[str, Any],
    *,
    session_id: str | None,
    **kwargs: Any,
) -> Any:
    if func_name == _AGENT_FUNC_NAME:
        return await invoke_remote_agent(params, **kwargs)

    try:
        func_name, params, via_plugin_skill = _normalize_plugin_skill_call(func_name, params)
    except ValueError as exc:
        return {"success": False, "error": str(exc)}

    # PLUGIN_SKILL_CATALOG: coerce then validate (fail fast, skip waiting on WS).
    if via_plugin_skill or func_name in PLUGIN_SKILL_CATALOG:
        params, norm_err = normalize_plugin_skill_args(func_name, params)
        if norm_err is not None:
            return {"success": False, "error": norm_err}
        catalog_err = validate_plugin_skill_args(func_name, params)
        if catalog_err is not None:
            return {"success": False, "error": catalog_err}

    built = _build_plugin_spec(func_name, params)
    if isinstance(built, dict):
        return built
    result = await _invoke_cloud_plugin(built, params, session_id=session_id)
    if (
        func_name == _SEEDANCE_TASK
        and isinstance(result, dict)
        and result.get("success")
        and want_seedance_wait(params)
    ):
        return await _poll_seedance_task(built, params, result, session_id=session_id)
    return result


_INVOKE_TOOL_CARD = ToolCard(
    id="jiuwenswarm_invoke_tool",
    name="invoke",
    description=invoke_tool_description(),
    input_params={
        "type": "object",
        "properties": {
            "functionName": {
                "type": "string",
                "description": (
                    "云端 skill：固定 PluginSkillExecTool；"
                    "远程 Agent：agent_as_a_tool。"
                    "arguments.functionName 才是具体能力"
                    "（seedreamLite4Skill / SeedreamPro4Skill / "
                    "imageUnderStandStream / seedanceMiniTask / seedanceMiniTaskQuery / "
                    "lyricsGeneration / musicGeneration）。"
                ),
            },
            "arguments": {
                "type": "object",
                "description": (
                    "必含 bundleName + functionName（真实能力名）+ 业务字段。"
                    "生图：bundleName=com.atomicservice.5765880207845681341，"
                    "functionName=seedreamLite4Skill|SeedreamPro4Skill，prompt=...；"
                    "图像理解：bundleName=xiaoyi，functionName=imageUnderStandStream，imageUrl=...；"
                    "生视频：同原子服务 bundle，seedanceMiniTask 用 content"
                    "（默认自动轮询到 video_url；wait=false 则只返回 task_id），"
                    "seedanceMiniTaskQuery 用 id；"
                    "生音乐：同原子服务 bundle，业务字段与 bundleName 平铺，不要包 content。"
                    "基础器乐只用 musicGeneration+is_instrumental=true；"
                    "基础人声 lyrics_optimizer=true；"
                    "高级人声先 lyricsGeneration（write_full_song，改词 edit+lyrics），"
                    "确认歌词后再 musicGeneration 带 lyrics。"
                    "成曲前向用户展示类型/语言/prompt/歌词并得到明确确认。"
                    "中文输入用中文 prompt 与歌词，英文同理，其它语言先问用户。"
                    "prompt 写成完整句子（情绪+流派+人声或乐器+叙事/场景），"
                    "不要逗号关键词列表。勿臆造其它 bundleName。"
                ),
            },
        },
        "required": ["functionName", "arguments"],
    },
)


class InvokeTool(Tool):
    """Routes invoke to cloud PluginSkillExec (mcp/run) or remote Agent."""

    def __init__(self, card: ToolCard | None = None) -> None:
        super().__init__(card or _INVOKE_TOOL_CARD)

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        merged = {**inputs, **kwargs}
        session_id = resolve_session_id(kwargs) or resolve_session_id(merged)
        try:
            func_name, params = _parse_invoke_inputs(merged)
        except ValueError as exc:
            return {"success": False, "error": str(exc)}

        if not func_name:
            return {"success": False, "error": "functionName 为必填参数"}

        logger.info(
            "[InvokeTool] functionName=%s sessionId=%s",
            func_name,
            session_id or "",
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


__all__ = ["InvokeTool"]
