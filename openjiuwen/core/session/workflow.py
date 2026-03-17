# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import Any, TYPE_CHECKING

from openjiuwen.core.session import BaseSession

if TYPE_CHECKING:
    from openjiuwen.core.session.agent import Session as AgentSession


class Session:
    """
    Session is the main class for managing the workflow of a session.
    """

    def __init__(self, parent: BaseSession = None, session_id: str = None, envs: dict[str, Any] = None):
        self._envs = envs
        self._parent = parent
        if parent is not None:
            self._session_id = session_id
            self._envs = parent.config().get_envs()
        elif session_id is not None:
            self._session_id = session_id
        else:
            self._session_id = str(uuid.uuid4())
        self._workflow_card = None

    def get_session_id(self) -> str:
        return self._session_id

    def get_envs(self):
        return self._envs

    def get_parent(self):
        return self._parent

    def set_workflow_card(self, card):
        self._workflow_card = card

    def get_workflow_card(self):
        return self._workflow_card


def create_workflow_session(parent: "AgentSession" = None, session_id: str = None,
                            envs: dict[str, Any] = None) -> Session:
    return Session(parent=parent, session_id=session_id, envs=envs)

