# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from datetime import datetime
from typing import Optional, Dict, List, Callable, Union, Any
from pydantic import ConfigDict, Field, BaseModel


class Span(BaseModel):
    trace_id: str = Field(alias="traceId")
    start_time: Optional[datetime] = Field(default=None, alias="startTime")
    end_time: Optional[datetime] = Field(default=None, alias="endTime")
    inputs: Optional[dict] = Field(default=None, alias="inputs")
    outputs: Optional[Union[dict, float, int, list, str, tuple]] = Field(default=None, alias="outputs")
    error: Optional[dict] = Field(default=None, alias="error")
    invoke_id: Optional[str] = Field(default=None, alias="invokeId")
    parent_invoke_id: Optional[str] = Field(default=None, alias="parentInvokeId")
    child_invokes_id: Optional[List[str]] = Field(default=None, alias="childInvokes")
    model_config = ConfigDict(populate_by_name=True)
    # module status
    status: Optional[str] = Field(default=None, alias="status")
    # Recording intermediate process information for the current module's execution time
    on_invoke_data: Optional[List[dict]] = Field(default=None, alias="onInvokeData")

    def update(self, data: dict):
        for attr_name, value in data.items():
            if not hasattr(self, attr_name):
                continue
            setattr(self, attr_name, value)

    def append_child_invoke_id(self, invoke_id):
        if self.child_invokes_id:
            self.child_invokes_id.append(invoke_id)
        else:
            self.child_invokes_id = [invoke_id]


class TraceAgentSpan(Span):
    invoke_type: str = Field(default=None, alias="invokeType")
    name: str = Field(default=None, alias="name")
    elapsed_time: Optional[str] = Field(default=None, alias="elapsedTime")
    meta_data: Optional[dict] = Field(default=None, alias="metaData")  # include llm function tools and token infos


class TraceWorkflowSpan(Span):
    execution_id: Optional[str] = Field(default=None, alias="executionId")
    source_ids: Optional[list] = Field(default=None, alias="sourceIds")
    workflow_id: Optional[str] = Field(default=None, alias="workflowId")
    workflow_version: Optional[str] = Field(default=None, alias="workflowVersion")
    workflow_name: Optional[str] = Field(default=None, alias="workflowName")
    component_id: Optional[str] = Field(default=None, alias="componentId")
    component_name: Optional[str] = Field(default=None, alias="componentName")
    component_type: Optional[str] = Field(default=None, alias="componentType")
    # for loop component
    loop_node_id: Optional[str] = Field(default=None, alias="loopNodeId")
    loop_index: Optional[int] = Field(default=None, alias="loopIndex")
    # for llm invoke data
    llm_invoke_data: Optional[Dict[str, dict]] = Field(default=None, exclude=True)  # model data
    # for subworkflow
    parent_node_id: Optional[str] = Field(default=None, alias="parentNodeId")
    stream_inputs: Optional[list] = Field(default=None, alias="streamInputs")
    # for component stream output
    stream_outputs: Optional[list] = Field(default=None, alias="streamOutputs")
    # for interactive inputs
    interactive_inputs: Optional[Any] = Field(default=None, alias="interactiveInputs")


    def append_stream_output(self, chunk):
        if self.stream_outputs is None:
            self.stream_outputs = []
        self.stream_outputs.append(chunk)

    def append_stream_inputs(self, chunk):
        if self.stream_inputs is None:
            self.stream_inputs = []
        self.stream_inputs.append(chunk)


class SpanManager:
    """Managing spans during tracer handler session"""

    def __init__(self, trace_id: str, parent_node_id: str = ""):
        self._trace_id = trace_id
        self._parent_node_id = parent_node_id
        self._order = []
        self._session_spans = {}

    def get_span(self, invoke_id: str):
        if invoke_id not in self._order:
            return None
        return self._session_spans.get(invoke_id, None)

    def pop_span(self, invoke_id: str):
        if invoke_id not in self._order:
            return
        self._order.remove(invoke_id)
        self._session_spans.pop(invoke_id)

    def refresh_span_record(self, invoke_id: str, session_span: Dict[str, Span]):
        if invoke_id not in self._order:
            self._order.append(invoke_id)
        self._session_spans[invoke_id] = session_span[invoke_id]

    def _refresh_parent_child_span(self, span, parent_span=None):
        if parent_span:
            parent_span.append_child_invoke_id(span.invoke_id)
            self.refresh_span_record(parent_span.invoke_id, {parent_span.invoke_id: parent_span})
        self.refresh_span_record(span.invoke_id, {span.invoke_id: span})

    def create_agent_span(self, parent_span: Optional[TraceAgentSpan] = None) -> TraceAgentSpan:
        invoke_id = str(uuid.uuid4())
        span = TraceAgentSpan(invoke_id=invoke_id, parent_invoke_id=parent_span.invoke_id if parent_span else None,
                              trace_id=self._trace_id)
        self._refresh_parent_child_span(span, parent_span)
        return span

    def create_workflow_span(self, invoke_id: str,
                             parent_span: Optional[TraceWorkflowSpan] = None) -> TraceWorkflowSpan:
        span = TraceWorkflowSpan(invoke_id=invoke_id, parent_invoke_id=parent_span.invoke_id if parent_span else None,
                                 trace_id=self._trace_id, parent_node_id=self._parent_node_id,
                                 execution_id=self._trace_id)
        self._refresh_parent_child_span(span, parent_span)
        return span

    def update_span(self, span: Span, data: dict):
        span.update(data)
        self.refresh_span_record(span.invoke_id, {span.invoke_id: span})

    def end_span(self):
        pass

    @property
    def last_span(self):
        if not self._order:
            return None
        last_span_id = self._order[-1]
        if last_span_id not in self._session_spans:
            return None
        return self._session_spans[last_span_id]
