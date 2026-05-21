# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tool registration helpers for the dedicated SkillDev Agent."""

from __future__ import annotations

from typing import Optional

from openjiuwen.core.foundation.tool import Tool
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
from openjiuwen.harness.tools.web_tools import WebPaidSearchTool

from jiuwenclaw.agentserver.tools.ask_user_question_tool import get_ask_user_question_tool
from jiuwenclaw.agentserver.tools.harness_named_web_tools import (
    JiuwenHarnessFetchWebpageTool,
    JiuwenHarnessFreeSearchTool,
)
from jiuwenclaw.agentserver.tools.subagent_tools import fork_agent, spawn_subagent
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.agent_as_skill_tool import (
    get_agent_as_skill_tool,
)
from jiuwenclaw.agentserver.skilldev_agent.meta_tools.function_call_tool import (
    get_function_call_tool,
)

HARNESS_TOOL_CLASSES = {
    "file_read": ReadFileTool,
    "file_write": WriteFileTool,
    "file_edit": EditFileTool,
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
            JiuwenHarnessFreeSearchTool(language=language, agent_id=agent_id),
            WebPaidSearchTool(language=language, agent_id=agent_id),
            JiuwenHarnessFetchWebpageTool(language=language, agent_id=agent_id),
            get_ask_user_question_tool(),
        ]
    )
    tools.extend([get_function_call_tool(), get_agent_as_skill_tool()])
    return tools
