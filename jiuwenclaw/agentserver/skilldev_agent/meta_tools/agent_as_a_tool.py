# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool that invokes external agents via agent-runtime-service."""

from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator, Dict, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool.base import Tool, ToolCard

from jiuwenclaw.agentserver.skilldev_agent.meta_tools.agent_runtime_client import (
    AgentRuntimeClient,
)
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.skilldev_tool_context import (
    load_agent_output_schema,
    resolve_session_id,
)

_MOCK_AGENT_RUNTIME_BASE_URL = "localhost"

logger = logging.getLogger(__name__)

_AGENT_RUNTIME_BASEURL_ENV = "AGENT_RUNTIME_BASEURL"

_DESCRIPTIONS = {
    "cn": (
        "调用外部 Agent 服务。传入目标 agentId、query 及可选的 filesInfo，"
        "通过 agent-runtime-service 的 A2A WebSocket 接口执行远程智能体。"
    ),
    "en": (
        "Invoke an external Agent service. Provide target agentId, query, "
        "and optional filesInfo; runs the remote agent via agent-runtime-service A2A WebSocket API."
    ),
}


def _build_input_params(language: str = "cn") -> Dict[str, Any]:
    is_cn = language in ("cn", "zh")
    return {
        "type": "object",
        "properties": {
            "agentId": {
                "type": "string",
                "description": "调用的目标 agentId" if is_cn else "Target agent ID to invoke",
            },
            "query": {
                "type": "string",
                "description": "输入目标 agent 的请求" if is_cn else "Request text for the target agent",
            },
            "filesInfo": {
                "type": "array",
                "description": "附件文件信息列表" if is_cn else "Optional attached file metadata",
                "items": {
                    "type": "object",
                    "properties": {
                        "fileType": {
                            "type": "string",
                            "enum": ["file", "image"],
                            "description": "文件类型" if is_cn else "File type: file or image",
                        },
                        "fileId": {
                            "type": "string",
                            "description": "文件唯一 ID" if is_cn else "Unique file ID",
                        },
                        "fileUrl": {
                            "type": "string",
                            "description": "文件下载地址" if is_cn else "File download URL",
                        },
                    },
                    "required": ["fileType", "fileId", "fileUrl"],
                },
            },
        },
        "required": ["agentId", "query"],
    }


def _build_agent_as_a_tool_card(
    *,
    agent_id: Optional[str] = None,
    language: str = "cn",
) -> ToolCard:
    lang = "cn" if language in ("cn", "zh") else "en"
    return ToolCard(
        id="agent_as_a_tool_default",
        name="agent_as_a_tool",
        description=_DESCRIPTIONS[lang],
        input_params=_build_input_params(language),
    )


class AgentAsATool(Tool):
    """Wraps external agent invocation as an openjiuwen Tool."""

    def __init__(
        self,
        *,
        agent_id: Optional[str] = None,
        language: str = "cn",
        card: Optional[ToolCard] = None,
    ) -> None:
        super().__init__(card or _build_agent_as_a_tool_card(agent_id=agent_id, language=language))
        base_url = (os.environ.get(_AGENT_RUNTIME_BASEURL_ENV) or "").strip()
        if not base_url:
            base_url = _MOCK_AGENT_RUNTIME_BASE_URL
        self._client = AgentRuntimeClient(base_url)

    def _validate_inputs(self, inputs: Any) -> dict[str, Any]:
        if not isinstance(inputs, dict):
            raise ValueError("inputs must be a dict")

        agent_id = (inputs.get("agentId") or "").strip()
        query = (inputs.get("query") or "").strip()
        if not agent_id:
            raise ValueError("agentId is required and cannot be empty")
        if not query:
            raise ValueError("query is required and cannot be empty")

        payload: dict[str, Any] = {
            "agentId": agent_id,
            "query": query,
        }
        files_info = inputs.get("filesInfo")
        if files_info is not None:
            if not isinstance(files_info, list):
                raise ValueError("filesInfo must be a list")
            payload["filesInfo"] = files_info
        return payload

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> Any:
        payload = self._validate_inputs(inputs)
        session_id = resolve_session_id(kwargs)
        if session_id:
            payload["sessionId"] = session_id
            output_schema = load_agent_output_schema(
                session_id,
                payload["agentId"],
                log_prefix="AgentAsSkillTool",
            )
            if output_schema is not None:
                payload["outputSchema"] = output_schema
        logger.info(
            "[AgentAsSkillTool] invoking external agent agentId=%s sessionId=%s "
            "outputSchema=%s (card_id=%s)",
            payload["agentId"],
            session_id or "",
            "set" if payload.get("outputSchema") is not None else "missing",
            self.card.id,
        )
        result = await self._client.run_agent(payload)
        return result

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        yield "Stream is not supported for this tool."
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)


def get_agent_as_a_tool() -> AgentAsATool:
    """Factory: 返回默认配置的 AgentAsSkillTool 实例（card.id=agent_as_a_tool_default）.

    与 ``get_function_call_tool`` 一致：父 agent 用本工厂注册 Tool 实例，
    子 agent 引用 ``.card`` 共享同一 card.id 以避免重复注册冲突。
    """
    return AgentAsATool()


__all__ = [
    "AgentAsATool",
    "_build_agent_as_a_tool_card",
    "get_agent_as_a_tool",
]
