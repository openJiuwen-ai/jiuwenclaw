# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from typing import Any

from openjiuwen.core.common.constants.constant import INTERACTIVE_INPUT
from openjiuwen.core.session.session import BaseSession


class BaseInteraction(ABC, metaclass=ABCMeta):
    def __init__(self, session: BaseSession, default_input=None):
        if default_input is not None:
            self._interactive_inputs = [default_input]
        else:
            self._interactive_inputs = None
        self._latest_interactive_inputs = None
        self._idx = 0
        self._session = session
        self._init_interactive_inputs()

    def _init_interactive_inputs(self):
        interactive_inputs = self._session.state().get(INTERACTIVE_INPUT)
        if isinstance(interactive_inputs, list):
            if self._interactive_inputs:
                self._interactive_inputs = interactive_inputs + self._interactive_inputs
            else:
                self._interactive_inputs = interactive_inputs
        if self._interactive_inputs:
            self._session.state().update({INTERACTIVE_INPUT: self._interactive_inputs})
            self._latest_interactive_inputs = self._interactive_inputs[-1]

    def _get_next_interactive_input(self) -> Any | None:
        if self._interactive_inputs and self._idx < len(self._interactive_inputs):
            res = self._interactive_inputs[self._idx]
            self._idx += 1
            return res
        return None

    @abstractmethod
    async def wait_user_inputs(self, value):
        pass

    async def user_latest_input(self, value):
        pass


class AgentInterrupt(Exception):
    def __init__(self, message):
        super().__init__()
        self.message = message
