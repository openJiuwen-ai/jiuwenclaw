# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Callable, Union, Hashable, Any

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.workflow.components.flow.branch_router import BranchRouter
from openjiuwen.core.workflow.components.condition.condition import Condition
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import Graph
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session


class BranchComponent(WorkflowComponent):

    def __init__(self):
        super().__init__()
        self._router = BranchRouter(True)

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        if not condition:
            raise build_error(StatusCode.COMPONENT_BRANCH_PARAM_INVALID, reason=f"condition is None or empty")
        if not target:
            raise build_error(StatusCode.COMPONENT_BRANCH_PARAM_INVALID, reason=f"target is None or empty")
        if isinstance(target, list):
            for idx, item in enumerate(target):
                if not item:
                    raise build_error(StatusCode.COMPONENT_BRANCH_PARAM_INVALID,
                                      reason=f'empty item at index {idx} in target list')
        self._router.add_branch(condition, target, branch_id=branch_id)

    def router(self) -> Callable[..., Union[Hashable, list[Hashable]]]:
        return self._router

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        self._router.set_session(session)
        return {}

    def add_component(self, graph: Graph, node_id: str, wait_for_all: bool = False):
        graph.add_node(node_id, self.to_executable(), wait_for_all=wait_for_all)
        graph.add_conditional_edges(node_id, self.router())

    def skip_trace(self) -> bool:
        return True

