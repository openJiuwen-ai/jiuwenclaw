# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Tuple, Optional

from openjiuwen.core.runner.resources_manager.abstract_manager import AbstractManager
from openjiuwen.core.runner.resources_manager.base import WorkflowProvider
from openjiuwen.core.session.tracer import decorate_workflow_with_trace


class WorkflowMgr(AbstractManager["Workflow"]):
    def __init__(self):
        super().__init__()

    def add_workflow(self, workflow_id: str, workflow: WorkflowProvider) -> None:
        self._register_resource_provider(workflow_id, workflow)

    def add_workflows(self, workflows: List[Tuple[str, WorkflowProvider]]):
        if not workflows:
            return
        for key, workflow in workflows:
            self._register_resource_provider(key, workflow)

    async def get_workflow(self, workflow_id: str, session=None):
        workflow = await self._get_resource(workflow_id)
        return decorate_workflow_with_trace(workflow, session)

    def remove_workflow(self, workflow_id: str) -> Optional[WorkflowProvider]:
        return self._unregister_resource_provider(workflow_id)