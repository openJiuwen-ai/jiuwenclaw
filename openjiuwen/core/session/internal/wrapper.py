# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Union, Any, Optional, List, Tuple, AsyncIterator

from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.session.stream.writer import StreamWriter
from openjiuwen.core.session.tracer.workflow_tracer import TracerWorkflowUtils


class WrappedSession(ABC):
    def __init__(self, inner: BaseSession):
        self._inner = inner

    def get_workflow_config(self, workflow_id):
        return self._inner.config().get_workflow_config(workflow_id)

    def get_agent_config(self):
        return self._inner.config().get_agent_config()

    def get_env(self, key) -> Optional[Any]:
        return self._inner.config().get_env(key)

    def base(self) -> BaseSession:
        return self._inner

    @abstractmethod
    def executable_id(self) -> str:
        pass

    @abstractmethod
    def session_id(self) -> str:
        pass

    def user_id(self) -> str:
        return ''

    @abstractmethod
    def update_state(self, data: dict):
        pass

    @abstractmethod
    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    @abstractmethod
    def update_global_state(self, data: dict):
        pass

    @abstractmethod
    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        pass

    @abstractmethod
    def stream_writer(self) -> Optional[StreamWriter]:
        pass

    @abstractmethod
    def custom_writer(self) -> Optional[StreamWriter]:
        pass

    @abstractmethod
    async def write_stream(self, data: Union[dict, OutputSchema]):
        pass

    @abstractmethod
    async def write_custom_stream(self, data: dict):
        pass

    @abstractmethod
    async def trace(self, data: dict):
        pass

    @abstractmethod
    async def trace_error(self, error: Exception):
        pass

    @abstractmethod
    async def interact(self, value):
        pass

    async def post_run(self):
        pass

    async def pre_run(self, **kwargs):
        pass

    async def release(self, session_id: str):
        pass


class StateSession(WrappedSession, ABC):

    def executable_id(self) -> str:
        return self._inner.executable_id()

    def session_id(self) -> str:
        return self._inner.session_id()

    def update_state(self, data: dict):
        return self._inner.state().update(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get(key)

    def update_global_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_global_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    def stream_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_output_writer()
        return None

    def custom_writer(self) -> Optional[StreamWriter]:
        manager = self._inner.stream_writer_manager()
        if manager:
            return manager.get_custom_writer()
        return None

    async def write_stream(self, data: Union[dict, OutputSchema]):
        writer = self.stream_writer()
        if writer:
            await writer.write(data)

    async def write_custom_stream(self, data: dict):
        writer = self.custom_writer()
        if writer:
            await writer.write(data)


class RouterSession(StateSession):
    async def interact(self, value):
        pass

    async def trace(self, data: dict):
        await TracerWorkflowUtils.trace(self._inner, data)

    def stream_writer(self) -> Optional[StreamWriter]:
        pass

    def custom_writer(self) -> Optional[StreamWriter]:
        pass

    async def write_stream(self, data: Union[dict, OutputSchema]):
        pass

    async def write_custom_stream(self, data: dict):
        pass

    async def trace_error(self, error: Exception):
        await TracerWorkflowUtils.trace_error(self._inner, error)

    def update_global_state(self, data: dict):
        pass

    def update_state(self, data: dict):
        pass

    def get_workflow_config(self, workflow_id):
        pass

    def get_agent_config(self):
        pass

    def get_env(self, key) -> Optional[Any]:
        pass

    def base(self) -> BaseSession:
        pass