# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, Optional

from openjiuwen.core.session.state.base import State, InMemoryStateLike, GLOBAL_STATE_KEY, AGENT_STATE_KEY


class StateCollection(State):
    def __init__(self):
        self._global_state = InMemoryStateLike()
        self._agent_state = InMemoryStateLike()
        self._trace_state = dict()

    def get(self, key: Union[str, list, dict] = None) -> Optional[dict]:
        if key is None:
            return self._agent_state.get_state()
        return self._agent_state.get(key)

    def update(self, data: dict):
        self._agent_state.update(data)

    def update_trace(self, span):
        pass

    def update_global(self, data: dict):
        self._global_state.update(data)

    def get_global(self, key: Union[str, list, dict]) -> Optional[dict]:
        if key is None:
            return self._global_state.get_state()
        return self._global_state.get(key)

    def get_state(self) -> dict:
        return {
            GLOBAL_STATE_KEY: self._global_state.get_state(),
            AGENT_STATE_KEY: self._agent_state.get_state()
        }

    def set_state(self, state: dict) -> None:
        self._global_state.set_state(state.get(GLOBAL_STATE_KEY))
        self._agent_state.set_state(state.get(AGENT_STATE_KEY))

    @property
    def global_state(self):
        return self._global_state

    def dump(self) -> dict:
        return {
            "global_state": self._global_state.get_state(),
            "agent_state": self._agent_state.get_state(),
            "trace_state": self._trace_state
        }