# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import os
from typing import Callable, Union

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow.components.condition.condition import Condition, FuncCondition
from openjiuwen.core.workflow.components.condition.expression import ExpressionCondition
from openjiuwen.core.session import BaseSession
from openjiuwen.core.session.node import Session
from openjiuwen.core.session.tracer import TracerWorkflowUtils
from openjiuwen.core.graph.visualization.drawable_edge import DrawableBranchRouter


WORKFLOW_DRAWABLE = "WORKFLOW_DRAWABLE"


class Branch:
    def __init__(self, condition: Union[str, Callable[[], bool], Condition], target: list[str],
                 branch_id: str = None):
        super().__init__()
        self.branch_id = branch_id
        if isinstance(condition, Condition):
            self._condition = condition
        elif isinstance(condition, str):
            self._condition = ExpressionCondition(condition)
        elif isinstance(condition, Callable):
            self._condition = FuncCondition(condition)
        else:
            raise ValueError("branch condition type does not meet the requirements")
        self.target = target

    def evaluate(self, session: BaseSession) -> bool:
        return self._condition(session)

    def trace_info(self, session: BaseSession) -> str:
        return self._condition.trace_info(session)


class BranchRouter:
    def __init__(self, report_trace: bool = False):
        super().__init__()
        self._branches: list[Branch] = []
        self._session: BaseSession = None
        self.report_trace = report_trace
        self._drawable_branch_router = None
        if os.environ.get(WORKFLOW_DRAWABLE, "false").lower() == "true":
            self._drawable_branch_router = DrawableBranchRouter(targets=[], datas=[])

    def add_branch(self, condition: Union[str, Callable[[], bool], Condition], target: Union[str, list[str]],
                   branch_id: str = None):
        if condition is None or target is None:
            raise build_error(
                StatusCode.COMPONENT_BRANCH_PARAM_INVALID, reason="condition is None or target is None"
            )
        target = [target] if isinstance(target, str) else target
        if self._drawable_branch_router:
            branch_data = branch_id if branch_id else ""
            if isinstance(condition, str):
                branch_data = condition
            for t in target:
                self._drawable_branch_router.targets.append(t)
                self._drawable_branch_router.datas.append(branch_data)
        try:
            self._branches.append(Branch(condition, target, branch_id))
        except ValueError as e:
            raise build_error(StatusCode.COMPONENT_BRANCH_PARAM_INVALID, reason=str(e))


    def get_drawable_branch_router(self):
        return self._drawable_branch_router

    def set_session(self, session: Union[Session, BaseSession]):
        if isinstance(session, Session):
            self._session = getattr(session, "_inner")
            return
        if isinstance(session, BaseSession):
            self._session = session
            return
        raise ValueError("session type is wrong")

    async def __call__(self, *args, **kwargs) -> list[str]:
        session = self._session
        if self.report_trace:
            branches = []
            for branch in self._branches:
                branches.append({
                    "branch_id": branch.branch_id,
                    "condition": branch.trace_info(session)
                })
            await TracerWorkflowUtils.trace_component_begin(session)
            await TracerWorkflowUtils.trace_component_inputs(session, {"branches": branches})
        for branch in self._branches:
            if branch.evaluate(session):
                if self.report_trace:
                    await TracerWorkflowUtils.trace_component_outputs(session, {"branch_id": branch.branch_id})
                    await TracerWorkflowUtils.trace_component_done(session)
                return branch.target
        raise build_error(
            StatusCode.COMPONENT_BRANCH_EXECUTION_ERROR,
            reason="branch meeting the condition was not found"
        )
