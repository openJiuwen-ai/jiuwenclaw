# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import (
    Any,
    AsyncIterator,
    TYPE_CHECKING,
    Union,
)

from openjiuwen.core.session import (
    Config,
)
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.interaction.interaction import SimpleAgentInteraction
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.stream import BaseStreamMode, OutputSchema
from openjiuwen.core.session.workflow import Session as WorkflowSession

if TYPE_CHECKING:
    from openjiuwen.core.single_agent import AgentCard


class Session:
    def __init__(self, session_id: str = None, envs: dict[str, Any] = None, card: "AgentCard" = None):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = AgentSession(session_id=session_id, config=config, card=card)
        self._card = card
        self._pre_run_done = False
        self._post_run_done = False
        self._interaction = None

    def get_session_id(self) -> str:
        return self._session_id

    def get_env(self, key: str, default: Any = None) -> Any:
        return self._inner.config().get_env(key, default)

    def get_envs(self):
        return self._inner.config().get_envs()

    def get_agent_id(self):
        return self._card.id

    def get_agent_name(self):
        return self._card.name

    def get_agent_description(self):
        return self._card.description

    def update_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        return self._inner.state().get_global(key)

    def dump_state(self) -> dict:
        return self._inner.state().dump()

    async def write_stream(self, data: Union[dict, OutputSchema]):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.OUTPUT).write(data)

    async def write_custom_stream(self, data: dict):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.CUSTOM).write(data)

    def stream_iterator(self) -> AsyncIterator[Any]:
        return self._inner.stream_writer_manager(
        ).stream_output()

    async def pre_run(self, **kwargs):
        if self._pre_run_done:
            return
        inputs = kwargs.get("inputs")
        await CheckpointerFactory.get_checkpointer().pre_agent_execute(self._inner, inputs)
        self._pre_run_done = True

    async def post_run(self):
        if self._post_run_done:
            return
        await self._inner.stream_writer_manager().stream_emitter().close()
        await self._inner.checkpointer().post_agent_execute(self._inner)
        self._post_run_done = True

    def create_workflow_session(self) -> WorkflowSession:
        return WorkflowSession(parent=self._inner, session_id=self.get_session_id())

    async def interact(self, value):
        if self._interaction is None:
            self._interaction = SimpleAgentInteraction(self._inner)
        await self._interaction.wait_user_inputs(value)


def create_agent_session(session_id: str = None, envs: dict[str, Any] = None, card: "AgentCard" = None) -> Session:
    return Session(session_id=session_id, envs=envs, card=card)
