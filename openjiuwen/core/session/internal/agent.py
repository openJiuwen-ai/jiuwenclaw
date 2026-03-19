# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

from openjiuwen.core.session.checkpointer import (
    Checkpointer,
    CheckpointerFactory,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.internal.workflow import WorkflowSession
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.core.session.state.base import (
    InMemoryCommitState,
    State,
)
from openjiuwen.core.session.state.workflow_state import InMemoryState
from openjiuwen.core.session.stream.emitter import StreamEmitter
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.tracer import Tracer


class AgentSession(BaseSession):
    def __init__(
            self,
            session_id: str,
            config: Config = None,
            checkpointer: Checkpointer | None = None,
            card=None,
            stream_writer_manager: StreamWriterManager | None = None):
        self._session_id = session_id
        self._config = config
        self._state = StateCollection()
        self._stream_writer_manager = (
            stream_writer_manager
            if stream_writer_manager is not None
            else StreamWriterManager(StreamEmitter())
        )
        tracer = Tracer()
        tracer.init(self._stream_writer_manager)
        self._tracer = tracer
        self._checkpointer = CheckpointerFactory.get_checkpointer() if checkpointer is None else checkpointer
        self._agent_span = self._tracer.tracer_agent_span_manager.create_agent_span() if self._tracer else None
        self._card = card

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def span(self):
        return self._agent_span

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def session_id(self) -> str:
        return self._session_id

    def checkpointer(self) -> Checkpointer:
        return self._checkpointer

    def create_workflow_session(self) -> WorkflowSession:
        state = self._state.global_state
        return WorkflowSession(
            parent=self,
            state=InMemoryState(InMemoryCommitState(state)),
            session_id=self._session_id)

    def agent_id(self):
        agent_config = self._config.get_agent_config()
        if agent_config is not None:
            return agent_config.id
        return self._card.id
