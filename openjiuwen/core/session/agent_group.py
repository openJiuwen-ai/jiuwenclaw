# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import uuid
from typing import Any

from openjiuwen.core.session import Config
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.session.internal.agent_group import AgentGroupSession
from openjiuwen.core.session.stream import (
    BaseStreamMode,
    OutputSchema,
)
from openjiuwen.core.single_agent import (
    create_agent_session,
    Session as AgentSession,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard


class Session:
    """AgentGroup Session"""

    def __init__(self, session_id: str = None, envs: dict[str, Any] = None, group_id: str = "agent_group"):
        if session_id is None:
            session_id = str(uuid.uuid4())
        self._session_id = session_id
        self._group_id = group_id
        config = Config()
        if envs is not None:
            config.set_envs(envs)
        self._inner = AgentGroupSession(session_id=session_id, group_id=group_id, config=config)
        self._pre_run_done = False
        self._post_run_done = False

    def get_session_id(self) -> str:
        return self._session_id

    def get_env(self, key: str, default: Any) -> Any:
        return self._inner.config().get_env(key, default)

    def get_group_id(self) -> str:
        return self._group_id

    def get_envs(self):
        return self._inner.config().get_envs()

    def update_state(self, data: dict):
        return self._inner.state().update_global(data)

    def get_state(self, key=None) -> Any:
        return self._inner.state().get_global(key)

    def dump_state(self) -> dict:
        return self._inner.state().dump()

    async def write_stream(self, data: dict | OutputSchema):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.OUTPUT).write(
            self._normalize_output_stream(self._tag_stream_payload(data))
        )

    async def write_custom_stream(self, data: dict):
        await self._inner.stream_writer_manager().get_writer(BaseStreamMode.CUSTOM).write(
            self._tag_stream_payload(data)
        )

    def stream_iterator(self):
        return self._inner.stream_writer_manager().stream_output()

    async def close_stream(self):
        await self._inner.stream_writer_manager().stream_emitter().close()

    async def pre_run(self, **kwargs):
        if self._pre_run_done:
            return
        await CheckpointerFactory.get_checkpointer().pre_agent_group_execute(self._inner, kwargs.get("inputs"))
        self._pre_run_done = True

    async def post_run(self):
        if self._post_run_done:
            return
        await self.close_stream()
        await self._inner.checkpointer().post_agent_group_execute(self._inner)
        self._post_run_done = True

    def create_agent_session(self, card: AgentCard | None = None, agent_id: str | None = None) -> AgentSession:
        if card is None:
            card = AgentCard(id=agent_id or "group_agent", name=agent_id or "group_agent")
        return create_agent_session(
            session_id=self._session_id,
            envs=self.get_envs(),
            card=card,
            stream_writer_manager=self._inner.stream_writer_manager(),
            close_stream_on_post_run=False,
            source_metadata={
                "source_agent_id": card.id,
                "source_group_id": self._group_id,
            },
        )

    def _tag_stream_payload(self, data: dict | OutputSchema):
        metadata = {"source_group_id": self._group_id}
        if isinstance(data, dict):
            return {**data, **metadata}
        if isinstance(data, OutputSchema):
            payload = data.payload
            if isinstance(payload, dict):
                payload = {**payload, **metadata}
            else:
                payload = {
                    "value": payload,
                    **metadata,
                }
            return data.model_copy(update={"payload": payload})
        return data

    @staticmethod
    def _normalize_output_stream(data: dict | OutputSchema) -> OutputSchema:
        if isinstance(data, OutputSchema):
            return data
        if {"type", "index", "payload"}.issubset(data.keys()):
            return OutputSchema.model_validate(data)
        return OutputSchema(type="message", index=0, payload=data)


def create_agent_group_session(session_id: str = None, envs: dict[str, Any] = None,
                               group_id: str = "agent_group") -> Session:
    """Create AgentGroup Session"""
    return Session(session_id=session_id, envs=envs, group_id=group_id)
