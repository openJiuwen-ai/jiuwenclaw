# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
import uuid

from openjiuwen.core.session.tracer.handler import TraceAgentHandler, TraceWorkflowHandler, TracerHandlerName
from openjiuwen.core.session.tracer.span import SpanManager


class Tracer:
    def __init__(self):
        self._trace_id = str(uuid.uuid4())
        self.tracer_agent_span_manager = SpanManager(self._trace_id)
        self.tracer_workflow_span_manager_dict = {}
        self._handlers = {}
        self._stream_writer_manager = None

    def init(self, stream_writer_manager):
        self._stream_writer_manager = stream_writer_manager
        agent_handler = TraceAgentHandler(stream_writer_manager, self.tracer_agent_span_manager)
        parent_wf_span_manager = SpanManager(self._trace_id)
        wf_handler = TraceWorkflowHandler(stream_writer_manager, parent_wf_span_manager)
        self.tracer_workflow_span_manager_dict[""] = parent_wf_span_manager
        self._handlers[TracerHandlerName.TRACE_AGENT.value] = agent_handler
        self._handlers[TracerHandlerName.TRACER_WORKFLOW.value] = wf_handler

    def register_workflow_span_manager(self, parent_node_id: str):
        span_manager = SpanManager(self._trace_id, parent_node_id=parent_node_id)
        self.tracer_workflow_span_manager_dict[parent_node_id] = span_manager
        handler = TraceWorkflowHandler(self._stream_writer_manager, span_manager)
        self._handlers[TracerHandlerName.TRACER_WORKFLOW.value + "." + parent_node_id] = handler

    def get_workflow_span(self, invoke_id: str, parent_node_id: str):
        workflow_span_manager = self.tracer_workflow_span_manager_dict.get(parent_node_id, None)
        if workflow_span_manager is None:
            return None
        return workflow_span_manager.get_span(invoke_id)

    async def trigger(self, handler_class_name: str, event_name: str, **kwargs):
        parent_node_id = kwargs.get("parent_node_id", None)
        if parent_node_id is not None:
            handler_class_name += "." + parent_node_id if parent_node_id != "" else ""
        handler = self._handlers.get(handler_class_name)
        if handler and hasattr(handler, event_name):
            await getattr(handler, event_name)(**kwargs)

    def sync_trigger(self, handler_class_name: str, event_name: str, **kwargs):
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.run_coroutine_threadsafe(self.trigger(handler_class_name, event_name, **kwargs), loop)
        else:
            loop.run_until_complete(self.trigger(handler_class_name, event_name, **kwargs))

    def pop_workflow_span(self, invoke_id: str, parent_node_id: str):
        if parent_node_id not in self.tracer_workflow_span_manager_dict:
            return
        self.tracer_workflow_span_manager_dict.get(parent_node_id).pop_span(invoke_id)
