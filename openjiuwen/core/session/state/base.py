# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import ABC, abstractmethod
from copy import deepcopy
from typing import Any, Union, Optional, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import session_logger, LogEventType
from openjiuwen.core.session.utils import update_dict, get_by_schema


class ReadableStateLike(ABC):
    @abstractmethod
    def get(self, key: Union[str, list, dict]) -> Optional[Any]:
        pass

    @abstractmethod
    def get_by_prefix(self, key: Union[str, list, dict], nested_prefix: str) -> Optional[Any]:
        pass


Transformer = Callable[[ReadableStateLike], Any]


class RecoverableStateLike(ABC):

    @abstractmethod
    def get_state(self) -> dict:
        pass

    @abstractmethod
    def set_state(self, state: dict) -> None:
        pass


class StateLike(ReadableStateLike, RecoverableStateLike):
    @abstractmethod
    def update(self, data: dict) -> None:
        pass

    @abstractmethod
    def get_by_transformer(self, transformer: Transformer) -> Optional[Any]:
        pass


class CommitStateLike(StateLike):
    @abstractmethod
    def update_by_id(self, node_id: str, data: dict) -> None:
        pass

    @abstractmethod
    def commit(self, node_id: str = None) -> None:
        pass

    @abstractmethod
    def rollback(self, node_id: str) -> None:
        pass

    @abstractmethod
    def get_updates(self) -> dict:
        pass

    @abstractmethod
    def set_updates(self, updates: dict):
        pass


IO_STATE_KEY = "io_state"
IO_STATE_UPDATES_KEY = "io_state_updates"
GLOBAL_STATE_KEY = "global_state"
GLOBAL_STATE_UPDATES_KEY = "global_state_updates"
COMP_STATE_KEY = "comp_state"
WORKFLOW_STATE_KEY = "workflow_state"
AGENT_STATE_KEY = "agent_state"
COMP_STATE_UPDATES_KEY = "comp_state_updates"
WORKFLOW_STATE_UPDATES_KEY = "workflow_state_updates"
DEFAULT_NODE_ID = "default"
DEFAULT_WORKFLOW_ID = "workflow"


class State(RecoverableStateLike):
    @abstractmethod
    def get_global(self, key: Union[str, list, dict]) -> Optional[Any]:
        pass

    @abstractmethod
    def update_global(self, data: dict):
        pass

    @abstractmethod
    def update_trace(self, span):
        pass

    @abstractmethod
    def update(self, data: dict):
        pass

    @abstractmethod
    def get(self, key: Union[str, list, dict] = None) -> Optional[Any]:
        pass

    @abstractmethod
    def dump(self) -> dict:
        ...


class InMemoryStateLike(StateLike):
    def __init__(self):
        self._state: dict = dict()

    def get(self, key: Union[str, list, dict]) -> Optional[Any]:
        return deepcopy(get_by_schema(key, self._state))

    def get_by_prefix(self, key: Union[str, list, dict], nested_prefix: str) -> Optional[Any]:
        return deepcopy(get_by_schema(key, self._state, nested_prefix))

    def get_by_transformer(self, transformer: Callable) -> Optional[Any]:
        return transformer(self._state)

    def update(self, data: dict) -> None:
        update_dict(deepcopy(data), self._state)

    def get_state(self) -> dict:
        return deepcopy(self._state)

    def set_state(self, state: dict) -> None:
        if state:
            self._state = state


class InMemoryCommitState(CommitStateLike):
    def __init__(self, state: StateLike = None):
        self._state = state if state else InMemoryStateLike()
        self._updates: dict[str, list[dict]] = dict()

    def update(self, data: dict) -> None:
        raise build_error(StatusCode.ERROR, msg="commit state update must support node_id")

    def update_by_id(self, node_id: str, data: dict) -> None:
        if node_id is None:
            raise build_error(StatusCode.ERROR, msg="can not update state by none node_id")
        if node_id not in self._updates:
            self._updates[node_id] = []
        self._updates[node_id].append(deepcopy(data))

    def commit(self, node_id: str = None) -> None:
        if node_id is None:
            for key, updates in self._updates.items():
                for update in updates:
                    self._state.update(update)
            self._updates.clear()
        else:
            node_updates = self._updates.get(node_id)
            if not node_updates:
                return
            for update in node_updates:
                self._state.update(update)
            self._updates[node_id] = []

    def rollback(self, node_id: str) -> None:
        self._updates[node_id] = []

    def get_by_transformer(self, transformer: Transformer) -> Optional[Any]:
        return transformer(self._state)

    def get(self, key: Union[str, list, dict]) -> Optional[Any]:
        return self._state.get(key)

    def get_by_prefix(self, key: Union[str, list, dict], nested_prefix: str) -> Optional[Any]:
        return self._state.get_by_prefix(key, nested_prefix)

    def get_updates(self) -> dict:
        return self._updates

    def set_updates(self, updates: dict):
        if updates:
            self._updates = updates

    def get_state(self) -> dict:
        return self._state.get_state()

    def set_state(self, state: dict) -> None:
        self._state.set_state(state)


