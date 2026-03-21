# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import AsyncIterator, Any, Dict

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.workflow.components.component import WorkflowComponent
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.graph.base import INPUTS_KEY, CONFIG_KEY
from openjiuwen.core.graph.executable import Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.workflow import Workflow


SUB_WORKFLOW_COMPONENT = "sub_workflow"


class SubWorkflowStreamState:
    """State maintained by SubWorkflowComponent for caching stream results"""

    def __init__(self):
        self._accumulated_outputs: list = []

    def accumulate(self, output: Any):
        """Accumulate stream output chunks"""
        self._accumulated_outputs.append(output)

    def build_final_result(self) -> dict:
        """Build final result from accumulated outputs.

        Merges all accumulated output frames into a single dict.
        """
        if not self._accumulated_outputs:
            return {}

        merged = {}
        for output in self._accumulated_outputs:
            if isinstance(output, dict):
                # Handle nested output format: {'output': {'out': 'xxx'}}
                inner = output.get('output', output)
                if isinstance(inner, dict):
                    for key, value in inner.items():
                        if key in merged:
                            merged[key] = merged[key] + value
                        else:
                            merged[key] = value

        return merged

    def clear(self):
        """Clear state"""
        self._accumulated_outputs = []


class SubWorkflowComponent(WorkflowComponent):
    def __init__(self, sub_workflow: Workflow, *, cache_stream: bool = False):
        super().__init__()
        if sub_workflow is None:
            raise build_error(StatusCode.COMPONENT_SUB_WORKFLOW_PARAM_INVALID, error_msg="sub_workflow is None")
        self._sub_workflow = sub_workflow
        self._cache_stream = cache_stream
        self._stream_state = SubWorkflowStreamState()

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        # When cache_stream is enabled and we have cached stream output,
        # return the cached output instead of re-invoking the sub workflow
        if self._cache_stream:
            cached = self.get_stream_output()
            if cached is not None:
                return {'output': cached}
        return await self._sub_workflow.invoke(inputs.get(INPUTS_KEY), session, context,
                                               config=inputs.get(CONFIG_KEY), is_sub=True)

    async def stream(self, inputs: Input, session: Session, context: ModelContext) -> AsyncIterator[Output]:
        async for value in self._sub_workflow.stream(inputs.get(INPUTS_KEY),
                                                         session, config=inputs.get(CONFIG_KEY), is_sub=True):
            if self._cache_stream:
                self._stream_state.accumulate(value)
            yield value

    def get_stream_output(self) -> Output:
        """Get the cached stream output for batch retrieval."""
        if self._cache_stream:
            return self._stream_state.build_final_result()
        return None

    def graph_invoker(self) -> bool:
        return True

    def component_type(self) -> str:
        return SUB_WORKFLOW_COMPONENT

    @property
    def sub_workflow(self) -> Workflow:
        return self._sub_workflow
