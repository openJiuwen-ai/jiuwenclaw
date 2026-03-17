# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import Optional, Any, Union

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.session.interaction.interaction import WorkflowInteraction
from openjiuwen.core.session.internal.workflow import NodeSession
from openjiuwen.core.session.stream import StreamWriter, OutputSchema
from openjiuwen.core.session.tracer import TracerWorkflowUtils


class Session:
    def __init__(self, session: NodeSession, stream_mode: bool = False):
        self._inner = session
        self._interaction = None
        self._stream_mode = stream_mode
        self._description = f'[wf_id={self.get_workflow_id()},comp_id={self.get_component_id()}]'

    def get_workflow_id(self):
        return self._inner.workflow_id()

    def get_component_id(self):
        return self._inner.node_id()

    def get_component_type(self):
        return self._inner.node_type()

    def get_component_descrip(self):
        return self._description

    async def trace(self, data: dict):
        if self._inner.skip_trace():
            return
        await TracerWorkflowUtils.trace(self._inner, data)

    async def trace_error(self, error: Exception):
        if self._inner.skip_trace():
            return
        await TracerWorkflowUtils.trace_error(self._inner, error)

    async def interact(self, value):
        if self._stream_mode:
            raise build_error(StatusCode.COMP_SESSION_INTERACT_ERROR, comp_id=self.get_component_id(),
                              workflow=self.get_workflow_id(),
                              reason="interact when streaming process(transform or collect) is not supported")
        if self._interaction is None:
            self._interaction = WorkflowInteraction(self._inner)
        user_inputs = await self._interaction.wait_user_inputs(value)
        if not self._inner.skip_trace():
            await TracerWorkflowUtils.trace_component_interactive_inputs(self._inner, user_inputs)
        return user_inputs

    def get_executable_id(self) -> str:
        return self._inner.executable_id()

    def get_session_id(self) -> str:
        return self._inner.session_id()

    def update_state(self, data: dict):
        return self._inner.state().update(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get(key)

    def update_global_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    def dump_state(self) -> dict:
        return self._inner.state().dump()

    async def write_stream(self, data: Union[dict, OutputSchema]):
        writer = self._stream_writer()
        if writer:
            await writer.write(data)

    async def write_custom_stream(self, data: dict):
        writer = self._custom_writer()
        if writer:
            await writer.write(data)

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)

    def _stream_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_output_writer()
        return None

    def _custom_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_custom_writer()
        return None
