# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool registration helpers for the dedicated SkillDev Agent."""

from __future__ import annotations

import uuid
from typing import Optional

from openjiuwen.core.foundation.tool import Tool, ToolCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.harness.tools.bash import BashTool
from openjiuwen.harness.tools.code import CodeTool
from openjiuwen.harness.tools.filesystem import (
    EditFileTool,
    GlobTool,
    GrepTool,
    ListDirTool,
    ReadFileTool,
    WriteFileTool,
)
from openjiuwen.harness.prompts.sections.tools import get_tool_input_params
from openjiuwen.harness.prompts.sections.tools.filesystem import (
    get_read_file_input_params,
    get_edit_file_input_params,
    get_write_file_input_params,
)

from jiuwenclaw.agentserver.tools.ask_user_question_tool import get_ask_user_question_tool
from jiuwenclaw.agentserver.tools.harness_named_web_tools import (
    JiuwenHarnessFetchWebpageTool,
    JiuwenHarnessFreeSearchTool,
)
from jiuwenclaw.agentserver.tools.subagent_tools import fork_agent, spawn_subagent
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.agent_as_a_tool import (
    get_agent_as_a_tool,
)
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.exec_tool import get_exec_tool
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.function_call_tool import (
    get_function_call_tool,
)


class ReadTool(ReadFileTool):

    def __init__(self, operation: SysOperation, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(operation, language, agent_id)
        self._card = ToolCard(
            id=f"read_file_{agent_id}" if agent_id else f"read_file_{uuid.uuid4().hex}",
            name="Read",
            description=(
                "Read a file from the local filesystem."
            ),
            input_params=get_read_file_input_params(language),
        )


class WriteTool(WriteFileTool):

    def __init__(self, operation: SysOperation, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(operation, language, agent_id)
        self._card = ToolCard(
            id=f"write_file_{agent_id}" if agent_id else f"write_file_{uuid.uuid4().hex}",
            name="Write",
            description=(
                "Write a file to the local filesystem."
            ),
            input_params=get_write_file_input_params(language),
        )


class EditTool(EditFileTool):

    def __init__(self, operation: SysOperation, language: str = "cn", agent_id: Optional[str] = None):
        super().__init__(operation, language, agent_id)
        self._card = ToolCard(
            id=f"edit_file_{agent_id}" if agent_id else f"edit_file_{uuid.uuid4().hex}",
            name="Edit",
            description=(
                "A tool for editing files."
            ),
            input_params=get_edit_file_input_params(language),
        )


class WebSearchTool(JiuwenHarnessFreeSearchTool):

    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ) -> None:
        super().__init__(
            language=language,
            agent_id=agent_id,
            card=card or ToolCard(
                id=f"web_search_{agent_id}" if agent_id else f"web_search_{uuid.uuid4().hex}",
                name="WebSearch",
                description=(
                    "XiaoYi wants to search the web for query of user."
                ),
                input_params=get_tool_input_params("free_search"),
            ),
        )


class WebFetchTool(JiuwenHarnessFetchWebpageTool):
    def __init__(
        self,
        language: str = "cn",
        agent_id: Optional[str] = None,
        card: Optional[ToolCard] = None,
    ) -> None:
        super().__init__(
            language=language,
            agent_id=agent_id,
            card=card or ToolCard(
                id=f"web_fetch_{agent_id}" if agent_id else f"web_fetch_{uuid.uuid4().hex}",
                name="WebFetch",
                description=(
                    "XiaoYi wants to fetch content from this URL."
                ),
                input_params=get_tool_input_params("fetch_webpage"),
            ),
        )


HARNESS_TOOL_CLASSES = {
    "Read": ReadTool,
    "Write": WriteTool,
    "Edit": EditTool,
    "file_glob": GlobTool,
    "file_grep": GrepTool,
    "file_listdir": ListDirTool,
    "shell": BashTool,
    "code_execute": CodeTool,
}


def build_skilldev_tools(
    *,
    sys_operation: SysOperation,
    language: str = "cn",
    agent_id: Optional[str] = None,
) -> list[Tool]:
    """Build the dedicated SkillDev Agent tool set.

    TODO tools (todo_create / todo_list / todo_modify) are registered
    separately by TaskPlanningRail and are NOT included here.
    """
    tools: list[Tool] = [
        tool_cls(sys_operation, language=language)
        for tool_cls in HARNESS_TOOL_CLASSES.values()
    ]
    tools.extend(
        [
            WebSearchTool(language=language, agent_id=agent_id),
            WebFetchTool(language=language, agent_id=agent_id),
            get_ask_user_question_tool(),
            get_exec_tool(),
        ]
    )
    tools.extend([get_function_call_tool(), get_agent_as_a_tool(), spawn_subagent])
    return tools
