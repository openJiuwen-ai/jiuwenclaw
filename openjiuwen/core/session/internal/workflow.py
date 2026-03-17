# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import Any

from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.state.base import State
from openjiuwen.core.session.state.workflow_state import InMemoryState
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.tracer import Tracer


class WorkflowSession(BaseSession):
    def __init__(self, workflow_id: str = '', parent: BaseSession = None, session_id: str = None, state: State = None):
        self._session_id = session_id
        self._parent = parent
        if parent is not None:
            if self._session_id is None:
                self._session_id = parent.session_id()
            self._config = parent.config()
            self._tracer = parent.tracer()
        else:
            if self._session_id is None:
                self._session_id = uuid.uuid4().hex
            self._config = Config()
            self._tracer = None

        self._state = state if state is not None else InMemoryState()
        self._stream_writer_manager = None  # type: StreamWriterManager
        self._actor_manager = None
        self._workflow_id = workflow_id

    def set_stream_writer_manager(self, stream_writer_manager: StreamWriterManager) -> None:
        if self._stream_writer_manager is not None:
            return
        self._stream_writer_manager = stream_writer_manager

    def set_tracer(self, tracer: Tracer) -> None:
        self._tracer = tracer

    def set_actor_manager(self, queue_manager: "ActorManager"):
        if self._actor_manager is not None:
            return
        self._actor_manager = queue_manager

    def set_workflow_id(self, workflow_id):
        self._workflow_id = workflow_id

    def actor_manager(self) -> "ActorManager":
        return self._actor_manager

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def session_id(self) -> str:
        return self._session_id

    def checkpointer(self):
        if self._parent is not None:
            return self._parent.checkpointer()
        # Lazy import to avoid circular import
        from openjiuwen.core.session.checkpointer.checkpointer import CheckpointerFactory
        return CheckpointerFactory.get_checkpointer()

    def workflow_id(self):
        return self._workflow_id

    def main_workflow_id(self):
        return self.workflow_id()

    def workflow_nesting_depth(self):
        return 0

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()

    def parent(self):
        return self._parent


def create_parent_id(session: BaseSession):
    return session.executable_id() if isinstance(session, NodeSession) else ''


def create_executable_id(node_id: str, parent_id: str):
    return parent_id + "." + node_id if len(parent_id) != 0 else node_id


class NodeSession(BaseSession):
    def __init__(self, session: BaseSession, node_id: str, node_type: str = None, skip_trace: bool = False):
        self._node_id = node_id
        self._node_type = node_type
        parent_id = create_parent_id(session)
        executable_id = create_executable_id(node_id, parent_id)
        state = session.state().create_node_state(executable_id, parent_id)
        self._state = state
        self._parent_id = parent_id
        self._executable_id = executable_id
        self._session = session
        self._workflow_id = session.workflow_id()
        self._workflow_nesting_depth = session.workflow_nesting_depth()
        self._main_workflow_id = session.main_workflow_id()
        self._skip_trace = skip_trace

    def node_id(self):
        return self._node_id

    def node_type(self):
        return self._node_type

    def executable_id(self):
        return self._executable_id

    def parent_id(self):
        return self._parent_id

    def workflow_id(self):
        return self._workflow_id

    def main_workflow_id(self):
        return self._main_workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def actor_manager(self) -> "ActorManager":
        return self._session.actor_manager()

    def parent(self):
        return self._session

    def tracer(self) -> Tracer:
        return self._session.tracer()

    def state(self) -> State:
        return self._state

    def config(self) -> Config:
        return self._session.config()

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._session.stream_writer_manager()

    def session_id(self) -> str:
        return self._session.session_id()

    def checkpointer(self):
        # NodeSession delegates to parent session's checkpointer
        return self._session.checkpointer()

    def node_config(self):
        workflow_config = self.config().get_workflow_config(self.workflow_id())
        if workflow_config:
            return workflow_config.spec.comp_configs.get(self._node_id)
        else:
            return None

    def skip_trace(self):
        return self._skip_trace


class SubWorkflowSession(NodeSession):
    def __init__(self, session: NodeSession, workflow_id: str, actor_manager: "ActorManager" = None):
        super().__init__(session=session.parent(), node_id=session.node_id(), node_type=session.node_type())
        self._workflow_id = workflow_id
        self._workflow_nesting_depth = session.workflow_nesting_depth() + 1
        self._main_workflow_id = session.main_workflow_id()
        self._actor_manager = actor_manager

    def workflow_id(self):
        return self._workflow_id

    def workflow_nesting_depth(self):
        return self._workflow_nesting_depth

    def main_workflow_id(self):
        return self._main_workflow_id

    def actor_manager(self) -> "ActorManager":
        return self._actor_manager

    async def close(self):
        if self._actor_manager is not None:
            await self._actor_manager.shutdown()
