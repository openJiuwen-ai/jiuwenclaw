# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from openjiuwen.core.common.constants.constant import (
    INTERACTION,
    INTERACTIVE_INPUT,
)
from openjiuwen.core.graph.pregel import (
    GraphInterrupt,
    Interrupt,
)
from openjiuwen.core.session.interaction.base import (
    AgentInterrupt,
    BaseInteraction,
)
from openjiuwen.core.session.internal.agent import AgentSession
from openjiuwen.core.session.session import BaseSession
from openjiuwen.core.session.stream.base import OutputSchema


class InteractionOutput(BaseModel):
    id: str
    value: Any


class WorkflowInteraction(BaseInteraction):
    def __init__(self, session: BaseSession):
        self._node_id = session.executable_id()
        workflow_interactive_input = session.state().get_workflow_state(INTERACTIVE_INPUT)
        if workflow_interactive_input is not None:
            session.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: None})
        super().__init__(session, workflow_interactive_input)

    async def wait_user_inputs(self, value: Any) -> Any:
        res = self._get_next_interactive_input()
        if res is not None:
            return res
        self._session.state().commit_cmp()
        payload = InteractionOutput(id=self._node_id, value=value)
        if self._session.stream_writer_manager():
            output_writer = self._session.stream_writer_manager().get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=payload))
        raise GraphInterrupt((Interrupt(
            value=OutputSchema(type=INTERACTION, index=self._idx, payload=payload)),))

    async def user_latest_input(self, value: Any) -> Any:
        if self._latest_interactive_inputs:
            res = self._latest_interactive_inputs
            self._latest_interactive_inputs = None
            return res
        if self._session.stream_writer_manager:
            output_writer = self._session.stream_writer_manager().get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=(self._node_id, value)))

        raise GraphInterrupt((Interrupt(
            value=OutputSchema(type=INTERACTION, index=self._idx, payload=(self._node_id, None)), resumable=True,
            ns=self._node_id),))


class SimpleAgentInteraction:
    def __init__(self, session: AgentSession):
        self._agent_session = session

    async def wait_user_inputs(self, message):
        await self._agent_session.checkpointer().interrupt_agent_execute(self._agent_session)
        raise AgentInterrupt(message)


class AgentInteraction(BaseInteraction):
    def __init__(self, session: AgentSession):
        super().__init__(session)
        self._agent_session = session

    async def wait_user_inputs(self, value):
        inputs = self._get_next_interactive_input()
        if inputs is not None:
            return inputs

        await self._agent_session.checkpointer().interrupt_agent_execute(self._session)
        payload = InteractionOutput(id=self._session.executable_id(), value=value)
        writer_manager = self._session.stream_writer_manager()
        if writer_manager is not None:
            output_writer = writer_manager.get_output_writer()
            await output_writer.write(OutputSchema(type=INTERACTION, index=self._idx, payload=payload))

        raise AgentInterrupt()
