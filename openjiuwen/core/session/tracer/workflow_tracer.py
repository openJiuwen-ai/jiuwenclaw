# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Optional

from openjiuwen.core.common.constants.constant import LOOP_ID, INDEX
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.utils import NESTED_PATH_SPLIT
from openjiuwen.core.session.tracer.handler import TracerHandlerName


class TracerWorkflowUtils:
    @staticmethod
    def _get_workflow_metadata(session) -> dict:
        executable_id = session.workflow_id()
        workflow_config = session.config().get_workflow_config(executable_id)
        workflow_metadata = workflow_config.card if workflow_config else None
        return {
            "workflow_id": executable_id,
            "workflow_version": workflow_metadata.version if workflow_metadata else '',
            "workflow_name": workflow_metadata.name if workflow_metadata else '',
        }

    @staticmethod
    def _get_component_metadata(session) -> dict:
        executable_id = session.executable_id()
        state = session.state()
        component_metadata = {
            "component_id": session.node_id(),
            "component_name": session.node_id(),
            "component_type": session.node_type(),
            "workflow_id": session.workflow_id()
        }
        loop_id = state.get_global(LOOP_ID)
        if loop_id is None:
            return component_metadata

        index = state.get_global(loop_id + NESTED_PATH_SPLIT + INDEX)
        component_metadata.update({
            "loop_node_id": loop_id,
            "loop_index": index
        })
        return component_metadata

    @staticmethod
    async def trace_workflow_start(session, inputs: Optional[dict]):
        tracer = session.tracer()
        if tracer is None:
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, event_name="on_call_start",
                             invoke_id=session.workflow_id(),
                             parent_node_id='',
                             metadata=TracerWorkflowUtils._get_workflow_metadata(session),
                             inputs=inputs,
                             need_send=True)

    @staticmethod
    async def trace_component_begin(session, source_ids: list = None):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_start",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             source_ids=source_ids,
                             metadata=TracerWorkflowUtils._get_component_metadata(session))

    @staticmethod
    async def trace_component_inputs(session, inputs: Optional[dict], send: bool = True):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_pre_invoke",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             inputs=inputs,
                             need_send=send,
                             component_metadata=TracerWorkflowUtils._get_component_metadata(session))

    @staticmethod
    async def trace_component_stream_input(session, chunk, send: bool = True):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        if isinstance(chunk, str):
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_pre_stream",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             need_send=send,
                             chunk=dict(chunk))

    @staticmethod
    async def trace_component_outputs(session, outputs: Optional[dict]):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_post_invoke",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             outputs=outputs)

    @staticmethod
    async def trace_component_stream_output(session, chunk):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        if isinstance(chunk, str):
            return
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_post_stream",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             chunk=dict(chunk))

    @staticmethod
    async def trace_workflow_done(session, outputs: Optional[dict]):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.workflow_id()
        parent_id = ""
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_done",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             outputs=outputs,
                             metadata=TracerWorkflowUtils._get_workflow_metadata(session))

    @staticmethod
    async def trace_component_done(session):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_call_done",
                             invoke_id=executable_id,
                             parent_node_id=parent_id)
        state = session.state()
        loop_id = state.get_global(LOOP_ID)
        if loop_id is None:
            return
        session.tracer().pop_workflow_span(executable_id, session.parent_id())

    @staticmethod
    async def trace(session, data: dict = None):
        tracer = session.tracer()
        if tracer is None:
            return
        invoke_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_invoke",
                             invoke_id=invoke_id,
                             parent_node_id=parent_id,
                             on_invoke_data=data)

    @staticmethod
    async def trace_error(session, error: Exception):
        tracer = session.tracer()
        if tracer is None:
            return
        if error is None:
            raise build_error(StatusCode.TRACER_WORKFLOW_TRACE_ERROR, reason="'trace_error''s error is None")
        invoke_id = session.executable_id()
        parent_id = session.parent_id()
        await session.tracer().trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_invoke",
                                       invoke_id=invoke_id,
                                       parent_node_id=parent_id,
                                       exception=error)

    @staticmethod
    async def trace_component_interactive_inputs(session, inputs: Optional[dict], send: bool = True):
        tracer = session.tracer()
        if tracer is None:
            return
        executable_id = session.executable_id()
        parent_id = session.parent_id()
        await tracer.trigger(TracerHandlerName.TRACER_WORKFLOW.value, "on_interact",
                             invoke_id=executable_id,
                             parent_node_id=parent_id,
                             inputs=inputs,
                             need_send=send,
                             component_metadata=TracerWorkflowUtils._get_component_metadata(session))
