# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

from openjiuwen.core.runner.callback.decorator import WrapHandler, create_wrap_decorator
from openjiuwen.core.session.tracer.data import InvokeType


class _TraceProxy:
    """Lightweight proxy that shadows specific methods with traced versions."""

    def __init__(self, wrapped: Any) -> None:
        self._wrapped = wrapped

    def __getattr__(self, name: str) -> Any:
        return getattr(self._wrapped, name)


def _should_decorate(obj, session):
    return (obj and
            session and
            hasattr(session, "tracer") and
            session.tracer() and
            hasattr(session, "span"))


def _make_trace_wrap_handler(
    session,
    invoke_type: InvokeType,
    instance_info: dict,
    index: int = 0,
    inputs_field_name: str = "inputs",
) -> WrapHandler:
    async def handler(call_next, /, *args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger(
                "tracer_agent", f"on_{invoke_type.value}_start",
                span=span,
                inputs={"inputs": args[index] if len(args) > index else kwargs.get(inputs_field_name, {})},
                instance_info=instance_info,
            )
            call_kwargs = dict(kwargs)
            if invoke_type == InvokeType.LLM:
                async def tracer_record_data(**kw):
                    await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_request", span=span, **kw)
                call_kwargs["tracer_record_data"] = tracer_record_data
            result = await call_next(*args, **call_kwargs)
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_end",
                                 span=span, outputs={"outputs": result})
            return result
        except Exception as error:
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_error", span=span, error=error)
            raise

    return handler


def async_trace(func, session, invoke_type: InvokeType, instance_info,
                index: int = 0, inputs_field_name: str = "inputs"):
    handler = _make_trace_wrap_handler(session, invoke_type, instance_info, index, inputs_field_name)
    return create_wrap_decorator(handler)(func)


def _make_trace_stream_wrap_handler(
    session,
    invoke_type: InvokeType,
    instance_info: dict,
    index: int = 0,
    inputs_field_name: str = "inputs",
) -> WrapHandler:
    async def handler(call_next, /, *args, **kwargs):
        tracer = session.tracer()
        span = None
        try:
            agent_span = session.span()
            span = tracer.tracer_agent_span_manager.create_agent_span(agent_span)
            await tracer.trigger(
                "tracer_agent", f"on_{invoke_type.value}_start",
                span=span,
                inputs={"inputs": args[index] if len(args) > index else kwargs.get(inputs_field_name, {})},
                instance_info=instance_info,
            )
            call_kwargs = dict(kwargs)
            if invoke_type == InvokeType.LLM:
                async def tracer_record_data(**kw):
                    await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_request", span=span, **kw)
                call_kwargs["tracer_record_data"] = tracer_record_data
            results = []
            async for item in call_next(*args, **call_kwargs):
                results.append(item)
                yield item
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_end",
                                 span=span, outputs={"outputs": results})
        except Exception as error:
            await tracer.trigger("tracer_agent", f"on_{invoke_type.value}_error", span=span, error=error)
            raise

    return handler


def async_trace_stream(func, session, invoke_type: InvokeType, instance_info,
                       index: int = 0, inputs_field_name: str = "inputs"):
    handler = _make_trace_stream_wrap_handler(session, invoke_type, instance_info, index, inputs_field_name)

    async def _as_async_gen(*args, **kwargs):
        """Normalize func to async generator so create_wrap_decorator takes the generator path."""
        result = func(*args, **kwargs)
        if hasattr(result, "__aiter__") or hasattr(result, "__anext__"):
            async for item in result:
                yield item

    return create_wrap_decorator(handler)(_as_async_gen)


def decorate_model_with_trace(model, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return model
    session = getattr(agent_session, "_inner")
    if not _should_decorate(model, session):
        return model
    try:
        model_name = model.config.model_config.model_name
    except Exception:
        model_name = type(model).__name__
    instance_info = {"class_name": model_name, "type": "llm"}
    proxy = _TraceProxy(model)
    proxy.invoke = async_trace(model.invoke, session, InvokeType.LLM, instance_info,
                               index=1, inputs_field_name="messages")
    proxy.stream = async_trace_stream(model.stream, session, InvokeType.LLM, instance_info,
                                      index=1, inputs_field_name="messages")
    return proxy


def decorate_tool_with_trace(tool, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return tool
    session = getattr(agent_session, "_inner")
    if not _should_decorate(tool, session):
        return tool
    instance_info = {"class_name": tool.card.name if hasattr(tool, "card") else type(tool).__name__, "type": "tool"}
    proxy = _TraceProxy(tool)
    proxy.invoke = async_trace(tool.invoke, session, InvokeType.PLUGIN, instance_info)
    return proxy


def decorate_workflow_with_trace(workflow, agent_session):
    if not agent_session or not hasattr(agent_session, "_inner"):
        return workflow
    session = getattr(agent_session, "_inner")
    if not _should_decorate(workflow, session):
        return workflow
    metadata = dict(id=workflow.card.id, name=workflow.card.name,
                    description=workflow.card.description,
                    version=workflow.card.version) if workflow else {}
    try:
        workflow_name = workflow.card.name
    except Exception:
        workflow_name = type(workflow).__name__
    instance_info = {"class_name": workflow_name, "type": "workflow", "metadata": dict(metadata)}
    proxy = _TraceProxy(workflow)
    proxy.invoke = async_trace(workflow.invoke, session, InvokeType.WORKFLOW, instance_info)
    proxy.stream = async_trace_stream(workflow.stream, session, InvokeType.WORKFLOW, instance_info)
    return proxy
