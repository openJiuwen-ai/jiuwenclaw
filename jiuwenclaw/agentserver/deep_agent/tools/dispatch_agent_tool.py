# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""DispatchAgentTool — 让 main agent 运行时自由起草 subagent 规格并 spawn。

相比 ``task_tool`` 仅能从 ``deep_config.subagents`` 白名单里按 name 调度，本工具让
LLM 在每次调用时自己写 ``name`` + ``system_prompt`` + ``task_description``，由工具
在运行时构造临时 ``SubAgentConfig``、注入 parent 的 ``deep_config.subagents``、
走 SDK 原生 ``create_subagent`` 流程 spawn 出独立 session 的子 agent，执行完
再把临时 spec 从列表里移除。

工具使用流程：
1. LLM 调用 ``dispatch_agent`` 并传入 name / description / system_prompt / task_description
2. 工具构造 ephemeral SubAgentConfig（FileSystemRail + parent 的 model/workspace/language）
3. 用 uuid 后缀拼出唯一 name，append 到 parent._deep_config.subagents
4. 调 parent.create_subagent(unique_name, sub_session_id) 得到 DeepAgent 实例
5. 调 subagent.invoke({query, conversation_id}) 拿结果
6. finally 清理 spec
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, AsyncIterator, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import Input, Output, Tool, ToolCard
from openjiuwen.core.session.agent import Session
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.harness.prompts.sections.tools import (
    build_tool_card,
    register_tool_provider,
)
from openjiuwen.harness.prompts.sections.tools.base import ToolMetadataProvider
from openjiuwen.harness.rails.filesystem_rail import FileSystemRail
from openjiuwen.harness.schema.config import SubAgentConfig
from openjiuwen.harness.tools.base_tool import ToolOutput

from jiuwenclaw.agentserver.tools.harness_named_web_tools import build_jiuwen_harness_named_web_tools

if TYPE_CHECKING:
    from openjiuwen.harness.deep_agent import DeepAgent


_DISPATCH_TOOL_NAME = "dispatch_agent"

_DESCRIPTION_CN = (
    "运行时自由起一个子代理处理独立子任务。与 task_tool 不同，本工具让你在每次调用时"
    "自己定义子代理的角色（system_prompt）而不是从固定列表里选。子代理有独立上下文，"
    "完成后返回文本结果。\n\n"
    "何时使用：\n"
    "- 想把主上下文里一段复杂工作切出去，避免污染主 token；\n"
    "- 需要一个临时特化角色（如 PPT 校对员、数据分析师），而已有子代理不匹配；\n"
    "- 要并行调度多个独立职责的子任务。\n\n"
    "子代理默认挂 FileSystemRail（read_file/write_file/edit_file/glob/list_dir/grep/bash/code），"
    "并附带与主代理一致的网页工具 free_search、fetch_webpage（实现与 agentserver/tools 一致）；"
    "工作目录、模型、语言与主代理一致，会话独立。"
)

_DESCRIPTION_EN = (
    "Spawn a freshly-specified subagent at call time to handle an independent subtask. "
    "Unlike task_tool which picks from a fixed roster, this tool lets you write the "
    "subagent's role (system_prompt) per invocation. The subagent runs in an isolated "
    "context and returns a text result.\n\n"
    "Use when:\n"
    "- You want to offload a chunk of work to keep the main context clean;\n"
    "- You need an ad-hoc specialist role (e.g. slide reviewer, data analyst) that no "
    "existing subagent matches;\n"
    "- You want to run multiple independent subtasks in parallel.\n\n"
    "The spawned subagent gets FileSystemRail (read_file/write_file/edit_file/glob/"
    "list_dir/grep/bash/code) plus the same web tools as the main agent: free_search and "
    "fetch_webpage (jiuwenclaw agentserver/tools implementation). Its workspace, model, "
    "and language mirror the main agent; its session is isolated."
)

_PARAMS: Dict[str, Dict[str, str]] = {
    "name": {
        "cn": "子代理简短名（英文/拼音，仅用于日志，不必唯一）",
        "en": "Short subagent name (used for telemetry; need not be unique)",
    },
    "description": {
        "cn": "一句话说明该子代理角色与预期产出",
        "en": "One-line purpose and expected output",
    },
    "system_prompt": {
        "cn": "完整的子代理 system prompt：角色设定、工作原则、输出格式约束",
        "en": "Full system prompt defining role, working principles, and output format",
    },
    "task_description": {
        "cn": "交给子代理执行的具体任务与必要上下文",
        "en": "Concrete task and necessary context handed to the subagent",
    },
}


def _get_dispatch_agent_input_params(language: str = "cn") -> Dict[str, Any]:
    p = _PARAMS
    return {
        "type": "object",
        "properties": {
            key: {
                "type": "string",
                "description": p[key].get(language, p[key]["cn"]),
            }
            for key in ("name", "description", "system_prompt", "task_description")
        },
        "required": ["name", "description", "system_prompt", "task_description"],
    }


class DispatchAgentMetadataProvider(ToolMetadataProvider):
    """Metadata for the dispatch_agent tool (name / description / param schema)."""

    def get_name(self) -> str:
        return _DISPATCH_TOOL_NAME

    def get_description(self, language: str = "cn") -> str:
        return _DESCRIPTION_CN if language == "cn" else _DESCRIPTION_EN

    def get_input_params(self, language: str = "cn") -> Dict[str, Any]:
        return _get_dispatch_agent_input_params(language)


def register_dispatch_agent_provider() -> None:
    """幂等注册 provider。重复调用会覆盖同名条目，安全。"""
    register_tool_provider(DispatchAgentMetadataProvider())


class DispatchAgentTool(Tool):
    """运行时动态 spawn 子代理。规格由 LLM 每次调用时提供。"""

    def __init__(self, card: ToolCard, parent_agent: "DeepAgent", language: str = "cn") -> None:
        super().__init__(card)
        self.parent_agent = parent_agent
        self.language = language

    @staticmethod
    def _unique_agent_name(raw_name: str) -> str:
        base = "".join(
            ch if (ch.isalnum() or ch in "-_") else "_"
            for ch in (raw_name or "").strip()
        ) or "adhoc"
        return f"{base}_{uuid.uuid4().hex[:8]}"

    @staticmethod
    def _sub_session_id(parent_session_id: str, unique_name: str) -> str:
        return f"{parent_session_id}_sub_{unique_name}"

    def _build_spec(
        self, unique_name: str, description: str, system_prompt: str,
    ) -> SubAgentConfig:
        mdc = self.parent_agent.deep_config
        model = getattr(mdc, "model", None)
        workspace = getattr(mdc, "workspace", None)
        language = getattr(mdc, "language", None) or self.language
        max_iterations = getattr(mdc, "max_iterations", None) or 15
        return SubAgentConfig(
            agent_card=AgentCard(name=unique_name, description=description),
            system_prompt=system_prompt,
            model=model,
            tools=build_jiuwen_harness_named_web_tools(
                agent_id=unique_name,
                language=str(language or "cn"),
            ),
            rails=[FileSystemRail()],
            workspace=workspace,
            language=language,
            enable_task_loop=False,
            max_iterations=max_iterations,
        )

    async def invoke(self, inputs: Input, **kwargs) -> ToolOutput:
        parent_session = kwargs.get("session", None)
        if not isinstance(parent_session, Session):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason="dispatch_agent requires a valid session in kwargs",
            )

        if not isinstance(inputs, dict):
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"dispatch_agent invalid inputs type: {type(inputs)}",
            )

        required_fields = {
            "name": str(inputs.get("name") or "").strip(),
            "description": str(inputs.get("description") or "").strip(),
            "system_prompt": str(inputs.get("system_prompt") or "").strip(),
            "task_description": str(inputs.get("task_description") or "").strip(),
        }
        missing = [k for k, v in required_fields.items() if not v]
        if missing:
            raise build_error(
                StatusCode.TOOL_TASK_TOOL_INVOKED,
                reason=f"dispatch_agent requires: {' / '.join(missing)}",
            )
        raw_name = required_fields["name"]
        description = required_fields["description"]
        system_prompt = required_fields["system_prompt"]
        task_description = required_fields["task_description"]

        unique_name = self._unique_agent_name(raw_name)
        spec = self._build_spec(unique_name, description, system_prompt)

        subagents_list = self.parent_agent.deep_config.subagents
        # SubagentRail.init 时若 deep_config.subagents 为 None 就不注册 task_tool；
        # 这里 dispatch_agent 能被调到则 rail 已注册，subagents 理论上非 None。
        # 防御性处理：兜底初始化一个空列表以允许 append。
        if subagents_list is None:
            subagents_list = []
            self.parent_agent.deep_config.subagents = subagents_list

        parent_session_id = parent_session.get_session_id()
        sub_session_id = self._sub_session_id(parent_session_id, unique_name)
        logger.info(
            "[DispatchAgent] spawn: name=%s raw_name=%s parent=%s sub=%s",
            unique_name, raw_name, parent_session_id, sub_session_id,
        )

        subagents_list.append(spec)
        try:
            try:
                subagent = self.parent_agent.create_subagent(unique_name, sub_session_id)
            except Exception as exc:
                logger.error(
                    "[DispatchAgent] create_subagent failed: name=%s error=%s",
                    unique_name, exc,
                )
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason=f"dispatch_agent create failed: {exc}",
                ) from exc

            try:
                result = await subagent.invoke(
                    {"query": task_description, "conversation_id": sub_session_id}
                )
                output = result.get("output", "") if isinstance(result, dict) else ""
                return ToolOutput(success=True, data={"output": output}, error=None)
            except Exception as exc:
                logger.error(
                    "[DispatchAgent] subagent invoke failed: name=%s error=%s",
                    unique_name, exc,
                )
                raise build_error(
                    StatusCode.TOOL_TASK_TOOL_INVOKED,
                    reason=f"dispatch_agent execution failed: {exc}",
                ) from exc
        finally:
            try:
                subagents_list.remove(spec)
            except ValueError:
                pass

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        # 当前仅实现一次性返回；stream 模式留空以满足抽象接口契约。
        if False:
            yield  # pragma: no cover


def create_dispatch_agent_tool(
    parent_agent: "DeepAgent",
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> List[Tool]:
    """构造 dispatch_agent ToolCard + Tool。"""
    register_dispatch_agent_provider()
    card = build_tool_card(
        name=_DISPATCH_TOOL_NAME,
        tool_id=_DISPATCH_TOOL_NAME,
        language=language,
        agent_id=agent_id,
    )
    return [DispatchAgentTool(card=card, parent_agent=parent_agent, language=language)]


__all__ = [
    "DispatchAgentTool",
    "DispatchAgentMetadataProvider",
    "create_dispatch_agent_tool",
    "register_dispatch_agent_provider",
]
