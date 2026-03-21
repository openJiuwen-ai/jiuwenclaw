# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.deepagents.rails.base import DeepAgentRail
from openjiuwen.deepagents.tools.code import CodeTool
from openjiuwen.deepagents.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.deepagents.tools.shell import BashTool
from openjiuwen.deepagents.tools.vision import (
    ImageOCRTool,
    VisualQuestionAnsweringTool,
)


class FileSystemRail(DeepAgentRail):
    """Rail for registering filesystem, shell and code tools."""

    priority = 100

    def __init__(self, language: str = "cn") -> None:
        super().__init__()
        self.tools = None
        self.language = language

    def init(self, agent) -> None:
        lang = self.language
        vision_model_config = None
        if hasattr(agent, "deep_config") and agent.deep_config is not None:
            vision_model_config = getattr(
                agent.deep_config,
                "vision_model_config",
                None,
            )
        read_tool = ReadFileTool(self.sys_operation, lang)
        write_tool = WriteFileTool(self.sys_operation, lang)
        edit_tool = EditFileTool(self.sys_operation, lang)
        glob_tool = GlobTool(self.sys_operation, lang)
        list_dir_tool = ListDirTool(self.sys_operation, lang)
        grep_tool = GrepTool(self.sys_operation, lang)
        bash_tool = BashTool(self.sys_operation, lang)
        code_tool = CodeTool(self.sys_operation, lang)
        image_ocr_tool = ImageOCRTool(lang, vision_model_config)
        visual_question_answering_tool = VisualQuestionAnsweringTool(
            lang,
            vision_model_config,
        )

        self.tools = [
            read_tool,
            write_tool,
            edit_tool,
            glob_tool,
            list_dir_tool,
            grep_tool,
            bash_tool,
            code_tool,
            image_ocr_tool,
            visual_question_answering_tool,
        ]

        Runner.resource_mgr.add_tool(self.tools)

        for tool in self.tools:
            agent.ability_manager.add(tool.card)

    def uninit(self, agent):
        if self.tools:
            for tool in self.tools:
                name = getattr(tool.card, 'name', None)
                if name and hasattr(agent, 'ability_manager'):
                    agent.ability_manager.remove(name)
                
                tool_id = tool.card.id
                if tool_id:
                    Runner.resource_mgr.remove_tool(tool_id)


    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _ = ctx


__all__ = [
    "FileSystemRail",
]
