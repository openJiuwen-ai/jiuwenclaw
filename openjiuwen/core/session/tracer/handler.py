# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import copy
import json
from abc import abstractmethod
from datetime import datetime
from enum import Enum
from typing import Any

from dateutil.tz import tzlocal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.data import InvokeType, NodeStatus
from openjiuwen.core.session.tracer.span import Span, TraceAgentSpan, TraceWorkflowSpan
from openjiuwen.core.session.tracer.span import SpanManager
from openjiuwen.core.graph.pregel import GraphInterrupt


class TracerHandlerName(Enum):
    """
    Trigger handler name.
    """
    TRACE_AGENT = "tracer_agent"
    TRACER_WORKFLOW = "tracer_workflow"


class TraceBaseHandler:
    def __init__(self, stream_writer_manager: StreamWriterManager, span_manager: SpanManager):
        self._stream_writer = stream_writer_manager.get_trace_writer()
        self._span_manager = span_manager

    async def emit_stream_writer(self, data):
        await self._emit_stream_writer(data)

    @abstractmethod
    def _format_data(self, span: Span) -> dict:
        ...

    async def _emit_stream_writer(self, span):
        if self._stream_writer is None:
            return
        await self._stream_writer.write(self._format_data(span))

    async def _send_data(self, span, exclude=None):
        if exclude:
            clean_dict = span.model_dump(exclude=exclude)
            await self.emit_stream_writer(type(span).model_validate(clean_dict))
            return
        await self.emit_stream_writer(copy.deepcopy(span))

    @staticmethod
    def _get_elapsed_time(start_time: datetime, end_time: datetime) -> str:
        """get elapsed time"""
        elapsed_time = end_time - start_time
        ms = elapsed_time.total_seconds() * 1000
        if ms < 1000:
            return f"{ms:.0f}ms"
        return f"{(ms / 1000):.2f}s"

    @staticmethod
    def _get_node_status(span: Span) -> str:
        if span.error:
            return NodeStatus.ERROR.value
        if span.on_invoke_data:
            return NodeStatus.RUNNING.value if not span.end_time else NodeStatus.FINISH.value
        if span.end_time:
            return NodeStatus.FINISH.value
        return NodeStatus.START.value


class TraceAgentHandler(TraceBaseHandler):
    def __init__(self, stream_writer_manager, span_manager):
        super().__init__(stream_writer_manager, span_manager)

    def event_name(self):
        return TracerHandlerName.TRACE_AGENT.value

    def _format_data(self, span: TraceAgentSpan) -> dict:
        if span.status != NodeStatus.INTERRUPTED.value:
            span.status = self._get_node_status(span)
        return {"type": self.event_name(), "payload": span.model_dump(by_alias=True)}

    def _get_tracer_agent_span(self, invoke_id: str) -> TraceAgentSpan:
        span = self._span_manager.get_span(invoke_id)
        if span is not None:
            return span
        return self._span_manager.create_agent_span(self._span_manager.last_span)

    def _update_start_trace_data(self, span: TraceAgentSpan, invoke_type: str, inputs: Any, instance_info: dict,
                                 **kwargs):
        try:
            meta_data = json.loads(
                json.dumps(instance_info, ensure_ascii=False,
                           default=lambda _obj: f"<<no-serializable: {type(_obj).__qualname__}>>")
            )
        except json.decoder.JSONDecodeError as err:
            session_logger.error(
                "Failed to process metadata for trace",
                event_type=LogEventType.SYSTEM_ERROR,
                metadata={"error": str(err), "instance_info": str(instance_info)}
            )
            raise ValueError(f"meta_data error: Decoder error") from err

        update_data = {
            "start_time": datetime.now(tz=tzlocal()).replace(tzinfo=None),
            "invoke_type": invoke_type,
            "inputs": inputs,
            "instance_info": instance_info,
            "name": instance_info["class_name"],
            "meta_data": meta_data
        }
        self._span_manager.update_span(span, update_data)

    def _update_end_trace_data(self, span: TraceAgentSpan, outputs, **kwargs):
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {
            "end_time": end_time,
            "outputs": outputs
        }
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)

    def _update_error_trace_data(self, span: TraceAgentSpan, error, **kwargs):
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        if isinstance(error, BaseError):
            error_info = {"error_code": error.status.code, "message": error.message}
        else:
            error_info = {"error_code": StatusCode.WORKFLOW_EXECUTION_ERROR.code,
                          "message": StatusCode.WORKFLOW_EXECUTION_ERROR.errmsg.format(reason=str(error), workflow="")}
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {
            "end_time": end_time,
            "error": error_info
        }
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)

    def _update_running_trace_data(self, span: TraceAgentSpan, **kwargs):
        if not isinstance(span.on_invoke_data, list):
            span.on_invoke_data = []
        span.on_invoke_data.append(kwargs)

        self._span_manager.update_span(span, {})

    async def on_chain_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.CHAIN.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_chain_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_chain_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_llm_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.LLM.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_llm_request(self, span: TraceAgentSpan, **kwargs):
        self._update_running_trace_data(span=span, **kwargs)
        await self._send_data(span)

    async def on_llm_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_llm_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_prompt_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.PROMPT.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_prompt_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_prompt_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_plugin_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.PLUGIN.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_plugin_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_plugin_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_retriever_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.RETRIEVER.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_retriever_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_retriever_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_evaluator_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.EVALUATOR.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_evaluator_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_evaluator_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)

    async def on_workflow_start(self, span: TraceAgentSpan, inputs: Any, instance_info: dict, **kwargs):
        self._update_start_trace_data(invoke_type=InvokeType.WORKFLOW.value, span=span, inputs=inputs,
                                      instance_info=instance_info, **kwargs)
        await self._send_data(span)

    async def on_workflow_end(self, span: TraceAgentSpan, outputs, **kwargs):
        self._update_end_trace_data(span=span, outputs=outputs, **kwargs)
        await self._send_data(span)

    async def on_workflow_error(self, span: TraceAgentSpan, error, **kwargs):
        self._update_error_trace_data(span=span, error=error, **kwargs)
        await self._send_data(span)


class TraceWorkflowHandler(TraceBaseHandler):
    def __init__(self, stream_writer_manager, span_manager):
        super().__init__(stream_writer_manager, span_manager)

    def event_name(self) -> str:
        return TracerHandlerName.TRACER_WORKFLOW.value

    def _format_data(self, span: TraceWorkflowSpan) -> dict:
        if span.status != NodeStatus.INTERRUPTED.value:
            span.status = self._get_node_status(span)
        result = span.model_dump(exclude_none=True, by_alias=True, exclude={"child_invokes_id", "llm_invoke_data"})
        return {"type": self.event_name(),
                "payload": result}

    def _get_tracer_workflow_span(self, invoke_id: str) -> TraceWorkflowSpan:
        span = self._span_manager.get_span(invoke_id)
        if span is not None:
            return span
        return self._span_manager.create_workflow_span(invoke_id, self._span_manager.last_span)

    async def on_call_start(self, invoke_id: str, metadata: dict = None, inputs: Any = None,
                            need_send: bool = False, source_ids: list = None,
                            **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {
            "start_time": datetime.now(tz=tzlocal()).replace(tzinfo=None),
            "invoke_type": type,
            "on_invoke_data": [],
            "inputs": inputs,
            "outputs": None,
            "stream_outputs": [],
            "source_ids": source_ids,
            **metadata
        }
        self._span_manager.update_span(span, update_data)
        if need_send:
            await self._send_data(span)

    async def on_pre_invoke(self, invoke_id: str, inputs: Any, component_metadata: dict, need_send: bool = False,
                            **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)

        update_data = {
            "inputs": inputs,
            **component_metadata
        }
        self._span_manager.update_span(span, update_data)
        if need_send:
            await self._send_data(span, exclude={"outputs", "stream_outputs"})

    async def on_pre_stream(self, invoke_id: str, chunk, need_send: bool = False, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        if chunk and isinstance(chunk, dict):
            span.append_stream_inputs(chunk)
        if need_send:
            await self._send_data(span, exclude={"outputs", "stream_outputs"})

    async def on_invoke(self, invoke_id: str, on_invoke_data: dict = None, exception: Exception = None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {}
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        if exception is not None:
            if isinstance(exception, BaseError):
                span.error = {"error_code": exception.code, "message": exception.message}
            elif isinstance(exception, GraphInterrupt):
                span.status = NodeStatus.INTERRUPTED.value
            else:
                span.error = {"error_code": StatusCode.WORKFLOW_EXECUTION_ERROR.code,
                              "message": StatusCode.WORKFLOW_EXECUTION_ERROR.errmsg.format(reason=str(exception))}
            if on_invoke_data:
                span.on_invoke_data.append(on_invoke_data)
            update_data = {
                "end_time": end_time
            }
            elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
            if elapsed_time is not None:
                update_data["elapsed_time"] = elapsed_time
        else:
            if not isinstance(span.on_invoke_data, list):
                span.on_invoke_data = []
            span.on_invoke_data.append(on_invoke_data)
        self._span_manager.update_span(span, update_data)

        await self._send_data(span)
        if exception and span.component_type == "LLM":
            self._span_manager.update_span(span, {})

    async def on_post_stream(self, invoke_id: str, chunk, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        span.append_stream_output(chunk)

    async def on_post_invoke(self, invoke_id: str, outputs, inputs=None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        update_data = {
            "outputs": outputs,
        }
        if inputs and span.component_type in ["End", "Message"]:
            span.inputs = inputs
        self._span_manager.update_span(span, update_data)

    async def on_call_done(self, invoke_id, outputs: Any = None, **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)
        end_time = datetime.now(tz=tzlocal()).replace(tzinfo=None)
        elapsed_time = self._get_elapsed_time(span.start_time, end_time) if span.start_time else None
        update_data = {"end_time": end_time, "outputs": outputs} if outputs is not None else {"end_time": end_time}
        if elapsed_time is not None:
            update_data["elapsed_time"] = elapsed_time
        self._span_manager.update_span(span, update_data)
        await self._send_data(span)
        if span.component_type == "End" and span.end_time:
            self._span_manager.update_span(span, {})

    async def on_interact(self, invoke_id: str, inputs: Any, component_metadata: dict, need_send: bool = False,
                          **kwargs):
        span = self._get_tracer_workflow_span(invoke_id)

        update_data = {
            "interactive_inputs": inputs,
            **component_metadata
        }
        self._span_manager.update_span(span, update_data)
        if need_send:
            await self._send_data(span, exclude={"outputs", "stream_outputs"})
