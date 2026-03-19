# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Any

from openjiuwen.core.session.checkpointer import (
    Checkpointer,
    CheckpointerFactory,
)
from openjiuwen.core.session.config.base import Config
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.state.agent_state import StateCollection
from openjiuwen.core.session.state.base import State
from openjiuwen.core.session.stream.emitter import StreamEmitter
from openjiuwen.core.session.stream.manager import StreamWriterManager
from openjiuwen.core.session.tracer.tracer import Tracer


class AgentGroupSession(BaseSession):
    def __init__(
            self,
            session_id: str,
            group_id: str,
            config: Config = None,
            checkpointer: Checkpointer | None = None,
            stream_writer_manager: StreamWriterManager | None = None):
        self._session_id = session_id
        self._group_id = group_id
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
        self._group_span = self._tracer.tracer_agent_span_manager.create_agent_span() if self._tracer else None

    def config(self) -> Config:
        return self._config

    def state(self) -> State:
        return self._state

    def tracer(self) -> Any:
        return self._tracer

    def span(self):
        return self._group_span

    def stream_writer_manager(self) -> StreamWriterManager:
        return self._stream_writer_manager

    def session_id(self) -> str:
        return self._session_id

    def checkpointer(self) -> Checkpointer:
        return self._checkpointer

    def group_id(self) -> str:
        return self._group_id
