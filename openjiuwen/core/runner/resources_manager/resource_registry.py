# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from openjiuwen.core.runner.resources_manager.agent_group_manager import AgentGroupMgr
from openjiuwen.core.runner.resources_manager.agent_manager import AgentMgr
from openjiuwen.core.runner.resources_manager.model_manager import ModelMgr
from openjiuwen.core.runner.resources_manager.prompt_manager import PromptMgr
from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr
from openjiuwen.core.runner.resources_manager.workflow_manager import WorkflowMgr
from openjiuwen.core.runner.resources_manager.sys_operation_manager import SysOperationMgr


class ResourceRegistry:
    def __init__(self) -> None:
        self._tool_mgr = ToolMgr()
        self._workflow_mgr = WorkflowMgr()
        self._prompt_mgr = PromptMgr()
        self._model_mgr = ModelMgr()
        self._agent_mgr: AgentMgr = AgentMgr()
        self._agent_group_mgr: AgentGroupMgr = AgentGroupMgr()
        self._sys_operation_mgr: SysOperationMgr = SysOperationMgr()

    def remove_by_id(self, resource_id: str):
        if self.tool().remove_tool(resource_id):
            return
        if self.workflow().remove_workflow(resource_id):
            return
        if self.agent().remove_agent(resource_id):
            return
        if self.agent_group().remove_agent_group(resource_id):
            return
        if self.prompt().remove_prompt(resource_id):
            return
        if self.model().remove_model(resource_id):
            return
        if self.sys_operation().remove_sys_operation(resource_id):
            return

    def tool(self) -> ToolMgr:
        return self._tool_mgr

    def prompt(self) -> PromptMgr:
        return self._prompt_mgr

    def model(self) -> ModelMgr:
        return self._model_mgr

    def workflow(self) -> WorkflowMgr:
        return self._workflow_mgr

    def agent(self) -> AgentMgr:
        return self._agent_mgr

    def agent_group(self) -> AgentGroupMgr:
        return self._agent_group_mgr

    def sys_operation(self) -> SysOperationMgr:
        return self._sys_operation_mgr