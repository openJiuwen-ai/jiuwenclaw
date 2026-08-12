# coding: utf-8
"""DoloresTodoRail — AgentLoop 用的轻量 todo 阶段锚 rail（无 task loop）。

stock `TaskPlanningRail` 的可复用核心：注册 4 个 TodoTool + 每次 before_model_call
注入 `build_todo_section` 到 system prompt。跳过一切与 outer task loop 耦合的部分
（after_task_iteration / _sync_todos_from_plan / model_selection / progress reminder）。

为什么需要：AgentLoop 无 outer task loop，跳过了 TaskPlanningRail → 模型在长上下文
（pptx-craft ~89k token）下丢失多阶段流程锚点，中途误判"任务完成"给 no-tool-call
final（见 _resume_diag.txt `iter31 break: no tool_calls`）。本 rail 把阶段清单
机制补回——每轮 system prompt 都钉着"用 todo_create/modify/list 跟踪进度"，
模型在 tempted-to-stop 时能看到还有 PENDING 的 stage。

机制对 task loop 无依赖（TaskPlanningRail 的耦合是偶然的：init 的 isinstance(DeepAgent)
守卫 + after_task_iteration 的 load_state().task_plan）。TodoTool 本身只要
sys_operation + workspace 字符串即可持久化到 {workspace}/{session}/todo.json。
"""
from __future__ import annotations

from openjiuwen.core.common.logging import agent_logger as logger
from openjiuwen.harness.prompts.sections import SectionName
from openjiuwen.harness.prompts.sections.todo import build_todo_section
from openjiuwen.harness.rails.base import DeepAgentRail


class DoloresTodoRail(DeepAgentRail):
    """AgentLoop 上的 todo 阶段锚 rail（无 task loop 耦合）。

    - init: 注册 TodoCreateTool/TodoListTool/TodoGetTool/TodoModifyTool 到 ability_manager
    - before_model_call: 注入 build_todo_section 到 system_prompt_builder
    """

    priority = 90

    def __init__(self) -> None:
        super().__init__()
        self.system_prompt_builder = None
        self._tools: list = []

    def init(self, agent) -> None:
        from openjiuwen.harness.tools import (
            TodoCreateTool,
            TodoListTool,
            TodoGetTool,
            TodoModifyTool,
        )
        from openjiuwen.harness.workspace.workspace import WorkspaceNode

        ability_manager = getattr(agent, "ability_manager", None)
        if ability_manager is None:
            return

        # register_rail 已在 init 前调 set_sys_operation + 设 system_prompt_builder
        self.system_prompt_builder = getattr(agent, "system_prompt_builder", None)

        # workspace：AgentLoop.register_rail 不设 workspace，从 deep_config 取
        if self.workspace is None:
            deep_cfg = getattr(agent, "deep_config", None) or getattr(agent, "_deep_config", None)
            workspace = getattr(deep_cfg, "workspace", None) if deep_cfg is not None else None
            if workspace is not None:
                self.set_workspace(workspace)
        if self.workspace is None or self.sys_operation is None:
            logger.warning("[DoloresTodoRail] init skip: no workspace/sys_operation")
            return

        workspace_dir = str(self.workspace.get_node_path(WorkspaceNode.TODO))
        agent_id = getattr(getattr(agent, "card", None), "id", None)
        language = self.system_prompt_builder.language if self.system_prompt_builder else "cn"

        for cls in (TodoCreateTool, TodoListTool, TodoGetTool, TodoModifyTool):
            try:
                tool = cls(self.sys_operation, workspace_dir, language, agent_id)
                ability_manager.add_ability(tool.card, tool)
                self._tools.append(tool)
            except Exception as exc:
                logger.warning("[DoloresTodoRail] add %s failed: %s", cls.__name__, exc)

    def uninit(self, agent) -> None:
        try:
            if self.system_prompt_builder:
                self.system_prompt_builder.remove_section(SectionName.TODO)
            ability_manager = getattr(agent, "ability_manager", None)
            if ability_manager and self._tools:
                for tool in self._tools:
                    name = getattr(tool.card, "name", None)
                    if name:
                        try:
                            ability_manager.remove_ability(name)
                        except Exception:
                            pass
        except Exception as exc:
            logger.warning("[DoloresTodoRail] uninit failed: %s", exc)

    async def before_model_call(self, ctx) -> None:
        """每轮注入 todo 阶段锚 section（与 stock TaskPlanningRail 一致）。"""
        if self.system_prompt_builder is None:
            return
        section = build_todo_section(language=self.system_prompt_builder.language)
        if section is not None:
            self.system_prompt_builder.add_section(section)
        else:
            self.system_prompt_builder.remove_section(SectionName.TODO)
