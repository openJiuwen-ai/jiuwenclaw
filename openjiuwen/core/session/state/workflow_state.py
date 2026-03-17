# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, Optional, Any, Callable, Self

from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.session.state.base import State, CommitStateLike, DEFAULT_NODE_ID, DEFAULT_WORKFLOW_ID, \
    IO_STATE_KEY, GLOBAL_STATE_KEY, COMP_STATE_KEY, WORKFLOW_STATE_KEY, IO_STATE_UPDATES_KEY, \
    GLOBAL_STATE_UPDATES_KEY, COMP_STATE_UPDATES_KEY, WORKFLOW_STATE_UPDATES_KEY, InMemoryCommitState


class StateCollection(State):

    def __init__(
            self,
            io_state: CommitStateLike,
            global_state: CommitStateLike,
            comp_state: CommitStateLike,
            workflow_state: CommitStateLike,
            trace_state: dict = None,
            parent_id: str = "",
            node_id: str = DEFAULT_NODE_ID,
    ):
        self._io_state = io_state
        self._global_state = global_state
        trace_state = trace_state or {}
        self._trace_state = trace_state
        self._comp_state = comp_state
        self._workflow_state = workflow_state
        self._parent_id = parent_id
        self._node_id = node_id

    def get_global(self, key: Union[str, list, dict]) -> Optional[Any]:
        if self._global_state is None or key is None:
            return None
        result = self._global_state.get(key)
        if result is None:
            result = self._io_state.get_by_prefix(key, self._parent_id)
        if result is None:
            result = self._io_state.get_by_prefix(key, self._node_id)
        return result

    def update_global(self, data: dict) -> None:
        if self._global_state is None or data is None:
            return
        self._global_state.update_by_id(self._node_id, data)

    def update_trace(self, span):
        self._trace_state.update({self._node_id: span})

    def update(self, data: dict) -> None:
        if self._comp_state is None:
            return
        self._comp_state.update_by_id(self._node_id, {self._node_id: data})

    def get(self, key: Union[str, list, dict] = None) -> Optional[Any]:
        if self._comp_state is None:
            return None
        if key is None:
            return self._comp_state.get(self._node_id)
        result = self._comp_state.get_by_prefix(key, self._node_id)
        return result

    def dump(self) -> dict:
        return {
            "io_state": self._io_state.get_state(),
            "io_state_updates": self._io_state.get_updates(),
            "global_state": self._global_state.get_state(),
            "global_state_updates": self._global_state.get_updates(),
            "comp_state": self._comp_state.get_state(),
            "comp_state_updates": self._comp_state.get_updates(),
            "workflow_state": self._workflow_state.get_state(),
            "workflow_state_updates": self._workflow_state.get_updates(),
            "trace_state": self._trace_state
        }

    def commit_cmp(self):
        self._comp_state.commit(self._node_id)
        self._io_state.commit(self._node_id)


class CommitState(StateCollection):

    def __init__(
            self,
            io_state: CommitStateLike,
            global_state: CommitStateLike,
            comp_state: CommitStateLike,
            workflow_state: CommitStateLike,
            trace_state: dict = None,
            parent_id: str = "",
            node_id: str = DEFAULT_NODE_ID,
            workflow_only=True,
    ):
        trace_state = trace_state or {}
        super().__init__(io_state=io_state, global_state=global_state, comp_state=comp_state, trace_state=trace_state,
                         workflow_state=workflow_state, parent_id=parent_id, node_id=node_id)
        self._workflow_only = workflow_only

    def get_workflow_state(self, key: Union[str, list, dict]) -> Optional[Any]:
        if self._workflow_state is None or key is None:
            return None
        return self._workflow_state.get(key)

    def update_and_commit_workflow_state(self, data: dict):
        self._workflow_state.update_by_id(DEFAULT_WORKFLOW_ID, data)
        self._workflow_state.commit()

    def set_outputs(self, data: dict) -> None:
        if self._io_state is None or data is None:
            return
        self._io_state.update_by_id(self._node_id, {self._node_id: data})

    def get_inputs(self, schema: Union[str, list, dict] = None) -> Optional[Any]:
        if self._io_state is None:
            return None
        if schema is None:
            return self._io_state.get(self._node_id)
        result = self._io_state.get_by_prefix(schema, self._parent_id)
        return result

    def get_outputs(self, node_id=None) -> Optional[Any]:
        if self._io_state is None:
            return None
        return self._io_state.get_by_prefix(node_id if node_id else self._node_id, self._parent_id)

    def get_inputs_by_transformer(self, transformer: Callable) -> dict:
        if self._io_state is None:
            return {}
        return self._io_state.get_by_transformer(transformer)

    def commit_user_inputs(self, inputs: Any) -> None:
        if self._io_state is None or inputs is None:
            return
        self._io_state.update_by_id(self._node_id,
                                    {self._node_id: inputs} if self._node_id != DEFAULT_NODE_ID else inputs)
        self._global_state.update_by_id(self._node_id, inputs)
        self.commit()

    def commit(self) -> None:
        self._io_state.commit()
        self._comp_state.commit()
        self._global_state.commit()
        self._workflow_state.commit()

    def rollback(self) -> None:
        self._comp_state.rollback(self._node_id)
        self._io_state.rollback(self._node_id)
        self._global_state.rollback(self._node_id)
        self._workflow_state.rollback(self._node_id)

    def get_state(self) -> dict:
        return {
            IO_STATE_KEY: self._io_state.get_state(),
            GLOBAL_STATE_KEY: self._global_state.get_state() if self._workflow_only else None,
            COMP_STATE_KEY: self._comp_state.get_state(),
            WORKFLOW_STATE_KEY: self._workflow_state.get_state()
        }

    def set_state(self, state: dict) -> None:
        global_state = state.get(GLOBAL_STATE_KEY)
        if global_state is not None:
            self._global_state.set_state(global_state)
        self._io_state.set_state(state.get(IO_STATE_KEY))
        self._comp_state.set_state(state.get(COMP_STATE_KEY))
        self._workflow_state.set_state(state.get(WORKFLOW_STATE_KEY))

    def get_updates(self) -> dict:
        return {
            IO_STATE_UPDATES_KEY: self._io_state.get_updates(),
            GLOBAL_STATE_UPDATES_KEY: self._global_state.get_updates() if self._workflow_only else None,
            COMP_STATE_UPDATES_KEY: self._comp_state.get_updates(),
            WORKFLOW_STATE_UPDATES_KEY: self._workflow_state.get_updates()
        }

    def set_updates(self, updates: dict) -> None:
        global_updates = updates.get(GLOBAL_STATE_UPDATES_KEY)
        if global_updates is not None:
            self._global_state.set_updates(global_updates)
        self._io_state.set_updates(updates.get(IO_STATE_UPDATES_KEY))
        self._comp_state.set_updates(updates.get(COMP_STATE_UPDATES_KEY))
        self._workflow_state.set_updates(updates.get(WORKFLOW_STATE_UPDATES_KEY))

    def create_node_state(self, node_id: str, parent_id: str) -> Self:
        return CommitState(io_state=self._io_state, global_state=self._global_state,
                           comp_state=self._comp_state, workflow_state=self._workflow_state,
                           trace_state=self._trace_state, node_id=node_id, parent_id=parent_id)


class InMemoryState(CommitState):
    def __init__(self, global_state: CommitStateLike = None):
        super().__init__(io_state=InMemoryCommitState(),
                         global_state=global_state if global_state is not None else InMemoryCommitState(),
                         workflow_only=global_state is None,
                         workflow_state=InMemoryCommitState(),
                         trace_state=dict(),
                         comp_state=InMemoryCommitState())
