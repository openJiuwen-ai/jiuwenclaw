# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod

from pymilvus.client.utils import is_successful

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.graph.store import (
    Serializer,
    Store,
    create_serializer,
)
from openjiuwen.core.session.checkpointer import Checkpointer
from openjiuwen.core.session.checkpointer.base import Storage
from openjiuwen.core.session.constants import FORCE_DEL_WORKFLOW_STATE_KEY
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.session.session import BaseSession


class InMemoryCheckpointer(Checkpointer):
    def __init__(self):
        self._agent_stores = {}
        self._agent_group_stores = {}
        self._workflow_stores = {}
        from openjiuwen.core.graph import InMemoryStore
        self._graph_store = InMemoryStore()
        self._session_to_workflow_ids = {}

    async def pre_workflow_execute(self, session: BaseSession, inputs: InteractiveInput):
        session_id = session.session_id()
        workflow_id = session.workflow_id()
        is_new_workflow_store = session_id not in self._workflow_stores
        workflow_store = self._workflow_stores.setdefault(session_id, WorkflowStorage())
        log_message = dict(
            session_id=session_id,
            workflow_id=workflow_id,
            metadata={"storage_type": "inmemory"})
        if is_new_workflow_store:
            session_logger.info("Create a new workflow checkpointer store before workflow execute",
                                event_type=LogEventType.CHECKPOINTER_STORE_ADD, **log_message
                                )
        self._session_to_workflow_ids.setdefault(session_id, set())
        if isinstance(inputs, InteractiveInput):
            session_logger.info(
                "Begin to restore workflow session before workflow execute",
                event_type=LogEventType.CHECKPOINT_RESTORE, **log_message
            )
            await workflow_store.recover(session, inputs)
            session_logger.info(
                "Succeed to restore workflow session before workflow execute",
                event_type=LogEventType.CHECKPOINT_RESTORE, **log_message
            )
        else:
            if not await workflow_store.exists(session):
                return
            if session.config().get_env(FORCE_DEL_WORKFLOW_STATE_KEY, False):
                session_logger.info(
                    f"Begin to clear all of current workflow's checkpoints forcefully before workflow execute",
                    event_type=LogEventType.CHECKPOINT_CLEAR, **log_message
                )
                try:
                    await self._graph_store.delete(session_id, workflow_id)
                    session_logger.info(
                        f"Succeed to clear all of current workflow's checkpoints forcefully before workflow execute",
                        event_type=LogEventType.CHECKPOINT_CLEAR, **log_message
                    )
                except Exception as e:
                    raise e
                finally:
                    await workflow_store.clear(workflow_id)
            else:
                raise build_error(StatusCode.CHECKPOINTER_PRE_WORKFLOW_EXECUTION_ERROR, session_id=session_id,
                                  workflow=workflow_id,
                                  reason="workflow state exists but non-interactive input and cleanup is disabled")

    async def post_workflow_execute(self, session: BaseSession, result, exception):
        session_id = session.session_id()
        workflow_id = session.workflow_id()
        workflow_store = self._workflow_stores.get(session_id)
        if exception is not None:
            if workflow_store is None:
                raise build_error(StatusCode.CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR, workflow=workflow_id,
                                  reason="workflow store not found")
            await self._inner_save_workflow_checkpoint(workflow_id, session_id, session,
                                                       f"workflow exception {exception}")
            raise exception
        from openjiuwen.core.graph.pregel import TASK_STATUS_INTERRUPT
        if result.get(TASK_STATUS_INTERRUPT) is None:
            try:
                await self._inner_clear_workflow_session(workflow_id=workflow_id, session_id=session_id,
                                                         reason="workflow execute completion")
            finally:
                from openjiuwen.core.session.internal.agent import AgentSession
                if not isinstance(session.parent(), AgentSession):
                    self._workflow_stores.pop(session_id, None)
                    self._session_to_workflow_ids.pop(session_id, None)
                    session_logger.info(
                        f"Remove workflow checkpoint store on workflow execute completion",
                        event_type=LogEventType.CHECKPOINTER_STORE_REMOVE,
                        session_id=session_id,
                        workflow_id=workflow_id,
                        metadata={"storage_type": "inmemory"}
                    )
        else:
            if workflow_store is None:
                raise build_error(StatusCode.CHECKPOINTER_POST_WORKFLOW_EXECUTION_ERROR, workflow=workflow_id,
                                  reason="workflow store not found")
            await self._inner_save_workflow_checkpoint(workflow_id, session_id, session, "workflow interruption")

    async def _inner_save_workflow_checkpoint(self, workflow_id, session_id, session, reason):
        workflow_store = self._workflow_stores.get(session_id)
        workflow_ids = self._session_to_workflow_ids.get(session_id)
        session_logger.info(
            f"Begin to save workflow checkpoint on {reason}",
            event_type=LogEventType.CHECKPOINT_SAVE,
            session_id=session_id,
            workflow_id=workflow_id,
            metadata={"storage_type": "inmemory"}
        )
        await workflow_store.save(session)
        workflow_ids.add(workflow_id)
        session_logger.info(
            f"Succeed to save workflow checkpoint on {reason}",
            event_type=LogEventType.CHECKPOINT_SAVE,
            session_id=session_id,
            workflow_id=workflow_id,
            metadata={"storage_type": "inmemory"}
        )

    async def _inner_clear_workflow_session(self, workflow_id, session_id, reason):
        workflow_store = self._workflow_stores.get(session_id)
        workflow_ids = self._session_to_workflow_ids.get(session_id)
        log_message = dict(
            event_type=LogEventType.CHECKPOINT_CLEAR,
            session_id=session_id,
            workflow_id=workflow_id,
            metadata={"storage_type": "inmemory"}
        )
        session_logger.info(f"Begin to clear all of current workflow's checkpoints on {reason}", **log_message)
        is_succeed = False
        try:
            await self._graph_store.delete(session_id, workflow_id)
            is_succeed = True
        except Exception as e:
            session_logger.error(f"Failed to clear all of current workflow's checkpoints on {reason}", exception=e,
                                 **log_message)
            raise
        finally:
            if workflow_store is not None:
                workflow_ids.discard(workflow_id)
                try:
                    await workflow_store.clear(workflow_id)
                except Exception as e:
                    if not is_succeed:
                        session_logger.error(f"Failed to clear clear all of current workflow's checkpoints on {reason}",
                                             exception=e, **log_message)
                    raise
            if is_succeed:
                session_logger.info(f"Succeed to clear all of current workflow's checkpoints on {reason}",
                                    **log_message)

    async def pre_agent_execute(self, session: BaseSession, inputs):
        agent_id = session.agent_id() if hasattr(session, "agent_id") else 'Na'
        session_id = session.session_id()
        is_new_agent_store = session_id not in self._agent_stores
        agent_store = self._agent_stores.setdefault(session_id, AgentStorage())
        log_message = dict(
            session_id=session_id,
            agent_id=agent_id,
            metadata={"storage_type": "inmemory"}
        )
        if is_new_agent_store:
            session_logger.info("Create a new agent checkpointer store before agent execute",
                                event_type=LogEventType.CHECKPOINTER_STORE_ADD, **log_message)
        session_logger.info(
            "Begin to restore agent session before agent execute", event_type=LogEventType.CHECKPOINT_RESTORE,
            **log_message
        )
        await agent_store.recover(session)
        session_logger.info(
            "Succeed to restore agent session before agent execute", event_type=LogEventType.CHECKPOINT_RESTORE,
            **log_message
        )
        if inputs is not None:
            session.state().set_state({INTERACTIVE_INPUT: [inputs]})

    async def pre_agent_group_execute(self, session: BaseSession, inputs):
        group_id = session.group_id() if hasattr(session, "group_id") else "Na"
        session_id = session.session_id()
        is_new_group_store = session_id not in self._agent_group_stores
        group_store = self._agent_group_stores.setdefault(session_id, AgentGroupStorage())
        log_message = dict(
            session_id=session_id,
            workflow_id=group_id,
            metadata={"storage_type": "inmemory"}
        )
        if is_new_group_store:
            session_logger.info("Create a new agent group checkpointer store before group execute",
                                event_type=LogEventType.CHECKPOINTER_STORE_ADD, **log_message)
        session_logger.info(
            "Begin to restore agent group session before execute",
            event_type=LogEventType.CHECKPOINT_RESTORE, **log_message
        )
        await group_store.recover(session)
        session_logger.info(
            "Succeed to restore agent group session before execute",
            event_type=LogEventType.CHECKPOINT_RESTORE, **log_message
        )
        if inputs is not None:
            session.state().update_global({INTERACTIVE_INPUT: [inputs]})

    async def interrupt_agent_execute(self, session: BaseSession):
        agent_id = session.agent_id()
        session_id = session.session_id()
        agent_store = self._agent_stores.get(session_id)
        if agent_store is None:
            raise build_error(StatusCode.CHECKPOINTER_INTERRUPT_AGENT_ERROR, agent=agent_id,
                              reason="agent store not found")
        log_message = dict(
            session_id=session_id,
            agent_id=agent_id,
            metadata={"storage_type": "inmemory"}
        )
        session_logger.info(
            "Begin to save agent checkpoint on agent interruption",
            event_type=LogEventType.CHECKPOINT_SAVE, **log_message
        )
        try:
            await agent_store.save(session)
            session_logger.info(
                "Succeed to save agent checkpoint on agent interruption",
                event_type=LogEventType.CHECKPOINT_SAVE, **log_message
            )
        except Exception as e:
            session_logger.error(
                "Failed to save agent checkpoint on agent interruption",
                event_type=LogEventType.CHECKPOINT_SAVE, exception=e, **log_message
            )
            raise

    async def post_agent_execute(self, session: BaseSession):
        agent_id = session.agent_id()
        session_id = session.session_id()
        agent_store = self._agent_stores.get(session_id)
        if agent_store is None:
            raise build_error(StatusCode.CHECKPOINTER_POST_AGENT_EXECUTION_ERROR,
                              agent=agent_id, reason="agent store not found")
        log_message = dict(
            session_id=session_id,
            agent_id=agent_id,
            metadata={"storage_type": "inmemory"}
        )
        session_logger.info(
            "Begin to save agent checkpoint on agent execute completion",
            event_type=LogEventType.CHECKPOINT_SAVE, **log_message
        )
        try:
            await agent_store.save(session)
            session_logger.info(
                "Succeed to save agent checkpoint on agent execute completion",
                event_type=LogEventType.CHECKPOINT_SAVE, **log_message
            )
        except Exception as e:
            session_logger.error(
                "Failed to save agent checkpoint on agent execute completion",
                exception=e,
                event_type=LogEventType.CHECKPOINT_SAVE, **log_message
            )
            raise

    async def post_agent_group_execute(self, session: BaseSession):
        group_id = session.group_id()
        session_id = session.session_id()
        group_store = self._agent_group_stores.get(session_id)
        if group_store is None:
            raise build_error(StatusCode.CHECKPOINTER_POST_AGENT_EXECUTION_ERROR,
                              agent=group_id, reason="agent group store not found")
        log_message = dict(
            session_id=session_id,
            workflow_id=group_id,
            metadata={"storage_type": "inmemory"}
        )
        session_logger.info(
            "Begin to save agent group checkpoint on group execute completion",
            event_type=LogEventType.CHECKPOINT_SAVE, **log_message
        )
        try:
            await group_store.save(session)
            session_logger.info(
                "Succeed to save agent group checkpoint on group execute completion",
                event_type=LogEventType.CHECKPOINT_SAVE, **log_message
            )
        except Exception as e:
            session_logger.error(
                "Failed to save agent group checkpoint on group execute completion",
                exception=e,
                event_type=LogEventType.CHECKPOINT_SAVE, **log_message
            )
            raise

    async def session_exists(self, session_id: str) -> bool:
        return (
            session_id in self._agent_stores
            or session_id in self._agent_group_stores
            or session_id in self._workflow_stores
        )

    async def release(self, session_id: str, agent_id: str = None):
        if agent_id is not None:
            agent_store = self._agent_stores.get(session_id)
            if agent_store is None:
                return
            session_logger.info("Begin to clear all of current agent's checkpoints on on manually release",
                                event_type=LogEventType.CHECKPOINT_CLEAR,
                                agent_id=agent_id, session_id=session_id, metadata={"storage_type": "inmemory"})
            try:
                await agent_store.clear(agent_id)
                session_logger.info("Succeed to clear all of current agent's checkpoints on on manually release",
                                    event_type=LogEventType.CHECKPOINT_CLEAR,
                                    agent_id=agent_id, session_id=session_id, metadata={"storage_type": "inmemory"})
            except Exception as e:
                session_logger.error("Failed to clear all of current agent's checkpoints on on manually release",
                                     agent_id=agent_id,
                                     event_type=LogEventType.CHECKPOINT_CLEAR,
                                     session_id=session_id, exception=e, metadata={"storage_type": "inmemory"})
        else:
            workflow_ids = self._session_to_workflow_ids.get(session_id)
            session_logger.info("Begin to clear all of current agent's workflow checkpoints on on manually release",
                                agent_id=agent_id,
                                event_type=LogEventType.CHECKPOINT_CLEAR,
                                session_id=session_id,
                                workflow_id=str(workflow_ids) if workflow_ids else '[]',
                                metadata={"storage_type": "inmemory"})
            if workflow_ids:
                for workflow_id in workflow_ids:
                    try:
                        await self._graph_store.delete(session_id, workflow_id)
                    except Exception as e:
                        session_logger.warning("Failed to clear workflow checkpoint",
                                               event_type=LogEventType.CHECKPOINT_CLEAR,
                                               e=e, agent_id=agent_id,
                                               session_id=session_id, workflow_id=workflow_id,
                                               metadata={"storage_type": "inmemory"})

            self._session_to_workflow_ids.pop(session_id, None)
            session_logger.info("Succeed to clear all of current agent's workflow checkpoints on on manually release",
                                agent_id=agent_id,
                                event_type=LogEventType.CHECKPOINT_CLEAR,
                                session_id=session_id,
                                workflow_id=str(workflow_ids) if workflow_ids else '[]',
                                metadata={"storage_type": "inmemory"})

            removed = self._workflow_stores.pop(session_id, None)
            if removed:
                session_logger.info(
                    f"Remove workflow checkpoint store on manually release",
                    event_type=LogEventType.CHECKPOINTER_STORE_REMOVE,
                    agent_id=agent_id,
                    session_id=session_id,
                    metadata={"storage_type": "inmemory"}
                )

            removed = self._agent_stores.pop(session_id, None)
            if removed:
                session_logger.info(
                    f"Remove agent checkpoint store on manually release",
                    event_type=LogEventType.CHECKPOINTER_STORE_REMOVE,
                    session_id=session_id,
                    agent_id=agent_id,
                    metadata={"storage_type": "inmemory"}
                )

            removed = self._agent_group_stores.pop(session_id, None)
            if removed:
                session_logger.info(
                    f"Remove agent group checkpoint store on manually release",
                    event_type=LogEventType.CHECKPOINTER_STORE_REMOVE,
                    session_id=session_id,
                    metadata={"storage_type": "inmemory"}
                )

    def graph_store(self) -> Store:
        return self._graph_store


class BaseSingleStateStorage(Storage, ABC):
    def __init__(self):
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}
        self.serde: Serializer = create_serializer("pickle")

    @abstractmethod
    def _get_entity_id(self, session: BaseSession) -> str:
        ...

    @abstractmethod
    def _get_state_to_save(self, session: BaseSession):
        ...

    @abstractmethod
    def _restore_state(self, session: BaseSession, state) -> None:
        ...

    async def save(self, session: BaseSession):
        entity_id = self._get_entity_id(session)
        state = self._get_state_to_save(session)
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[entity_id] = state_blob

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        entity_id = self._get_entity_id(session)
        state_blob = self.state_blobs.get(entity_id)
        if state_blob is None:
            return
        state = self.serde.loads_typed(state_blob)
        self._restore_state(session, state)

    async def clear(self, entity_id: str):
        self.state_blobs.pop(entity_id, None)

    async def exists(self, session: BaseSession) -> bool:
        return self.state_blobs.get(self._get_entity_id(session)) is not None


class AgentStorage(BaseSingleStateStorage):
    def _get_entity_id(self, session: BaseSession) -> str:
        return session.agent_id()

    def _get_state_to_save(self, session: BaseSession):
        return session.state().get_state()

    def _restore_state(self, session: BaseSession, state) -> None:
        session.state().set_state(state)


class WorkflowStorage(Storage):
    def __init__(self):
        self.serde: Serializer = create_serializer("pickle")
        self.state_blobs: dict[
            str,
            tuple[str, bytes],
        ] = {}

        self.state_updates_blobs: dict[
            str,
            tuple[str, bytes]
        ] = {}

    async def save(self, session: BaseSession):
        workflow_id = session.workflow_id()
        state = session.state().get_state()
        state_blob = self.serde.dumps_typed(state)
        if state_blob:
            self.state_blobs[workflow_id] = state_blob

        updates = session.state().get_updates()
        updates_blob = self.serde.dumps_typed(updates)
        if updates_blob:
            self.state_updates_blobs[workflow_id] = updates_blob

    async def recover(self, session: BaseSession, inputs: InteractiveInput = None):
        workflow_id = session.workflow_id()
        state_blob = self.state_blobs.get(workflow_id)
        if state_blob and state_blob[0] != "empty":
            state = self.serde.loads_typed(state_blob)
            session.state().set_state(state)

        if inputs.raw_inputs is not None:
            session.state().update_and_commit_workflow_state({INTERACTIVE_INPUT: inputs.raw_inputs})
        else:
            from openjiuwen.core.session.internal.workflow import NodeSession

            for node_id, value in inputs.user_inputs.items():
                node_session = NodeSession(session, node_id)
                interactive_input = node_session.state().get(INTERACTIVE_INPUT)
                if isinstance(interactive_input, list):
                    interactive_input.append(value)
                    node_session.state().update({INTERACTIVE_INPUT: interactive_input})
                else:
                    node_session.state().update({INTERACTIVE_INPUT: [value]})
            session.state().commit()

        state_updates_blob = self.state_updates_blobs.get(workflow_id)
        if state_updates_blob:
            state_updates = self.serde.loads_typed(state_updates_blob)
            session.state().set_updates(state_updates)

    async def clear(self, workflow_id: str):
        self.state_blobs.pop(workflow_id, None)
        self.state_updates_blobs.pop(workflow_id, None)

    async def exists(self, session: BaseSession) -> bool:
        state_blob = self.state_blobs.get(session.workflow_id())
        if state_blob and state_blob[0] != "empty":
            return True
        return False


class AgentGroupStorage(BaseSingleStateStorage):
    def _get_entity_id(self, session: BaseSession) -> str:
        return session.group_id()

    def _get_state_to_save(self, session: BaseSession):
        return session.state().get_global(None)

    def _restore_state(self, session: BaseSession, state) -> None:
        session.state().global_state.set_state(state)
